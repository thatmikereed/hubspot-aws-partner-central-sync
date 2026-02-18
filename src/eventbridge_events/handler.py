"""
Lambda handler: EventBridge Event Processing

Processes real-time events from AWS Partner Central via EventBridge:

1. Opportunity Created - Another partner or AWS created an opportunity in a shared engagement
2. Opportunity Updated - AWS updated the opportunity (stage, notes, review status)
3. Engagement Invitation Created - Real-time invitation acceptance (replaces polling)

This handler enables:
- Instant invitation acceptance (no 5-minute polling delay)
- Reverse sync: PC → HubSpot when AWS makes changes
- Multi-party collaboration awareness
"""

import json
import logging
import os
import sys
import uuid

sys.path.insert(0, "/var/task")

from common.aws_client import get_partner_central_client, PARTNER_CENTRAL_CATALOG
from common.hubspot_client import HubSpotClient
from common.mappers import partner_central_opportunity_to_hubspot

logger = logging.getLogger(__name__)


def lambda_handler(event: dict, context) -> dict:
    """
    Process Partner Central EventBridge events.
    
    Event structure:
    {
        "version": "1",
        "id": "...",
        "source": "aws.partnercentral-selling",
        "detail-type": "Opportunity Created|Opportunity Updated|Engagement Invitation Created",
        "time": "2025-02-17T10:00:00Z",
        "region": "us-east-1",
        "account": "123456789012",
        "detail": {
            "schemaVersion": "1",
            "catalog": "AWS",
            "opportunity": {"identifier": "O1234567"},
            "invitation": {"identifier": "arn:..."}
        }
    }
    """
    logger.info("Received EventBridge event: %s", json.dumps(event, default=str))
    
    detail_type = event.get("detail-type", "")
    detail = event.get("detail", {})
    
    try:
        hubspot = HubSpotClient()
        pc_client = get_partner_central_client()
        
        if detail_type == "Opportunity Created":
            result = _handle_opportunity_created(detail, hubspot, pc_client)
        elif detail_type == "Opportunity Updated":
            result = _handle_opportunity_updated(detail, hubspot, pc_client)
        elif detail_type == "Engagement Invitation Created":
            result = _handle_invitation_created(detail, hubspot, pc_client)
        else:
            logger.warning("Unhandled event type: %s", detail_type)
            return {"statusCode": 200, "body": json.dumps({"skipped": True})}
        
        return {"statusCode": 200, "body": json.dumps(result)}
        
    except Exception as exc:
        logger.exception("Error processing EventBridge event: %s", exc)
        return {"statusCode": 500, "body": json.dumps({"error": str(exc)})}


# ---------------------------------------------------------------------------
# Event Handlers
# ---------------------------------------------------------------------------

def _handle_opportunity_created(detail: dict, hubspot: HubSpotClient, pc_client) -> dict:
    """
    Handle 'Opportunity Created' event.
    
    This typically means another partner or AWS created an opportunity in a
    shared engagement. We should check if we already have it in HubSpot;
    if not, create a new deal.
    """
    opportunity_id = detail.get("opportunity", {}).get("identifier")
    if not opportunity_id:
        logger.warning("Opportunity Created event missing identifier")
        return {"error": "Missing opportunity identifier"}
    
    logger.info("Processing Opportunity Created: %s", opportunity_id)
    
    # Check if already exists in HubSpot
    existing = hubspot.search_deals_by_aws_opportunity_id(opportunity_id)
    if existing:
        logger.info("Opportunity %s already exists as deal %s", opportunity_id, existing[0]["id"])
        return {"status": "already_exists", "dealId": existing[0]["id"]}
    
    # Fetch the opportunity details
    opportunity = pc_client.get_opportunity(
        Catalog=PARTNER_CENTRAL_CATALOG,
        Identifier=opportunity_id,
    )
    
    # Create HubSpot deal
    hs_properties = partner_central_opportunity_to_hubspot(opportunity)
    deal = hubspot.create_deal(hs_properties)
    
    logger.info("Created HubSpot deal %s from PC opportunity %s", deal["id"], opportunity_id)
    
    return {
        "status": "created",
        "dealId": deal["id"],
        "opportunityId": opportunity_id,
        "source": "eventbridge_opportunity_created",
    }


