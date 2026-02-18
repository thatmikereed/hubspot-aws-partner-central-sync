"""
Lambda handler: HubSpot Webhook → Google Cloud CRM Partners API

Handles HubSpot webhook events for deals tagged with #GCP:

  deal.creation
    Triggered when a new deal is created in HubSpot. If the deal name
    contains #GCP, creates a corresponding Lead and Opportunity in GCP Partners API,
    and writes the GCP Opportunity ID back to HubSpot.

  deal.propertyChange
    Triggered when any tracked deal property changes. If the deal already
    has a gcp_opportunity_id, syncs the update to GCP Partners API.
"""

import json
import logging
import os
import sys

sys.path.insert(0, "/var/task")

from common.gcp_client import get_gcp_partners_client, get_partner_id
from common.hubspot_client import HubSpotClient
from common.gcp_mappers import (
    hubspot_deal_to_gcp_lead,
    hubspot_deal_to_gcp_opportunity,
    hubspot_deal_to_gcp_opportunity_update,
)

logger = logging.getLogger()
logger.setLevel(logging.INFO)

GCP_TRIGGER_TAG = "#GCP"


def lambda_handler(event: dict, context) -> dict:
    logger.info("Received event: %s", json.dumps(event, default=str))

    try:
        body = event.get("body", "")
        if event.get("isBase64Encoded"):
            import base64
            body = base64.b64decode(body).decode("utf-8")
        webhook_events = json.loads(body) if isinstance(body, str) else body
    except (json.JSONDecodeError, Exception) as exc:
        logger.error("Failed to parse webhook body: %s", exc)
        return _response(400, {"error": "Invalid JSON payload"})

    _verify_signature(event)

    processed = []
    errors = []

    hubspot = HubSpotClient()
    gcp_client = get_gcp_partners_client()
    partner_id = get_partner_id()

    for webhook_event in webhook_events:
        event_type = webhook_event.get("subscriptionType", "")
        object_id = str(webhook_event.get("objectId", ""))

        try:
            if "deal.creation" in event_type:
                result = _handle_deal_creation(object_id, hubspot, gcp_client, partner_id)
            elif "deal.propertyChange" in event_type:
                result = _handle_deal_update(object_id, webhook_event, hubspot, gcp_client, partner_id)
            else:
                logger.debug("Skipping unhandled event type: %s", event_type)
                result = None

            if result:
                processed.append(result)

        except Exception as exc:
            logger.exception("Error processing %s for deal %s: %s", event_type, object_id, exc)
            errors.append({"dealId": object_id, "eventType": event_type, "error": str(exc)})

    return _response(
        200,
        {
            "processed": len(processed),
            "skipped": len(webhook_events) - len(processed) - len(errors),
            "errors": len(errors),
            "results": processed,
            "errorDetails": errors,
        },
    )


# ---------------------------------------------------------------------------
# deal.creation handler
# ---------------------------------------------------------------------------

def _handle_deal_creation(deal_id: str, hubspot: HubSpotClient, gcp_client, partner_id: str) -> dict | None:
    """
    Fetch the HubSpot deal, check for #GCP tag, create a GCP Lead, 
    then create a GCP Opportunity linked to that lead.
    Writes the Opportunity ID back to HubSpot.
    """
    deal, company, contacts = hubspot.get_deal_with_associations(deal_id)
    deal_name = deal.get("properties", {}).get("dealname", "")

    logger.info("Processing deal creation %s: '%s'", deal_id, deal_name)

    if GCP_TRIGGER_TAG.lower() not in deal_name.lower():
        logger.info("Deal '%s' does not contain %s — skipping", deal_name, GCP_TRIGGER_TAG)
        return None

    # Idempotency: skip if already synced
    existing_gcp_id = deal.get("properties", {}).get("gcp_opportunity_id")
    if existing_gcp_id:
        logger.info("Deal %s already synced to GCP opportunity %s", deal_id, existing_gcp_id)
        return None

    # Step 1: Create Lead in GCP Partners API
    lead_payload = hubspot_deal_to_gcp_lead(deal, company, contacts)
    logger.info("Creating GCP lead for deal %s with payload keys: %s",
                deal_id, list(lead_payload.keys()))

    lead_response = gcp_client.partners().leads().create(
        parent=f"partners/{partner_id}",
        body=lead_payload
    ).execute()

    lead_name = lead_response.get("name", "")
    logger.info("Created GCP lead %s for deal %s", lead_name, deal_id)

    # Step 2: Create Opportunity linked to the Lead
    opportunity_payload = hubspot_deal_to_gcp_opportunity(deal, lead_name, company, contacts)
    logger.info("Creating GCP opportunity for deal %s with payload keys: %s",
                deal_id, list(opportunity_payload.keys()))

    opportunity_response = gcp_client.partners().opportunities().create(
        parent=f"partners/{partner_id}",
        body=opportunity_payload
    ).execute()

    opportunity_name = opportunity_response.get("name", "")
    # Extract ID from name like "partners/12345/opportunities/67890"
    opportunity_id = opportunity_name.split("/")[-1] if "/" in opportunity_name else opportunity_name
    logger.info("Created GCP opportunity %s for deal %s", opportunity_id, deal_id)

    # Write GCP IDs back to HubSpot
    hubspot.update_deal(
        deal_id,
        {
            "gcp_opportunity_id": opportunity_id,
            "gcp_opportunity_name": opportunity_name,
            "gcp_lead_name": lead_name,
            "gcp_sync_status": "synced",
        },
    )

    return {
        "action": "created",
        "hubspotDealId": deal_id,
        "dealName": deal_name,
        "gcpLeadName": lead_name,
        "gcpOpportunityId": opportunity_id,
        "gcpOpportunityName": opportunity_name,
    }


