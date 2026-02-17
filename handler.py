"""
Lambda handler: HubSpot Webhook → AWS Partner Central

Triggered by API Gateway when HubSpot fires a deal.creation webhook.
Filters for deals whose name contains #AWS, then creates a corresponding
opportunity in AWS Partner Central using the assumed IAM role.
"""

import json
import logging
import os
import sys

# Allow relative imports when running in Lambda
sys.path.insert(0, "/var/task")

from common.aws_client import get_partner_central_client, PARTNER_CENTRAL_CATALOG
from common.hubspot_client import HubSpotClient
from common.mappers import hubspot_deal_to_partner_central

logger = logging.getLogger()
logger.setLevel(logging.INFO)

AWS_TRIGGER_TAG = "#AWS"


def lambda_handler(event: dict, context) -> dict:
    """
    Entry point for the HubSpot → Partner Central Lambda.

    Expects an API Gateway proxy event containing the HubSpot webhook payload.
    HubSpot webhook payloads are arrays of subscription events.
    """
    logger.info("Received event: %s", json.dumps(event, default=str))

    # -----------------------------------------------------------------------
    # 1. Parse and validate the webhook payload
    # -----------------------------------------------------------------------
    try:
        body = event.get("body", "")
        if event.get("isBase64Encoded"):
            import base64
            body = base64.b64decode(body).decode("utf-8")

        webhook_events = json.loads(body) if isinstance(body, str) else body
    except (json.JSONDecodeError, Exception) as exc:
        logger.error("Failed to parse webhook body: %s", exc)
        return _response(400, {"error": "Invalid JSON payload"})

    # Optionally verify HubSpot signature
    _verify_signature(event)

    # -----------------------------------------------------------------------
    # 2. Process each event in the batch
    # -----------------------------------------------------------------------
    processed = []
    errors = []

    hubspot = HubSpotClient()
    pc_client = get_partner_central_client()

    for webhook_event in webhook_events:
        event_type = webhook_event.get("subscriptionType", "")
        object_id = str(webhook_event.get("objectId", ""))

        if "deal.creation" not in event_type:
            logger.info("Skipping non-creation event: %s", event_type)
            continue

        try:
            result = _process_deal_creation(object_id, hubspot, pc_client)
            if result:
                processed.append(result)
        except Exception as exc:
            logger.exception("Error processing deal %s: %s", object_id, exc)
            errors.append({"dealId": object_id, "error": str(exc)})

    return _response(
        200,
        {
            "processed": len(processed),
            "skipped": len(webhook_events) - len(processed) - len(errors),
            "errors": len(errors),
            "results": processed,
        },
    )


def _process_deal_creation(deal_id: str, hubspot: HubSpotClient, pc_client) -> dict | None:
    """
    Fetch the HubSpot deal, check for #AWS tag, and create a Partner Central opportunity.
    Returns a result dict on success, None if the deal should be skipped.
    """
    # Fetch full deal
    deal = hubspot.get_deal(deal_id)
    deal_name = deal.get("properties", {}).get("dealname", "")

    logger.info("Processing deal %s: '%s'", deal_id, deal_name)

    # Filter: only process deals with #AWS in the title
    if AWS_TRIGGER_TAG.lower() not in deal_name.lower():
        logger.info("Deal '%s' does not contain %s — skipping", deal_name, AWS_TRIGGER_TAG)
        return None

    # Check if already synced (idempotency)
    existing_pc_id = deal.get("properties", {}).get("aws_opportunity_id")
    if existing_pc_id:
        logger.info("Deal %s already has PC opportunity %s — skipping", deal_id, existing_pc_id)
        return None

    # Map to Partner Central format
    pc_payload = hubspot_deal_to_partner_central(deal)
    logger.info("Creating Partner Central opportunity for deal %s", deal_id)

    # Create opportunity in Partner Central
    pc_response = pc_client.create_opportunity(
        Catalog=PARTNER_CENTRAL_CATALOG,
        **{k: v for k, v in pc_payload.items() if k != "Catalog"},
    )

    opportunity_id = pc_response.get("Id", "")
    opportunity_arn = pc_response.get("OpportunityArn", "")

    logger.info(
        "Created Partner Central opportunity %s (ARN: %s) for deal %s",
        opportunity_id,
        opportunity_arn,
        deal_id,
    )

    # Write the PC opportunity ID back to the HubSpot deal
    hubspot.update_deal(
        deal_id,
        {
            "aws_opportunity_id": opportunity_id,
            "aws_opportunity_arn": opportunity_arn,
            "aws_sync_status": "synced",
        },
    )

    return {
        "hubspotDealId": deal_id,
        "dealName": deal_name,
        "partnerCentralOpportunityId": opportunity_id,
        "partnerCentralOpportunityArn": opportunity_arn,
    }


def _verify_signature(event: dict) -> None:
    """Verify HubSpot webhook signature if secret is configured."""
    secret = os.environ.get("HUBSPOT_WEBHOOK_SECRET")
    if not secret:
        return  # Signature verification disabled

    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    signature = headers.get("x-hubspot-signature-v3", "")

    if not signature:
        logger.warning("Missing HubSpot signature header — proceeding without verification")
        return

    body = event.get("body", "").encode("utf-8")
    hubspot = HubSpotClient()
    if not hubspot.verify_webhook_signature(body, signature, secret):
        raise ValueError("Invalid HubSpot webhook signature")


def _response(status_code: int, body: dict) -> dict:
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }
