"""
Lambda handler: HubSpot Webhook → AWS Partner Central

Handles two HubSpot event types:

  deal.creation
    Triggered when a new deal is created in HubSpot. If the deal name
    contains #AWS, creates a corresponding Opportunity in Partner Central,
    associates the configured solution, and writes the PC Opportunity ID
    back to HubSpot.

  deal.propertyChange
    Triggered when any tracked deal property changes. If the deal already
    has an aws_opportunity_id, syncs the update to Partner Central —
    with the following critical exception:
      Project.Title is IMMUTABLE in Partner Central after submission.
      If the HubSpot deal name (dealname) changes, the title change is
      silently dropped from the PC update and a note is added to the
      HubSpot deal to inform the sales rep.

Both event types respect Partner Central's review-status rules:
  - If ReviewStatus is Submitted or In-Review, all updates are blocked.
"""

import base64
import json
import logging
import os
import sys
import uuid

sys.path.insert(0, "/var/task")

from common.aws_client import get_partner_central_client, PARTNER_CENTRAL_CATALOG
from common.hubspot_client import HubSpotClient
from common.mappers import (
    hubspot_deal_to_partner_central,
    hubspot_deal_to_partner_central_update,
)
from common.validators import (
    validate_hubspot_id,
    sanitize_deal_name,
)

logger = logging.getLogger(__name__)

AWS_TRIGGER_TAG = "#AWS"

# HubSpot custom property that holds the PC title to detect renames
PC_TITLE_PROPERTY = "aws_opportunity_title"

# Sensitive headers to redact from logs
SENSITIVE_HEADERS = {
    "authorization", "x-hubspot-signature", "x-hubspot-signature-v3",
    "cookie", "x-api-key"
}


def _redact_sensitive_data(event: dict) -> dict:
    """
    Create a sanitized copy of the event for logging.
    Redacts sensitive headers and tokens to prevent credential exposure.
    """
    safe_event = event.copy()
    
    # Redact sensitive headers
    if "headers" in safe_event:
        safe_headers = {}
        for key, value in (safe_event.get("headers") or {}).items():
            if key.lower() in SENSITIVE_HEADERS:
                safe_headers[key] = "[REDACTED]"
            else:
                safe_headers[key] = value
        safe_event["headers"] = safe_headers
    
    # Don't log the full body if it's large
    if "body" in safe_event and safe_event["body"]:
        body_len = len(str(safe_event["body"]))
        if body_len > 1000:
            safe_event["body"] = f"[BODY REDACTED - {body_len} bytes]"
    
    return safe_event


def lambda_handler(event: dict, context) -> dict:
    logger.info("Received event: %s", json.dumps(_redact_sensitive_data(event), default=str))

    # Verify signature BEFORE parsing body to prevent processing malicious payloads
    # Note: _verify_signature handles base64 decoding internally for verification
    _verify_signature(event)

    try:
        body = event.get("body", "")
        # Decode if base64-encoded (API Gateway feature)
        if event.get("isBase64Encoded"):
            body = base64.b64decode(body).decode("utf-8")
        
        # Parse JSON payload
        webhook_events = json.loads(body) if isinstance(body, str) else body
    except (json.JSONDecodeError, Exception) as exc:
        logger.error("Failed to parse webhook body: %s", exc)
        return _response(400, {"error": "Invalid JSON payload"})

    processed = []
    errors = []

    hubspot = HubSpotClient()
    pc_client = get_partner_central_client()

    for webhook_event in webhook_events:
        event_type = webhook_event.get("subscriptionType", "")
        object_id = str(webhook_event.get("objectId", ""))

        try:
            if "deal.creation" in event_type:
                result = _handle_deal_creation(object_id, hubspot, pc_client)
            elif "deal.propertyChange" in event_type:
                result = _handle_deal_update(object_id, webhook_event, hubspot, pc_client)
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