def _handle_opportunity_updated(detail: dict, hubspot: HubSpotClient, pc_client) -> dict:
    """
    Handle 'Opportunity Updated' event.
    
    This is the core of REVERSE SYNC: when AWS updates an opportunity
    (stage change, review status, notes), we sync those changes to HubSpot.
    """
    opportunity_id = detail.get("opportunity", {}).get("identifier")
    if not opportunity_id:
        return {"error": "Missing opportunity identifier"}
    
    logger.info("Processing Opportunity Updated: %s", opportunity_id)
    
    # Find the corresponding HubSpot deal
    deals = hubspot.search_deals_by_aws_opportunity_id(opportunity_id)
    if not deals:
        logger.warning("No HubSpot deal found for opportunity %s - creating new", opportunity_id)
        # Create deal if it doesn't exist (could be AWS-originated)
        opportunity = pc_client.get_opportunity(
            Catalog=PARTNER_CENTRAL_CATALOG,
            Identifier=opportunity_id,
        )
        hs_properties = partner_central_opportunity_to_hubspot(opportunity)
        deal = hubspot.create_deal(hs_properties)
        deal_id = deal["id"]
    else:
        deal_id = deals[0]["id"]
    
    # Fetch latest opportunity state
    opportunity = pc_client.get_opportunity(
        Catalog=PARTNER_CENTRAL_CATALOG,
        Identifier=opportunity_id,
    )
    
    # Also fetch AWS summary if available
    aws_summary = None
    try:
        aws_summary = pc_client.get_aws_opportunity_summary(
            Catalog=PARTNER_CENTRAL_CATALOG,
            Identifier=opportunity_id,
        )
    except Exception:
        pass  # Summary may not be available yet
    
    # Build update payload
    lifecycle = opportunity.get("LifeCycle", {})
    project = opportunity.get("Project", {})
    
    updates = {
        "aws_review_status": lifecycle.get("ReviewStatus", ""),
        "dealstage": _map_stage_to_hubspot(lifecycle.get("Stage", "")),
    }
    
    # Sync description if changed (but never the title - it's immutable)
    business_problem = project.get("CustomerBusinessProblem")
    if business_problem:
        updates["description"] = business_problem
    
    # Sync close date if changed
    target_close = lifecycle.get("TargetCloseDate")
    if target_close:
        from datetime import datetime
        try:
            dt = datetime.strptime(target_close, "%Y-%m-%d")
            updates["closedate"] = dt.isoformat() + "Z"
        except ValueError:
            pass
    
    # Sync AWS summary data if available
    if aws_summary:
        insights = aws_summary.get("Insights", {})
        engagement_score = insights.get("EngagementScore")
        if engagement_score is not None:
            updates["aws_engagement_score"] = str(engagement_score)
        
        aws_involvement = aws_summary.get("LifeCycle", {}).get("InvolvementType")
        if aws_involvement:
            updates["aws_involvement_type"] = aws_involvement
    
    # Update HubSpot
    hubspot.update_deal(deal_id, updates)
    
    # Add a note about the AWS update
    note_parts = ["🔄 AWS Updated Opportunity"]
    if "ReviewStatus" in lifecycle:
        note_parts.append(f"Review Status: {lifecycle['ReviewStatus']}")
    if "Stage" in lifecycle:
        note_parts.append(f"Stage: {lifecycle['Stage']}")
    if aws_summary:
        score = aws_summary.get("Insights", {}).get("EngagementScore")
        if score:
            note_parts.append(f"Engagement Score: {score}/100")
    
    hubspot.add_note_to_deal(deal_id, "\n".join(note_parts))
    
    logger.info("Synced PC updates to HubSpot deal %s", deal_id)
    
    return {
        "status": "synced",
        "dealId": deal_id,
        "opportunityId": opportunity_id,
        "updatedFields": list(updates.keys()),
    }


def _handle_invitation_created(detail: dict, hubspot: HubSpotClient, pc_client) -> dict:
    """
    Handle 'Engagement Invitation Created' event.
    
    This is INSTANT invitation processing - replaces the 5-minute polling loop.
    """
    invitation_id = detail.get("invitation", {}).get("identifier")
    if not invitation_id:
        return {"error": "Missing invitation identifier"}
    
    logger.info("Processing Engagement Invitation Created: %s", invitation_id)
    
    # Check if already processed
    existing = hubspot.search_deals_by_aws_invitation_id(invitation_id)
    if existing:
        logger.info("Invitation %s already processed as deal %s", invitation_id, existing[0]["id"])
        return {"status": "already_processed", "dealId": existing[0]["id"]}
    
    # Fetch invitation details
    invitation = pc_client.get_engagement_invitation(
        Catalog=PARTNER_CENTRAL_CATALOG,
        Identifier=invitation_id,
    )
    
    # Accept the invitation
    logger.info("Auto-accepting invitation %s", invitation_id)
    
    client_token = f"eb-accept-{uuid.uuid4()}"
    task_response = pc_client.start_engagement_by_accepting_invitation_task(
        Catalog=PARTNER_CENTRAL_CATALOG,
        Identifier=invitation_id,
        ClientToken=client_token,
    )
    
    opportunity_id = task_response.get("OpportunityId")
    task_status = task_response.get("TaskStatus", "")
    
    logger.info("Invitation accepted: task_status=%s opportunity=%s",
                task_status, opportunity_id)
    
    # Fetch opportunity
    if not opportunity_id:
        logger.warning("No opportunity ID returned from acceptance task")
        return {"status": "accepted_no_opportunity", "invitationId": invitation_id}
    
    opportunity = pc_client.get_opportunity(
        Catalog=PARTNER_CENTRAL_CATALOG,
        Identifier=opportunity_id,
    )
    
    # Create HubSpot deal
    hs_properties = partner_central_opportunity_to_hubspot(opportunity, invitation_id=invitation_id)
    deal = hubspot.create_deal(hs_properties)
    
    logger.info("Created HubSpot deal %s from invitation %s", deal["id"], invitation_id)
    
    return {
        "status": "accepted_and_created",
        "dealId": deal["id"],
        "opportunityId": opportunity_id,
        "invitationId": invitation_id,
        "source": "eventbridge_realtime",
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _map_stage_to_hubspot(pc_stage: str) -> str:
    """Map PC stage back to HubSpot deal stage."""
    from common.mappers import PC_STAGE_TO_HUBSPOT
    return PC_STAGE_TO_HUBSPOT.get(pc_stage, "appointmentscheduled")
