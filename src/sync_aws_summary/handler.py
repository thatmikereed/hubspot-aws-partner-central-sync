"""
Lambda handler: Sync AWS Opportunity Summary

Scheduled Lambda (runs hourly) that fetches GetAwsOpportunitySummary for all
active opportunities and syncs AWS's view (engagement score, seller notes,
recommended actions) back to HubSpot.

This provides partners visibility into:
- AWS Engagement Score (0-100) - how interested AWS is
- AWS Involvement Type (Co-Sell vs For Visibility Only)
- AWS Seller assigned to the opportunity
- Recommended next actions from AWS
"""

import json
import logging
import sys

sys.path.insert(0, "/var/task")

from common.aws_client import get_partner_central_client, PARTNER_CENTRAL_CATALOG
from common.hubspot_client import HubSpotClient, HUBSPOT_API_BASE

logger = logging.getLogger(__name__)


def lambda_handler(event: dict, context) -> dict:
    """
    Scheduled sync of AWS Opportunity Summaries.

    For each HubSpot deal with an aws_opportunity_id and a review status
    of Approved or Action Required, fetch the AWS view and sync to HubSpot.
    """
    logger.info("Starting AWS Opportunity Summary sync")

    hubspot = HubSpotClient()
    pc_client = get_partner_central_client()

    synced = []
    errors = []

    try:
        # Get all deals with AWS opportunities in eligible states
        deals = _list_eligible_deals(hubspot)
        logger.info("Found %d eligible deals to sync", len(deals))

        for deal in deals:
            deal_id = deal["id"]
            props = deal.get("properties", {})
            opportunity_id = props.get("aws_opportunity_id")

            try:
                result = _sync_aws_summary(
                    deal_id, opportunity_id, hubspot, pc_client, props
                )
                if result:
                    synced.append(result)
            except Exception as exc:
                logger.exception(
                    "Error syncing deal %s / opportunity %s: %s",
                    deal_id,
                    opportunity_id,
                    exc,
                )
                errors.append(
                    {
                        "dealId": deal_id,
                        "opportunityId": opportunity_id,
                        "error": str(exc),
                    }
                )

    except Exception as exc:
        logger.exception("Fatal error in AWS summary sync: %s", exc)
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(exc)}),
        }

    summary = {
        "dealsSynced": len(synced),
        "errors": len(errors),
        "results": synced,
        "errorDetails": errors,
    }

    logger.info("AWS summary sync complete: %s", json.dumps(summary, default=str))
    return {"statusCode": 200, "body": json.dumps(summary)}


def _list_eligible_deals(hubspot: HubSpotClient) -> list:
    """
    Search for HubSpot deals that have AWS opportunities in states where
    AWS summary data is available (Approved, Action Required, In Review).
    """
    # Search for deals with aws_opportunity_id present
    url = f"{HUBSPOT_API_BASE}/crm/v3/objects/deals/search"

    payload = {
        "filterGroups": [
            {
                "filters": [
                    {
                        "propertyName": "aws_opportunity_id",
                        "operator": "HAS_PROPERTY",
                    },
                    {
                        "propertyName": "aws_review_status",
                        "operator": "IN",
                        "values": [
                            "Approved",
                            "Action Required",
                            "In Review",
                            "Submitted",
                        ],
                    },
                ]
            }
        ],
        "properties": [
            "dealname",
            "aws_opportunity_id",
            "aws_review_status",
            "aws_engagement_score",
            "aws_last_summary_sync",
        ],
        "limit": 100,
    }

    try:
        response = hubspot.session.post(
            "https://api.hubapi.com/crm/v3/objects/deals/search", json=payload
        )
        response.raise_for_status()
        return response.json().get("results", [])
    except Exception as exc:
        logger.warning("Could not search for eligible deals: %s", exc)
        return []


