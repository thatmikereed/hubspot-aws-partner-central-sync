"""
Lambda handler for syncing HubSpot company changes to AWS Partner Central.

When a company property changes in HubSpot, this handler:
1. Finds all deals associated with the company
2. For each deal with an AWS opportunity, updates the Partner Central opportunity
3. Adds a note to the deal documenting the sync
4. Updates the aws_contact_company_last_sync property

Trigger: HubSpot webhook (company.propertyChange)
"""

import json
import logging
import os
from typing import Optional

logger = logging.getLogger()
logger.setLevel(os.getenv("LOG_LEVEL", "INFO"))

# Import common modules
from common.hubspot_client import HubSpotClient
from common.aws_client import get_partner_central_client


# Industry mapping from HubSpot to Partner Central
HUBSPOT_INDUSTRY_TO_PC = {
    "AEROSPACE": "Aerospace",
    "AGRICULTURE": "Agriculture",
    "AUTOMOTIVE": "Automotive",
    "BANKING": "Financial Services",
    "BIOTECHNOLOGY": "Life Sciences",
    "CHEMICALS": "Manufacturing",
    "COMMUNICATIONS": "Telecommunications",
    "COMPUTER_HARDWARE": "Computers and Electronics",
    "COMPUTER_SOFTWARE": "Software and Internet",
    "CONSTRUCTION": "Real Estate and Construction",
    "CONSULTING": "Professional Services",
    "CONSUMER_GOODS": "Consumer Goods",
    "EDUCATION": "Education",
    "ELECTRONICS": "Computers and Electronics",
    "ENERGY": "Energy - Power and Utilities",
    "ENTERTAINMENT": "Media and Entertainment",
    "FINANCE": "Financial Services",
    "FINANCIAL_SERVICES": "Financial Services",
    "FOOD_BEVERAGE": "Consumer Goods",
    "GAMING": "Gaming",
    "GOVERNMENT": "Government",
    "HEALTHCARE": "Healthcare",
    "HOSPITALITY": "Hospitality",
    "INSURANCE": "Financial Services",
    "LEGAL": "Professional Services",
    "LIFE_SCIENCES": "Life Sciences",
    "LOGISTICS": "Transportation and Logistics",
    "MANUFACTURING": "Manufacturing",
    "MEDIA": "Media and Entertainment",
    "MINING": "Mining",
    "NONPROFIT": "Non-Profit Organization",
    "PHARMACEUTICALS": "Life Sciences",
    "REAL_ESTATE": "Real Estate and Construction",
    "RETAIL": "Retail",
    "SOFTWARE": "Software and Internet",
    "TELECOMMUNICATIONS": "Telecommunications",
    "TRANSPORTATION": "Transportation and Logistics",
    "TRAVEL": "Travel",
    "WHOLESALE": "Wholesale and Distribution",
}


