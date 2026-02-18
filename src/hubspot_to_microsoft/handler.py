"""
Lambda handler: HubSpot Webhook → Microsoft Partner Center

Handles two HubSpot event types:

  deal.creation
    Triggered when a new deal is created in HubSpot. If the deal name
    contains #Microsoft, creates a corresponding referral in Microsoft
    Partner Center and writes the referral ID back to HubSpot.

  deal.propertyChange
    Triggered when any tracked deal property changes. If the deal already
    has a microsoft_referral_id, syncs the update to Microsoft Partner Center.
"""

import json
import logging
import os
import sys

sys.path.insert(0, "/var/task")

from common.microsoft_client import get_microsoft_client
from common.hubspot_client import HubSpotClient
from common.microsoft_mappers import (
    hubspot_deal_to_microsoft_referral,
    hubspot_deal_to_microsoft_referral_update,
)

logger = logging.getLogger()
logger.setLevel(logging.INFO)

MICROSOFT_TRIGGER_TAG = "#Microsoft"


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
    microsoft_client = get_microsoft_client()

    for webhook_event in webhook_events:
        event_type = webhook_event.get("subscriptionType", "")
        object_id = str(webhook_event.get("objectId", ""))

        try:
            if "deal.creation" in event_type:
                result = _handle_deal_creation(object_id, hubspot, microsoft_client)
            elif "deal.propertyChange" in event_type:
                result = _handle_deal_update(object_id, webhook_event, hubspot, microsoft_client)
            else:
                logger.debug("Skipping unhandled event type: %s", event_type)
                result = None

            if result:
                processed.append(result)

        except Exception as exc:
            logger.exception("Error processing %s for deal %s: %s", event_type, object_id, exc)
            errors.append({"dealId": object_id, "eventType": event_type, "error": str(exc)})

    microsoft_client.close()

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

def _handle_deal_creation(deal_id: str, hubspot: HubSpotClient, microsoft_client) -> dict | None:
    """
    Fetch the HubSpot deal, check for #Microsoft tag, and create a Microsoft
    Partner Center referral. On success, writes the referral ID back to HubSpot.
    """
    deal, company, contacts = hubspot.get_deal_with_associations(deal_id)
    deal_name = deal.get("properties", {}).get("dealname", "")

    logger.info("Processing deal creation %s: '%s'", deal_id, deal_name)

    if MICROSOFT_TRIGGER_TAG.lower() not in deal_name.lower():
        logger.info("Deal '%s' does not contain %s — skipping", deal_name, MICROSOFT_TRIGGER_TAG)
        return None

    # Idempotency: skip if already synced
    existing_referral_id = deal.get("properties", {}).get("microsoft_referral_id")
    if existing_referral_id:
        logger.info("Deal %s already synced to Microsoft referral %s", deal_id, existing_referral_id)
        return None

    # Build and submit the payload
    referral_payload = hubspot_deal_to_microsoft_referral(deal, company, contacts)
    logger.info("Creating Microsoft referral for deal %s with name: %s",
                deal_id, referral_payload.get("name"))

    referral = microsoft_client.create_referral(referral_payload)

    referral_id = referral.get("id", "")
    logger.info("Created Microsoft referral %s for deal %s", referral_id, deal_id)

    # Write the referral ID back to HubSpot
    hubspot.update_deal(
        deal_id,
        {
            "microsoft_referral_id": referral_id,
            "microsoft_sync_status": "synced",
            "microsoft_status": referral.get("status", "New"),
            "microsoft_substatus": referral.get("substatus", "Pending"),
        },
    )

    return {
        "action": "created",
        "hubspotDealId": deal_id,
        "dealName": deal_name,
        "microsoftReferralId": referral_id,
    }


# ---------------------------------------------------------------------------
# deal.propertyChange handler
# ---------------------------------------------------------------------------

def _handle_deal_update(deal_id: str, webhook_event: dict, hubspot: HubSpotClient, microsoft_client) -> dict | None:
    """
    Sync a HubSpot deal property change to Microsoft Partner Center.
    """
    deal, company, contacts = hubspot.get_deal_with_associations(deal_id)
    props = deal.get("properties", {})

    # Only sync deals that are already in Microsoft
    referral_id = props.get("microsoft_referral_id")
    if not referral_id:
        logger.debug("Deal %s has no microsoft_referral_id — skipping update", deal_id)
        return None

    # The deal must still have #Microsoft to stay in sync scope
    deal_name = props.get("dealname", "")
    if MICROSOFT_TRIGGER_TAG.lower() not in deal_name.lower():
        logger.info("Deal %s no longer contains %s — skipping update", deal_id, MICROSOFT_TRIGGER_TAG)
        return None

    changed_property = webhook_event.get("propertyName", "")
    changed_properties = {changed_property} if changed_property else set()

    # Fetch current Microsoft referral state
    try:
        current_referral = microsoft_client.get_referral(referral_id)
    except Exception as exc:
        logger.warning("Could not fetch Microsoft referral %s: %s", referral_id, exc)
        return None

    # Build the update payload
    update_payload, warnings = hubspot_deal_to_microsoft_referral_update(
        deal, current_referral, company, contacts, changed_properties
    )

    # Surface warnings back to HubSpot as notes
    for warning in warnings:
        logger.warning("Deal %s: %s", deal_id, warning)
        try:
            hubspot.add_note_to_deal(
                deal_id,
                f"⚠️ Microsoft Partner Center Sync Warning\n\n{warning}"
            )
        except Exception as note_exc:
            logger.warning("Could not add note to deal %s: %s", deal_id, note_exc)

    if update_payload is None:
        return {
            "action": "blocked",
            "hubspotDealId": deal_id,
            "microsoftReferralId": referral_id,
            "warnings": warnings,
        }

    # Send the update
    etag = current_referral.get("eTag", "")
    try:
        microsoft_client.update_referral(referral_id, update_payload, etag)
        logger.info("Updated Microsoft referral %s from deal %s", referral_id, deal_id)

        hubspot.update_deal(deal_id, {"microsoft_sync_status": "synced"})

        return {
            "action": "updated",
            "hubspotDealId": deal_id,
            "microsoftReferralId": referral_id,
            "changedProperty": changed_property,
            "warnings": warnings,
        }
    except Exception as exc:
        logger.error("Failed to update Microsoft referral %s: %s", referral_id, exc)
        hubspot.update_deal(deal_id, {"microsoft_sync_status": "error"})
        raise


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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