# ---------------------------------------------------------------------------
# deal.propertyChange handler
# ---------------------------------------------------------------------------

def _handle_deal_update(
    deal_id: str, webhook_event: dict, hubspot: HubSpotClient, gcp_client, partner_id: str
) -> dict | None:
    """
    Sync a HubSpot deal property change to GCP Partners API.
    """
    deal, company, contacts = hubspot.get_deal_with_associations(deal_id)
    props = deal.get("properties", {})

    # Only sync deals that are already in GCP
    opportunity_id = props.get("gcp_opportunity_id")
    opportunity_name = props.get("gcp_opportunity_name")
    
    if not opportunity_id or not opportunity_name:
        logger.debug("Deal %s has no gcp_opportunity_id — skipping update", deal_id)
        return None

    # The deal must still have #GCP to stay in sync scope
    deal_name = props.get("dealname", "")
    if GCP_TRIGGER_TAG.lower() not in deal_name.lower():
        logger.info("Deal %s no longer contains %s — skipping update", deal_id, GCP_TRIGGER_TAG)
        return None

    changed_property = webhook_event.get("propertyName", "")
    changed_properties = {changed_property} if changed_property else set()

    # Fetch current GCP opportunity state
    current_gcp_opp = _get_gcp_opportunity(gcp_client, opportunity_name)
    if not current_gcp_opp:
        logger.warning("Could not fetch GCP opportunity %s — skipping update", opportunity_id)
        return None

    # Build the update payload
    update_payload, warnings = hubspot_deal_to_gcp_opportunity_update(
        deal, current_gcp_opp, company, contacts, changed_properties
    )

    # Log warnings
    for warning in warnings:
        logger.warning("Deal %s: %s", deal_id, warning)

    if update_payload is None:
        return {
            "action": "no_update",
            "hubspotDealId": deal_id,
            "gcpOpportunityId": opportunity_id,
            "warnings": warnings,
        }

    # Send the update via PATCH
    gcp_client.partners().opportunities().patch(
        name=opportunity_name,
        body=update_payload
    ).execute()
    
    logger.info("Updated GCP opportunity %s from deal %s", opportunity_id, deal_id)

    hubspot.update_deal(deal_id, {"gcp_sync_status": "synced"})

    return {
        "action": "updated",
        "hubspotDealId": deal_id,
        "gcpOpportunityId": opportunity_id,
        "changedProperty": changed_property,
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_gcp_opportunity(gcp_client, opportunity_name: str) -> dict | None:
    """Fetch a GCP opportunity; return None on failure."""
    try:
        return gcp_client.partners().opportunities().get(
            name=opportunity_name
        ).execute()
    except Exception as exc:
        logger.error("Failed to fetch GCP opportunity %s: %s", opportunity_name, exc)
        return None


def _verify_signature(event: dict) -> None:
    """Verify HubSpot webhook signature if secret is configured."""
    secret = os.environ.get("HUBSPOT_WEBHOOK_SECRET")
    if not secret:
        return
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    signature = headers.get("x-hubspot-signature-v3", "")
    if not signature:
        logger.warning("Missing HubSpot signature header — proceeding without verification")
        return
    body = (event.get("body") or "").encode("utf-8")
    hubspot = HubSpotClient()
    if not hubspot.verify_webhook_signature(body, signature, secret):
        raise ValueError("Invalid HubSpot webhook signature")


def _response(status_code: int, body: dict) -> dict:
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body, default=str),
    }