def _sync_aws_summary(
    deal_id: str,
    opportunity_id: str,
    hubspot: HubSpotClient,
    pc_client,
    current_deal_props: dict,
) -> dict | None:
    """
    Fetch AWS Opportunity Summary and sync to HubSpot.
    Also checks if AWS marked the opportunity as Closed Lost and creates a notification.
    """
    logger.info(
        "Fetching AWS summary for opportunity %s (deal %s)", opportunity_id, deal_id
    )

    try:
        # Fetch AWS view
        summary = pc_client.get_aws_opportunity_summary(
            Catalog=PARTNER_CENTRAL_CATALOG,
            Identifier=opportunity_id,
        )
    except Exception as exc:
        # AWS summary may not be available yet (too early in lifecycle)
        if "NotFound" in str(exc) or "404" in str(exc):
            logger.info("AWS summary not available yet for %s", opportunity_id)
            return None
        raise

    # Also fetch the full opportunity to check stage
    try:
        opportunity = pc_client.get_opportunity(
            Catalog=PARTNER_CENTRAL_CATALOG,
            Identifier=opportunity_id,
        )
        opp_lifecycle = opportunity.get("LifeCycle", {})
        opp_stage = opp_lifecycle.get("Stage", "")

        # Check if AWS marked opportunity as "Closed Lost"
        if opp_stage == "Closed Lost":
            logger.info(
                "Opportunity %s marked as Closed Lost by AWS - creating notification",
                opportunity_id,
            )
            _create_closed_lost_notification(hubspot, deal_id, opportunity_id)
            # Don't sync stage to HubSpot, but continue with other updates
    except Exception as exc:
        logger.warning("Could not fetch full opportunity %s: %s", opportunity_id, exc)
        opp_stage = None

    # Extract key fields
    lifecycle = summary.get("LifeCycle", {})
    customer = summary.get("Customer", {})
    insights = summary.get("Insights", {})

    engagement_score = insights.get("EngagementScore")
    involvement_type = lifecycle.get("InvolvementType", "")
    review_status = lifecycle.get("ReviewStatus", "")
    next_steps = lifecycle.get("NextSteps", "")

    # AWS team information
    aws_team = summary.get("OpportunityTeam", [])
    aws_seller = None
    if aws_team:
        # Usually the first team member is the primary AWS seller
        aws_seller = f"{aws_team[0].get('FirstName', '')} {aws_team[0].get('LastName', '')}".strip()
        if not aws_seller:
            aws_seller = aws_team[0].get("Email", "")

    # Build HubSpot update
    from datetime import datetime, timezone

    updates = {
        "aws_last_summary_sync": datetime.now(timezone.utc).isoformat(),
        "aws_review_status": review_status,
    }

    if engagement_score is not None:
        updates["aws_engagement_score"] = str(engagement_score)

    if involvement_type:
        updates["aws_involvement_type"] = involvement_type

    if next_steps:
        updates["aws_next_steps"] = next_steps[:65535]  # HubSpot text field limit

    if aws_seller:
        updates["aws_seller_name"] = aws_seller

    # Update HubSpot
    hubspot.update_deal(deal_id, updates)

    # If engagement score changed significantly, add a note
    current_score = current_deal_props.get("aws_engagement_score")
    if engagement_score is not None and current_score:
        try:
            score_delta = int(engagement_score) - int(current_score)
            if abs(score_delta) >= 10:
                direction = "increased" if score_delta > 0 else "decreased"
                note = (
                    f"📊 AWS Engagement Score {direction}\n\n"
                    f"Score: {engagement_score}/100 ({score_delta:+d})\n"
                    f"This indicates AWS's level of interest in co-selling this opportunity."
                )
                hubspot.add_note_to_deal(deal_id, note)
        except (ValueError, TypeError):
            pass

    logger.info(
        "Synced AWS summary for deal %s: score=%s, status=%s",
        deal_id,
        engagement_score,
        review_status,
    )

    return {
        "dealId": deal_id,
        "opportunityId": opportunity_id,
        "engagementScore": engagement_score,
        "reviewStatus": review_status,
        "involvementType": involvement_type,
        "awsSeller": aws_seller,
    }


def _create_closed_lost_notification(
    hubspot: HubSpotClient, deal_id: str, opportunity_id: str
) -> None:
    """
    Create a HubSpot task notification when AWS marks an opportunity as Closed Lost.

    Instead of automatically updating the HubSpot deal to closed lost, we create a
    high-priority task asking the sales rep to reach out to the AWS Account Executive
    to understand why the opportunity was closed lost.
    """
    try:
        # Get deal details to find owner
        deal = hubspot.get_deal(deal_id)
        owner_id = deal.get("properties", {}).get("hubspot_owner_id")
        deal_name = deal.get("properties", {}).get("dealname", "Unknown Deal")

        # Calculate due date (1 business day for high priority)
        from datetime import datetime, timedelta, timezone

        due_date = datetime.now(timezone.utc) + timedelta(days=1)

        # Create task with detailed message
        task_subject = f"🚨 AWS Marked Opportunity as Closed Lost: {deal_name}"
        task_body = (
            f"AWS Partner Central has marked this opportunity (ID: {opportunity_id}) as Closed Lost.\n\n"
            f"⚠️ This deal has NOT been automatically marked as closed lost in HubSpot.\n\n"
            f"ACTION REQUIRED:\n"
            f"Please reach out to your AWS Account Executive to:\n"
            f"1. Understand why the opportunity was marked as closed lost\n"
            f"2. Determine if there are any next steps or follow-up actions\n"
            f"3. Update the HubSpot deal stage accordingly based on the conversation\n\n"
            f"After speaking with AWS, update this deal's stage in HubSpot to reflect the actual status."
        )

        task_data = {
            "properties": {
                "hs_task_subject": task_subject,
                "hs_task_body": task_body,
                "hs_task_status": "NOT_STARTED",
                "hs_task_priority": "HIGH",
                "hs_timestamp": due_date.isoformat(),
            }
        }

        if owner_id:
            task_data["properties"]["hubspot_owner_id"] = owner_id

        # Create the task
        url = "https://api.hubapi.com/crm/v3/objects/tasks"
        response = hubspot.session.post(url, json=task_data)
        response.raise_for_status()

        task_id = response.json().get("id")
        logger.info(
            "Created HubSpot task %s for closed lost notification on deal %s",
            task_id,
            deal_id,
        )

        # Associate task with deal
        assoc_url = f"https://api.hubapi.com/crm/v4/objects/tasks/{task_id}/associations/deals/{deal_id}"
        assoc_data = [
            {
                "associationCategory": "HUBSPOT_DEFINED",
                "associationTypeId": 216,  # Task to Deal association type
            }
        ]
        hubspot.session.put(assoc_url, json=assoc_data)

        logger.info("Associated task %s with deal %s", task_id, deal_id)

        # Also add a note to the deal timeline for visibility
        note_body = (
            f"🚨 AWS Closed Lost Notification\n\n"
            f"AWS Partner Central has marked opportunity {opportunity_id} as Closed Lost.\n\n"
            f"A high-priority task has been created to reach out to the AWS AE for more information.\n"
            f"The HubSpot deal stage has NOT been automatically updated."
        )
        hubspot.add_note_to_deal(deal_id, note_body)

    except Exception as e:
        logger.error(
            "Error creating closed lost notification for deal %s: %s",
            deal_id,
            str(e),
            exc_info=True,
        )
        # Don't raise - we don't want to fail the entire sync if notification fails
