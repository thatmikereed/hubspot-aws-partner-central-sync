"""
Lambda handler for syncing HubSpot contact changes to AWS Partner Central.

When a contact property changes in HubSpot, this handler:
1. Finds all deals associated with the contact
2. For each deal with an AWS opportunity, updates the Partner Central opportunity
3. Adds a note to the deal documenting the sync
4. Updates the aws_contact_company_last_sync property

Trigger: HubSpot webhook (contact.propertyChange)
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


def lambda_handler(event: dict, context: dict) -> dict:
    """
    Handle HubSpot contact property change webhook.
    
    Args:
        event: API Gateway event with webhook payload
        context: Lambda context
        
    Returns:
        HTTP response with status and details
    """
    try:
        # Parse webhook payload
        body = json.loads(event.get("body", "{}"))
        logger.info(f"Received contact webhook: {json.dumps(body)}")
        
        # Extract contact ID and changed property
        contact_id = body.get("objectId")
        property_name = body.get("propertyName")
        property_value = body.get("propertyValue")
        
        if not contact_id:
            logger.error("No contact ID in webhook payload")
            return {
                "statusCode": 400,
                "body": json.dumps({"error": "Missing contact ID"})
            }
        
        logger.info(f"Contact {contact_id} property '{property_name}' changed to '{property_value}'")
        
        # Initialize clients
        hubspot_client = HubSpotClient()
        pc_client = get_partner_central_client()
        
        # Get full contact details
        contact = hubspot_client.get_contact(contact_id)
        if not contact:
            logger.error(f"Contact {contact_id} not found")
            return {
                "statusCode": 404,
                "body": json.dumps({"error": "Contact not found"})
            }
        
        # Find all deals associated with this contact
        associated_deals = hubspot_client.get_contact_associations(
            contact_id, "deals"
        )
        
        if not associated_deals:
            logger.info(f"No deals associated with contact {contact_id}")
            return {
                "statusCode": 200,
                "body": json.dumps({
                    "message": "No deals to sync",
                    "contactId": contact_id
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
                
                # Get all contacts for this deal
                contact_ids = hubspot_client.get_deal_associations(deal_id, "contacts")
                all_contacts = []
                for cid in contact_ids[:10]:  # Max 10 contacts per API limit
                    c = hubspot_client.get_contact(cid)
                    if c:
                        all_contacts.append(c)
                
                # Build updated contacts list for Partner Central
                pc_contacts = _map_contacts_to_partner_central(all_contacts)
                
                # Update the opportunity
                update_payload = {
                    "Catalog": "AWS",
                    "Identifier": opportunity_id,
                    "Customer": {
                        **current_opportunity.get("Customer", {}),
                        "Contacts": pc_contacts
                    },
                    "LifeCycle": current_opportunity.get("LifeCycle", {}),
                    "Project": current_opportunity.get("Project", {}),
                }
                
                # Remove Title from Project (immutable)
                if "Title" in update_payload["Project"]:
                    del update_payload["Project"]["Title"]
                
                logger.info(f"Updating opportunity {opportunity_id} with new contacts")
                pc_client.update_opportunity(**update_payload)
                
                # Add note to HubSpot deal
                note_text = f"""🔄 Contact Information Synced to AWS Partner Central

Contact: {contact.get('properties', {}).get('firstname', '')} {contact.get('properties', {}).get('lastname', '')}
Property changed: {property_name}
New value: {property_value}

All contact information for this opportunity has been updated in AWS Partner Central."""
                
                hubspot_client.create_deal_note(deal_id, note_text)
                
                # Update sync timestamp
                hubspot_client.update_deal(deal_id, {
                    "aws_contact_company_last_sync": hubspot_client.now_timestamp_ms()
                })
                
                synced_count += 1
                logger.info(f"Successfully synced contact to opportunity {opportunity_id}")
                
            except Exception as e:
                logger.error(f"Error syncing deal {deal_id}: {e}", exc_info=True)
                errors.append(f"Deal {deal_id}: {str(e)}")
        
        # Return summary
        result = {
            "contactId": contact_id,
            "propertyChanged": property_name,
            "dealsFound": len(associated_deals),
            "dealsSynced": synced_count,
            "dealsSkipped": skipped_count,
            "errors": errors
        }
        
        logger.info(f"Contact sync complete: {json.dumps(result)}")
        
        return {
            "statusCode": 200,
            "body": json.dumps(result)
        }
        
    except Exception as e:
        logger.error(f"Fatal error in contact sync: {e}", exc_info=True)
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e)})
        }


def _map_contacts_to_partner_central(contacts: list[dict]) -> list[dict]:
    """
    Map HubSpot contact objects to Partner Central Contact format.
    
    Args:
        contacts: List of HubSpot contact objects
        
    Returns:
        List of Partner Central Contact dicts
    """
    result = []
    
    for contact in contacts[:10]:  # API max: 10 contacts
        properties = contact.get("properties", {})
        
        email = properties.get("email", "").strip()
        first_name = properties.get("firstname", "").strip()[:80]
        last_name = properties.get("lastname", "").strip()[:80]
        phone = _sanitize_phone(
            properties.get("phone") or properties.get("mobilephone")
        )
        title = properties.get("jobtitle", "").strip()[:80]
        
        # Skip if no identifying information
        if not email and not first_name and not last_name:
            continue
        
        pc_contact = {}
        if email:
            pc_contact["Email"] = email[:80]
        if first_name:
            pc_contact["FirstName"] = first_name
        if last_name:
            pc_contact["LastName"] = last_name
        if phone:
            pc_contact["Phone"] = phone
        if title:
            pc_contact["BusinessTitle"] = title
        
        result.append(pc_contact)
    
    return result


def _sanitize_phone(raw: Optional[str]) -> Optional[str]:
    """
    Sanitize phone number to Partner Central format: +[1-9][0-9]{1,14}
    
    Args:
        raw: Raw phone number string
        
    Returns:
        Formatted phone number or None if invalid
    """
    if not raw:
        return None
    
    # Extract digits and + sign
    digits = "".join(c for c in raw if c.isdigit() or c == "+")
    
    # Add US country code if missing
    if not digits.startswith("+"):
        digits = "+1" + digits
    
    # Validate length (2-15 digits after +)
    if len(digits) < 4 or len(digits) > 16:
        return None
    
    return digits
