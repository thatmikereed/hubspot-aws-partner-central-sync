"""
Company sync processor module.

Extracted business logic for processing HubSpot company changes and syncing to AWS Partner Central.
"""

from logging import Logger
from typing import Any, Dict

from common.events import SyncEvent

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


def process_company_update(
    sync_event: SyncEvent,
    hubspot_client: Any,
    pc_client: Any,
    logger: Logger,
) -> Dict[str, Any]:
    """
    Process HubSpot company property change event.
    
    When a company property changes:
    1. Finds all deals associated with the company
    2. For each deal with an AWS opportunity, updates Partner Central
    3. Adds sync notes to deals
    
    Args:
        sync_event: SyncEvent with company update data
        hubspot_client: HubSpot API client
        pc_client: Partner Central API client
        logger: Logger instance
        
    Returns:
        Processing result dict
    """
    company_id = sync_event.object_id
    property_name = sync_event.properties.get("propertyName")
    property_value = sync_event.properties.get("propertyValue")
    
    logger.info(
        f"Company {company_id} property '{property_name}' changed to '{property_value}'"
    )
    
    # Get full company details
    company = hubspot_client.get_company(company_id)
    if not company:
        return {
            "action": "error",
            "reason": "company_not_found",
            "companyId": company_id,
        }
    
    # Find all deals associated with this company
    associated_deals = hubspot_client.get_company_associations(company_id, "deals")
    
    if not associated_deals:
        logger.info(f"No deals associated with company {company_id}")
        return {
            "action": "skipped",
            "reason": "no_deals",
            "companyId": company_id,
        }
    
    logger.info(f"Found {len(associated_deals)} associated deals")
    
    # Sync each deal's opportunity
    synced_count = 0
    skipped_count = 0
    errors = []
    
    for deal_id in associated_deals:
        try:
            result = _sync_deal(
                deal_id, company, property_name, property_value,
                hubspot_client, pc_client, logger
            )
            if result["synced"]:
                synced_count += 1
            else:
                skipped_count += 1
            if result.get("error"):
                errors.append(result["error"])
        except Exception as e:
            logger.error(f"Error syncing deal {deal_id}: {e}", exc_info=True)
            errors.append(f"Deal {deal_id}: {str(e)}")
    
    return {
        "action": "synced",
        "companyId": company_id,
        "propertyChanged": property_name,
        "dealsFound": len(associated_deals),
        "dealsSynced": synced_count,
        "dealsSkipped": skipped_count,
        "errors": errors,
    }


def _sync_deal(
    deal_id: str,
    company: Dict[str, Any],
    property_name: str,
    property_value: str,
    hubspot_client: Any,
    pc_client: Any,
    logger: Logger,
) -> Dict[str, Any]:
    """
    Sync a single deal's opportunity with company information.
    
    Returns:
        Dict with 'synced' boolean and optional 'error' message
    """
    # Get deal details
    deal = hubspot_client.get_deal(deal_id)
    if not deal:
        logger.warning(f"Deal {deal_id} not found, skipping")
        return {"synced": False}
    
    # Check if deal has an AWS opportunity
    properties = deal.get("properties", {})
    opportunity_id = properties.get("aws_opportunity_id")
    
    if not opportunity_id:
        logger.debug(f"Deal {deal_id} has no AWS opportunity, skipping")
        return {"synced": False}
    
    # Get current opportunity from Partner Central
    try:
        current_opportunity = pc_client.get_opportunity(
            Catalog="AWS", Identifier=opportunity_id
        )
    except Exception as e:
        logger.error(f"Failed to get opportunity {opportunity_id}: {e}")
        return {"synced": False, "error": f"Deal {deal_id}: {str(e)}"}
    
    # Build updated customer account information
    company_props = company.get("properties", {})
    customer_account = _map_company_to_partner_central_account(company_props)
    
    # Build customer object
    customer = {"Account": customer_account}
    
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
    hubspot_client.update_deal(
        deal_id,
        {"aws_contact_company_last_sync": hubspot_client.now_timestamp_ms()},
    )
    
    logger.info(f"Successfully synced company to opportunity {opportunity_id}")
    return {"synced": True}


def _map_company_to_partner_central_account(company_props: Dict[str, Any]) -> Dict[str, Any]:
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
        "Address": {"CountryCode": country},
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
