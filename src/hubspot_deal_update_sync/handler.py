"""
Lambda handler: HubSpot Deal Update Sync

Triggered by HubSpot deal.propertyChange webhook. When a deal property is updated
in HubSpot, this handler syncs relevant changes back to AWS Partner Central via
the UpdateOpportunity API.

This enables true bidirectional sync:
- Sales rep updates deal stage in HubSpot → Partner Central opportunity stage updated
- Sales rep updates close date → Partner Central target close date updated
- Sales rep updates amount → Partner Central expected spend updated

Properties synced:
- dealstage → LifeCycle.Stage
- closedate → LifeCycle.TargetCloseDate
- amount → Project.ExpectedCustomerSpend
- description → Project.CustomerBusinessProblem
- dealname → Project.Title
"""

import json
import logging
import os
import sys
from datetime import datetime

sys.path.insert(0, "/var/task")

from common.aws_client import get_partner_central_client, PARTNER_CENTRAL_CATALOG
from common.hubspot_client import HubSpotClient
from common.mappers import hubspot_deal_to_partner_central_updates

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Properties that trigger Partner Central sync
SYNCED_PROPERTIES = {
    "dealstage",
    "closedate", 
    "amount",
    "description",
    "dealname",
    "hs_deal_description",
    "deal_currency_code"
}


def lambda_handler(event: dict, context) -> dict:
    """
    Process HubSpot deal.propertyChange webhook.
    
    Event structure from HubSpot:
    {
        "objectId": 12345,
        "propertyName": "dealstage",
        "propertyValue": "presentationscheduled",
        "subscriptionType": "deal.propertyChange",
        "occurredAt": 1708257600000
    }
    """
    logger.info("Received deal update event: %s", json.dumps(event, default=str))
    
    try:
        # Verify webhook signature if secret is configured
        if "headers" in event:
            _verify_webhook_signature(event)
        
        # Parse webhook payload
        if "body" in event:
            body = json.loads(event["body"]) if isinstance(event["body"], str) else event["body"]
        else:
            body = event
        
        # Extract event details
        deal_id = str(body.get("objectId"))
        property_name = body.get("propertyName")
        property_value = body.get("propertyValue")
        
        logger.info(
            "Deal %s property changed: %s = %s", 
            deal_id, property_name, property_value
        )
        
        # Only sync if this property should trigger Partner Central update
        if property_name not in SYNCED_PROPERTIES:
            logger.info("Property %s not in sync list, skipping", property_name)
            return _success_response("Property not synced")
        
        # Get full deal to check if it has a Partner Central opportunity
        hubspot = HubSpotClient()
        deal = hubspot.get_deal(deal_id)
        
        aws_opportunity_id = deal.get("properties", {}).get("aws_opportunity_id")
        if not aws_opportunity_id:
            logger.info("Deal %s has no aws_opportunity_id, skipping sync", deal_id)
            return _success_response("No Partner Central opportunity linked")
        
        # Get deal with associations for full mapping
        deal, company, contacts = hubspot.get_deal_with_associations(deal_id)
        
        # Map HubSpot deal updates to Partner Central UpdateOpportunity request
        update_payload = hubspot_deal_to_partner_central_updates(
            deal, company, contacts, property_name, property_value
        )
        
        if not update_payload:
            logger.info("No Partner Central updates needed for property %s", property_name)
            return _success_response("No updates required")
        
        # Update Partner Central opportunity
        pc_client = get_partner_central_client()
        
        logger.info(
            "Updating Partner Central opportunity %s: %s",
            aws_opportunity_id,
            json.dumps(update_payload, default=str)
        )
        
        response = pc_client.update_opportunity(
            Catalog=PARTNER_CENTRAL_CATALOG,
            Identifier=aws_opportunity_id,
            **update_payload
        )
        
        logger.info("Partner Central opportunity updated successfully")
        
        # Add note to HubSpot deal documenting the sync
        note_text = (
            f"🔄 Synced to AWS Partner Central\n\n"
            f"Property: {property_name}\n"
            f"Value: {property_value}\n"
            f"Opportunity ID: {aws_opportunity_id}\n"
            f"Timestamp: {datetime.utcnow().isoformat()}Z"
        )
        hubspot.add_note_to_deal(deal_id, note_text)
        
        # Update sync status
        hubspot.update_deal(deal_id, {
            "aws_sync_status": "synced",
            "aws_last_sync_date": datetime.utcnow().isoformat()
        })
        
        return _success_response("Opportunity updated in Partner Central")
        
    except Exception as e:
        logger.error("Error syncing deal update: %s", str(e), exc_info=True)
        return _error_response(str(e))


def _verify_webhook_signature(event: dict) -> None:
    """Verify HubSpot webhook signature if secret is configured."""
    webhook_secret = os.environ.get("HUBSPOT_WEBHOOK_SECRET")
    if not webhook_secret:
        return
    
    import hmac
    import hashlib
    
    signature = event.get("headers", {}).get("x-hubspot-signature-v3") or \
                event.get("headers", {}).get("X-HubSpot-Signature-V3")
    
    if not signature:
        logger.warning("No signature provided in webhook")
        return
    
    body = event.get("body", "")
    if isinstance(body, dict):
        body = json.dumps(body)
    
    method = event.get("requestContext", {}).get("http", {}).get("method", "POST")
    source_url = event.get("headers", {}).get("x-hubspot-request-url", "")
    timestamp = event.get("headers", {}).get("x-hubspot-request-timestamp", "")
    
    message = f"{method}{source_url}{body}{timestamp}"
    expected_sig = hmac.new(
        webhook_secret.encode(),
        message.encode(),
        hashlib.sha256
    ).hexdigest()
    
    if not hmac.compare_digest(signature, expected_sig):
        raise ValueError("Invalid webhook signature")


def _success_response(message: str) -> dict:
    """Return API Gateway success response."""
    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"status": "success", "message": message})
    }


def _error_response(error: str) -> dict:
    """Return API Gateway error response."""
    return {
        "statusCode": 500,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"status": "error", "error": error})
    }