def lambda_handler(event: dict, context: dict) -> dict:
    """
    Handle HubSpot company property change webhook.
    
    Args:
        event: API Gateway event with webhook payload
        context: Lambda context
        
    Returns:
        HTTP response with status and details
    """
    try:
        # Parse webhook payload
        body = json.loads(event.get("body", "{}"))
        logger.info(f"Received company webhook: {json.dumps(body)}")
        
        # Extract company ID and changed property
        company_id = body.get("objectId")
        property_name = body.get("propertyName")
        property_value = body.get("propertyValue")
        
        if not company_id:
            logger.error("No company ID in webhook payload")
            return {
                "statusCode": 400,
                "body": json.dumps({"error": "Missing company ID"})
            }
        
        logger.info(f"Company {company_id} property '{property_name}' changed to '{property_value}'")
        
        # Initialize clients
        hubspot_client = HubSpotClient()
        pc_client = get_partner_central_client()
        
        # Get full company details
        company = hubspot_client.get_company(company_id)
        if not company:
            logger.error(f"Company {company_id} not found")
            return {
                "statusCode": 404,
                "body": json.dumps({"error": "Company not found"})
            }
        
        # Find all deals associated with this company
        associated_deals = hubspot_client.get_company_associations(
            company_id, "deals"
        )
        
        if not associated_deals:
            logger.info(f"No deals associated with company {company_id}")
            return {
                "statusCode": 200,
                "body": json.dumps({
                    "message": "No deals to sync",
                    "companyId": company_id
                })
            }
        
        logger.info(f"Found {len(associated_deals)} associated deals")
        
        # Sync each deal's opportunity
        synced_count = 0
        skipped_count = 0
        errors = []
        
        for deal_id in associated_deals:
            try:
                # Get deal details
                deal = hubspot_client.get_deal(deal_id)
                if not deal:
                    logger.warning(f"Deal {deal_id} not found, skipping")
                    skipped_count += 1
                    continue
                
                # Check if deal has an AWS opportunity
                properties = deal.get("properties", {})
                opportunity_id = properties.get("aws_opportunity_id")
                
                if not opportunity_id:
                    logger.debug(f"Deal {deal_id} has no AWS opportunity, skipping")
                    skipped_count += 1
                    continue
                
                # Get current opportunity from Partner Central
                try:
                    current_opportunity = pc_client.get_opportunity(
                        Catalog="AWS",
                        Identifier=opportunity_id
                    )
                except Exception as e:
                    logger.error(f"Failed to get opportunity {opportunity_id}: {e}")
                    errors.append(f"Deal {deal_id}: {str(e)}")
                    skipped_count += 1
                    continue
                
                # Build updated customer account information
                company_props = company.get("properties", {})
                customer_account = _map_company_to_partner_central_account(company_props)
                
                # Build customer object
                customer = {
                    "Account": customer_account
                }
                
                # Preserve existing contacts
                existing_customer = current_opportunity.get("Customer", {})
                if "Contacts" in existing_customer:
                    customer["Contacts"] = existing_customer["Contacts"]
                
                # Update the opportunity
                update_payload = {
                    "Catalog": "AWS",
                    "Identifier": opportunity_id,
                    "Customer": customer,
                    "LifeCycle": current_opportunity.get("LifeCycle", {}),
                    "Project": current_opportunity.get("Project", {}),
                }
                
                # Remove Title from Project (immutable)
                if "Title" in update_payload["Project"]:
                    del update_payload["Project"]["Title"]
                
                logger.info(f"Updating opportunity {opportunity_id} with new company info")
                pc_client.update_opportunity(**update_payload)
                
                # Add note to HubSpot deal
                note_text = f"""🔄 Company Information Synced to AWS Partner Central

Company: {company_props.get('name', 'Unknown')}
Property changed: {property_name}
New value: {property_value}

Company information for this opportunity has been updated in AWS Partner Central."""
                
                hubspot_client.create_deal_note(deal_id, note_text)
                
                # Update sync timestamp
                hubspot_client.update_deal(deal_id, {
                    "aws_contact_company_last_sync": hubspot_client.now_timestamp_ms()
                })
                
                synced_count += 1
                logger.info(f"Successfully synced company to opportunity {opportunity_id}")
                
            except Exception as e:
                logger.error(f"Error syncing deal {deal_id}: {e}", exc_info=True)
                errors.append(f"Deal {deal_id}: {str(e)}")
        
        # Return summary
        result = {
            "companyId": company_id,
            "propertyChanged": property_name,
            "dealsFound": len(associated_deals),
            "dealsSynced": synced_count,
            "dealsSkipped": skipped_count,
            "errors": errors
        }
        
        logger.info(f"Company sync complete: {json.dumps(result)}")
        
        return {
            "statusCode": 200,
            "body": json.dumps(result)
        }
        
    except Exception as e:
        logger.error(f"Fatal error in company sync: {e}", exc_info=True)
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e)})
        }


def _map_company_to_partner_central_account(company_props: dict) -> dict:
    """
    Map HubSpot company properties to Partner Central Account format.
    
    Args:
        company_props: HubSpot company properties dict
        
    Returns:
        Partner Central Account dict
    """
    # Company name (required)
    company_name = company_props.get("name", "Unknown Company")[:120]
    
    # Industry mapping
    raw_industry = company_props.get("industry", "").upper().replace(" ", "_")
    industry = HUBSPOT_INDUSTRY_TO_PC.get(raw_industry, "Other")
    
    # Website URL
    website = company_props.get("website", "").strip()
    if website and not website.startswith("http"):
        website = "https://" + website
    website = website[:255] if website else None
    
    # Address components
    street = company_props.get("address", "").strip()[:255]
    city = company_props.get("city", "").strip()[:50]
    state = company_props.get("state", "").strip()[:50]
    zip_code = company_props.get("zip", "").strip()[:10]
    country = company_props.get("country", "").strip()[:2].upper()
    
    # Default to US if no country
    if not country:
        country = "US"
    
    # Build account dict
    account = {
        "CompanyName": company_name,
        "Industry": industry,
        "Address": {
            "CountryCode": country
        }
    }
    
    # Add optional fields
    if website:
        account["WebsiteUrl"] = website
    if city:
        account["Address"]["City"] = city
    if state:
        account["Address"]["StateOrRegion"] = state
    if zip_code:
        account["Address"]["PostalCode"] = zip_code
    if street:
        account["Address"]["StreetAddress"] = street
    
    return account