def _handle_deal_creation(deal_id: str, hubspot: HubSpotClient, pc_client) -> dict | None:
    """
    Fetch the HubSpot deal, check for #AWS tag, and create a Partner Central
    Opportunity. On success, associates the configured PC solution and writes
    the Opportunity ID + title back to HubSpot.
    """
    # Validate deal ID
    try:
        deal_id = validate_hubspot_id(deal_id, "Deal ID")
    except ValueError as e:
        logger.error(f"Invalid deal ID: {e}")
        raise
    
    deal, company, contacts = hubspot.get_deal_with_associations(deal_id)
    deal_name = deal.get("properties", {}).get("dealname", "")
    
    # Sanitize deal name for logging
    safe_deal_name = sanitize_deal_name(deal_name)

    logger.info("Processing deal creation %s: '%s'", deal_id, safe_deal_name)

    if AWS_TRIGGER_TAG.lower() not in deal_name.lower():
        logger.info("Deal '%s' does not contain %s — skipping", deal_name, AWS_TRIGGER_TAG)
        return None

    # Idempotency: skip if already synced
    existing_pc_id = deal.get("properties", {}).get("aws_opportunity_id")
    if existing_pc_id:
        logger.info("Deal %s already synced to PC opportunity %s", deal_id, existing_pc_id)
        return None

    # Build and submit the payload
    pc_payload = hubspot_deal_to_partner_central(deal, company, contacts)
    logger.info("Creating PC opportunity for deal %s with payload keys: %s",
                deal_id, list(pc_payload.keys()))

    pc_response = pc_client.create_opportunity(
        Catalog=PARTNER_CENTRAL_CATALOG,
        ClientToken=pc_payload["ClientToken"],
        Origin=pc_payload["Origin"],
        OpportunityType=pc_payload["OpportunityType"],
        NationalSecurity=pc_payload["NationalSecurity"],
        PartnerOpportunityIdentifier=pc_payload["PartnerOpportunityIdentifier"],
        PrimaryNeedsFromAws=pc_payload["PrimaryNeedsFromAws"],
        Customer=pc_payload["Customer"],
        LifeCycle=pc_payload["LifeCycle"],
        Project=pc_payload["Project"],
    )

    opportunity_id = pc_response.get("Id", "")
    logger.info("Created PC opportunity %s for deal %s", opportunity_id, deal_id)

    # Associate the configured solution (required before submission)
    solution_id = os.environ.get("PARTNER_CENTRAL_SOLUTION_ID")
    if solution_id:
        _associate_solution(pc_client, opportunity_id, solution_id)
    else:
        logger.warning(
            "PARTNER_CENTRAL_SOLUTION_ID not set — skipping solution association. "
            "The opportunity cannot be submitted to AWS until a solution is associated."
        )

    # Write the PC Opportunity ID and canonical title back to HubSpot
    hubspot.update_deal(
        deal_id,
        {
            "aws_opportunity_id": opportunity_id,
            "aws_opportunity_title": pc_payload["Project"]["Title"],
            "aws_sync_status": "synced",
            "aws_review_status": "Pending Submission",
        },
    )

    return {
        "action": "created",
        "hubspotDealId": deal_id,
        "dealName": deal_name,
        "partnerCentralOpportunityId": opportunity_id,
        "solutionAssociated": bool(solution_id),
    }


# ---------------------------------------------------------------------------
# deal.propertyChange handler
# ---------------------------------------------------------------------------

