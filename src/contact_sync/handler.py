"""
Lambda handler: Bidirectional Contact Sync

Currently contacts only flow HubSpot → Partner Central during opportunity creation.
This handler enables reverse sync: Partner Central → HubSpot.

When AWS adds contacts to an opportunity (e.g., AWS seller, customer stakeholders),
those contacts are synced to HubSpot and associated with the deal.

Triggered by:
- EventBridge "Opportunity Updated" events (when contacts change)
- Scheduled sync (hourly)
- Manual API call
"""

import json
import logging
import os
import sys

sys.path.insert(0, "/var/task")

from common.aws_client import get_partner_central_client, PARTNER_CENTRAL_CATALOG
from common.hubspot_client import HubSpotClient

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def lambda_handler(event: dict, context) -> dict:
    """
    Sync Partner Central contacts to HubSpot.
    
    Event types:
    1. EventBridge event (opportunity updated)
    2. Scheduled sync (all opportunities)
    3. Manual API call {"opportunityId": "O123"}
    """
    logger.info("Contact sync triggered: %s", json.dumps(event, default=str))
    
    hubspot = HubSpotClient()
    pc_client = get_partner_central_client()
    
    synced = []
    errors = []
    
    try:
        # Determine opportunity IDs to process
        opportunity_ids = _get_opportunity_ids(event, hubspot)
        
        logger.info("Processing %d opportunities for contact sync", len(opportunity_ids))
        
        for opp_id in opportunity_ids:
            try:
                result = _sync_contacts_for_opportunity(opp_id, hubspot, pc_client)
                if result:
                    synced.append(result)
            except Exception as exc:
                logger.exception("Error syncing contacts for %s: %s", opp_id, exc)
                errors.append({"opportunityId": opp_id, "error": str(exc)})
        
        summary = {
            "opportunitiesProcessed": len(opportunity_ids),
            "contactsSynced": sum(r.get("contactsSynced", 0) for r in synced),
            "errors": len(errors),
            "results": synced,
            "errorDetails": errors,
        }
        
        logger.info("Contact sync complete: %s", json.dumps(summary, default=str))
        return {"statusCode": 200, "body": json.dumps(summary)}
        
    except Exception as exc:
        logger.exception("Fatal error in contact sync: %s", exc)
        return {"statusCode": 500, "body": json.dumps({"error": str(exc)})}


def _get_opportunity_ids(event: dict, hubspot: HubSpotClient) -> list[str]:
    """Extract opportunity IDs to process from the event."""
    # EventBridge event
    detail = event.get("detail", {})
    if detail.get("opportunity"):
        return [detail["opportunity"]["identifier"]]
    
    # Manual API call
    if "opportunityId" in event:
        return [event["opportunityId"]]
    
    # Scheduled sync - get all opportunities with aws_opportunity_id
    return _list_all_opportunity_ids(hubspot)


def _list_all_opportunity_ids(hubspot: HubSpotClient) -> list[str]:
    """List all HubSpot deals that have Partner Central opportunities."""
    payload = {
        "filterGroups": [{
            "filters": [{
                "propertyName": "aws_opportunity_id",
                "operator": "HAS_PROPERTY"
            }]
        }],
        "properties": ["aws_opportunity_id"],
        "limit": 100,
    }
    
    try:
        response = hubspot.session.post(
            "https://api.hubapi.com/crm/v3/objects/deals/search",
            json=payload
        )
        response.raise_for_status()
        deals = response.json().get("results", [])
        return [d.get("properties", {}).get("aws_opportunity_id") for d in deals if d.get("properties", {}).get("aws_opportunity_id")]
    except Exception as exc:
        logger.warning("Could not list opportunities: %s", exc)
        return []