def _handle_deal_update(deal_id: str, webhook_event: dict, hubspot: HubSpotClient, pc_client) -> dict | None:
    """
    Sync a HubSpot deal property change to Partner Central.

    Handles the title-immutability rule:
      - If the changed property is 'dealname', the title is NOT sent to PC.
      - A note is added to the HubSpot deal to inform the sales rep.
    """
    deal, company, contacts = hubspot.get_deal_with_associations(deal_id)
    props = deal.get("properties", {})

    # Only sync deals that are already in PC
    opportunity_id = props.get("aws_opportunity_id")
    if not opportunity_id:
        logger.debug("Deal %s has no aws_opportunity_id — skipping update", deal_id)
        return None

    # The deal must still have #AWS to stay in sync scope
    deal_name = props.get("dealname", "")
    if AWS_TRIGGER_TAG.lower() not in deal_name.lower():
        logger.info("Deal %s no longer contains %s — skipping update", deal_id, AWS_TRIGGER_TAG)
        return None

    changed_property = webhook_event.get("propertyName", "")
    changed_properties = {changed_property} if changed_property else set()

    # Fetch current PC state to check review status and title
    current_pc_opp = _get_pc_opportunity(pc_client, opportunity_id)
    if not current_pc_opp:
        logger.warning("Could not fetch PC opportunity %s — skipping update", opportunity_id)
        return None

    # Build the update payload (mappers handles all blocking/stripping logic)
    update_payload, warnings = hubspot_deal_to_partner_central_update(
        deal, current_pc_opp, company, contacts, changed_properties
    )

    # Surface warnings back to HubSpot as notes
    for warning in warnings:
        logger.warning("Deal %s: %s", deal_id, warning)
        if "title cannot be changed" in warning.lower() or "immutable" in warning.lower():
            try:
                hubspot.add_note_to_deal(
                    deal_id,
                    f"🔒 AWS Partner Central — Title Change Blocked\n\n{warning}"
                )
                # Revert the display title in HubSpot to the canonical PC title
                canonical_title = current_pc_opp.get("Project", {}).get("Title", "")
                if canonical_title:
                    # Append #AWS if missing
                    display_title = canonical_title if "#AWS" in canonical_title else f"{canonical_title} #AWS"
                    hubspot.update_deal(deal_id, {
                        "dealname": display_title,
                        "aws_opportunity_title": canonical_title,
                    })
            except Exception as note_exc:
                logger.warning("Could not add note to deal %s: %s", deal_id, note_exc)

    if update_payload is None:
        return {
            "action": "blocked",
            "hubspotDealId": deal_id,
            "partnerCentralOpportunityId": opportunity_id,
            "warnings": warnings,
        }

    # Send the update
    pc_client.update_opportunity(**update_payload)
    logger.info("Updated PC opportunity %s from deal %s", opportunity_id, deal_id)

    hubspot.update_deal(deal_id, {"aws_sync_status": "synced"})

    return {
        "action": "updated",
        "hubspotDealId": deal_id,
        "partnerCentralOpportunityId": opportunity_id,
        "changedProperty": changed_property,
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _associate_solution(pc_client, opportunity_id: str, solution_id: str) -> None:
    """Associate the partner's PC solution with the opportunity."""
    try:
        pc_client.associate_opportunity(
            Catalog=PARTNER_CENTRAL_CATALOG,
            OpportunityIdentifier=opportunity_id,
            RelatedEntityIdentifier=solution_id,
            RelatedEntityType="Solutions",
        )
        logger.info("Associated solution %s with opportunity %s", solution_id, opportunity_id)
    except Exception as exc:
        logger.warning("Could not associate solution %s with %s: %s", solution_id, opportunity_id, exc)


def _get_pc_opportunity(pc_client, opportunity_id: str) -> dict | None:
    """Fetch a Partner Central opportunity; return None on failure."""
    try:
        return pc_client.get_opportunity(
            Catalog=PARTNER_CENTRAL_CATALOG,
            Identifier=opportunity_id,
        )
    except Exception as exc:
        logger.error("Failed to fetch PC opportunity %s: %s", opportunity_id, exc)
        return None


def _verify_signature(event: dict) -> None:
    """
    Verify HubSpot webhook signature if secret is configured.
    
    Security: This MUST be called before parsing the body to prevent
    processing of malicious payloads.
    
    Note: This function handles base64 decoding internally - do NOT decode
    the body before calling this function.
    """
    secret = os.environ.get("HUBSPOT_WEBHOOK_SECRET")
    if not secret:
        logger.error(
            "CRITICAL SECURITY WARNING: HUBSPOT_WEBHOOK_SECRET is not configured! "
            "Webhook signature verification is disabled. This allows unauthenticated "
            "requests and is a CRITICAL security risk in production. "
            "Set HUBSPOT_WEBHOOK_SECRET immediately or risk unauthorized access."
        )
        # In production, you may want to: raise ValueError("Webhook secret required")
        return
    
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    signature = headers.get("x-hubspot-signature-v3", "")
    
    if not signature:
        logger.error("Missing HubSpot signature header in webhook request")
        raise ValueError("Missing required webhook signature header")
    
    # Get the raw body for signature verification
    body = event.get("body", "")
    if event.get("isBase64Encoded"):
        # Decode for signature verification - the caller will decode again for parsing
        body = base64.b64decode(body)
    else:
        body = body.encode("utf-8") if isinstance(body, str) else body
    
    hubspot = HubSpotClient()
    if not hubspot.verify_webhook_signature(body, signature, secret):
        logger.error("Invalid HubSpot webhook signature - possible forgery attempt")
        raise ValueError("Invalid HubSpot webhook signature")


def _response(status_code: int, body: dict) -> dict:
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body, default=str),
    }