def _sync_contacts_for_opportunity(
    opportunity_id: str,
    hubspot: HubSpotClient,
    pc_client
) -> dict | None:
    """
    Fetch contacts from Partner Central opportunity and sync to HubSpot.
    """
    logger.info("Syncing contacts for opportunity %s", opportunity_id)
    
    # Fetch PC opportunity
    opportunity = pc_client.get_opportunity(
        Catalog=PARTNER_CENTRAL_CATALOG,
        Identifier=opportunity_id,
    )
    
    # Extract contacts
    customer_contacts = opportunity.get("Customer", {}).get("Contacts", [])
    team_contacts = opportunity.get("OpportunityTeam", [])
    all_pc_contacts = customer_contacts + team_contacts
    
    if not all_pc_contacts:
        logger.info("No contacts to sync for %s", opportunity_id)
        return None
    
    # Find the corresponding HubSpot deal
    deals = hubspot.search_deals_by_aws_opportunity_id(opportunity_id)
    if not deals:
        logger.warning("No HubSpot deal found for opportunity %s", opportunity_id)
        return None
    
    deal_id = deals[0]["id"]
    
    # Sync each contact
    synced_count = 0
    for pc_contact in all_pc_contacts:
        email = pc_contact.get("Email")
        if not email:
            continue
        
        try:
            hs_contact = _create_or_update_hubspot_contact(pc_contact, hubspot)
            _associate_contact_with_deal(hs_contact["id"], deal_id, hubspot)
            synced_count += 1
            logger.info("Synced contact %s to deal %s", email, deal_id)
        except Exception as exc:
            logger.warning("Failed to sync contact %s: %s", email, exc)
    
    return {
        "opportunityId": opportunity_id,
        "dealId": deal_id,
        "contactsSynced": synced_count,
    }


def _create_or_update_hubspot_contact(pc_contact: dict, hubspot: HubSpotClient) -> dict:
    """
    Create or update a HubSpot contact from a Partner Central contact.
    Returns the HubSpot contact object.
    """
    email = pc_contact.get("Email", "")
    first_name = pc_contact.get("FirstName", "")
    last_name = pc_contact.get("LastName", "")
    phone = pc_contact.get("Phone", "")
    title = pc_contact.get("BusinessTitle", "")
    
    properties = {
        "email": email,
        "firstname": first_name,
        "lastname": last_name,
        "jobtitle": title,
    }
    if phone:
        properties["phone"] = phone
    
    # Check if contact exists
    try:
        search_response = hubspot.session.post(
            "https://api.hubapi.com/crm/v3/objects/contacts/search",
            json={
                "filterGroups": [{
                    "filters": [{
                        "propertyName": "email",
                        "operator": "EQ",
                        "value": email
                    }]
                }],
                "limit": 1
            }
        )
        search_response.raise_for_status()
        existing = search_response.json().get("results", [])
        
        if existing:
            # Update existing
            contact_id = existing[0]["id"]
            response = hubspot.session.patch(
                f"https://api.hubapi.com/crm/v3/objects/contacts/{contact_id}",
                json={"properties": properties}
            )
            response.raise_for_status()
            return response.json()
        else:
            # Create new
            response = hubspot.session.post(
                "https://api.hubapi.com/crm/v3/objects/contacts",
                json={"properties": properties}
            )
            response.raise_for_status()
            return response.json()
    
    except Exception as exc:
        logger.warning("Error creating/updating contact %s: %s", email, exc)
        raise


def _associate_contact_with_deal(contact_id: str, deal_id: str, hubspot: HubSpotClient):
    """Associate a HubSpot contact with a deal."""
    try:
        url = f"https://api.hubapi.com/crm/v3/objects/contacts/{contact_id}/associations/deals/{deal_id}/contact_to_deal"
        response = hubspot.session.put(url)
        # 200 = success, 409 = already associated
        if response.status_code not in (200, 204, 409):
            response.raise_for_status()
    except Exception as exc:
        logger.warning("Error associating contact %s with deal %s: %s", contact_id, deal_id, exc)
