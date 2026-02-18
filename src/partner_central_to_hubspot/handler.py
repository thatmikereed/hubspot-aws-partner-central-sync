"""
Lambda handler: AWS Partner Central Invitations → HubSpot

Triggered on a schedule (EventBridge) every N minutes.

Uses StartEngagementByAcceptingInvitationTask (the correct API method for
accepting AWS-originated engagement invitations — NOT the non-existent
AcceptEngagementInvitation). This method both accepts the invitation AND
starts the engagement in one atomic task call.

Workflow per invitation:
  1. Deduplicate: skip if already synced to HubSpot via aws_invitation_id
  2. Fetch invitation details to extract the opportunity ID
  3. Call StartEngagementByAcceptingInvitationTask (accepts + starts engagement)
  4. Poll the task until COMPLETE (with exponential backoff)
  5. Fetch the full opportunity from the response's OpportunityId
  6. Create a HubSpot deal with all opportunity fields mapped
"""

import json
import logging
import os
import sys
import time
import uuid

sys.path.insert(0, "/var/task")

from common.aws_client import get_partner_central_client, PARTNER_CENTRAL_CATALOG
from common.hubspot_client import HubSpotClient
from common.mappers import partner_central_opportunity_to_hubspot
from common.validators import validate_partner_central_id

logger = logging.getLogger(__name__)

# How long to wait for a task to complete (seconds)
TASK_POLL_INTERVAL = 2
TASK_MAX_ATTEMPTS = 10


def lambda_handler(event: dict, context) -> dict:
    logger.info("Starting Partner Central invitation sync")

    pc_client = get_partner_central_client()
    hubspot = HubSpotClient()

    accepted = []
    errors = []

    try:
        invitations = _list_pending_invitations(pc_client)
        logger.info("Found %d pending invitation(s)", len(invitations))

        for invitation in invitations:
            invitation_id = invitation.get("Id") or invitation.get("Arn", "")
            try:
                result = _process_invitation(invitation_id, pc_client, hubspot)
                if result:
                    accepted.append(result)
            except Exception as exc:
                logger.exception("Error processing invitation %s: %s", invitation_id, exc)
                errors.append({"invitationId": invitation_id, "error": str(exc)})

    except Exception as exc:
        logger.exception("Fatal error listing invitations: %s", exc)
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(exc)}),
        }

    summary = {
        "invitationsProcessed": len(accepted),
        "errors": len(errors),
        "results": accepted,
        "errorDetails": errors,
    }
    logger.info("Sync complete: %s", json.dumps(summary, default=str))
    return {"statusCode": 200, "body": json.dumps(summary)}


def _list_pending_invitations(pc_client) -> list:
    """
    Page through all PENDING EngagementInvitations.
    Filters to OpportunityInvitation payload type only.
    """
    invitations = []
    next_token = None

    while True:
        kwargs: dict = {
            "Catalog": PARTNER_CENTRAL_CATALOG,
            "ParticipantType": "RECEIVER",
            "PayloadType": ["OpportunityInvitation"],
            "MaxResults": 50,
            "Sort": {
                "SortBy": "InvitationDate",
                "SortOrder": "DESCENDING",
            },
        }
        if next_token:
            kwargs["NextToken"] = next_token

        response = pc_client.list_engagement_invitations(**kwargs)

        for inv in response.get("EngagementInvitationSummaries", []):
            if inv.get("Status", "").upper() == "PENDING":
                invitations.append(inv)

        next_token = response.get("NextToken")
        if not next_token:
            break

    return invitations


def _process_invitation(invitation_id: str, pc_client, hubspot: HubSpotClient) -> dict | None:
    """
    Accept one Partner Central invitation via StartEngagementByAcceptingInvitationTask,
    then create a HubSpot deal from the opportunity.
    """
    # Validate invitation ID
    try:
        invitation_id = validate_partner_central_id(invitation_id, "Invitation ID")
    except ValueError as e:
        logger.error(f"Invalid invitation ID: {e}")
        raise
    
    # -----------------------------------------------------------------------
    # 1. Deduplicate: skip if already synced
    # -----------------------------------------------------------------------
    existing = hubspot.search_deals_by_aws_invitation_id(invitation_id)
    if existing:
        logger.info("Invitation %s already synced (deal %s) — skipping",
                    invitation_id, existing[0].get("id"))
        return None

    # -----------------------------------------------------------------------
    # 2. Fetch invitation details (for logging / opportunity ID extraction)
    # -----------------------------------------------------------------------
    logger.info("Fetching invitation details: %s", invitation_id)
    inv_detail = pc_client.get_engagement_invitation(
        Catalog=PARTNER_CENTRAL_CATALOG,
        Identifier=invitation_id,
    )
    logger.info("Invitation payload type: %s",
                inv_detail.get("PayloadType", "unknown"))

    # -----------------------------------------------------------------------
    # 3. Accept via StartEngagementByAcceptingInvitationTask
    #    (this is the correct API — AcceptEngagementInvitation does NOT exist)
    # -----------------------------------------------------------------------
    client_token = f"hs-accept-{uuid.uuid4()}"
    logger.info("Accepting invitation %s via StartEngagementByAcceptingInvitationTask", invitation_id)

    task_response = pc_client.start_engagement_by_accepting_invitation_task(
        Catalog=PARTNER_CENTRAL_CATALOG,
        Identifier=invitation_id,
        ClientToken=client_token,
    )

    task_id = task_response.get("TaskId") or task_response.get("Id")
    opportunity_id = task_response.get("OpportunityId")
    task_status = task_response.get("TaskStatus", "")

    logger.info("Task started: id=%s status=%s opportunity=%s",
                task_id, task_status, opportunity_id)

    # -----------------------------------------------------------------------
    # 4. Poll task to completion (some tasks complete synchronously)
    # -----------------------------------------------------------------------
    if task_id and task_status not in ("COMPLETE", "FAILED"):
        task_status, opportunity_id = _poll_task(pc_client, task_id, opportunity_id)

    if task_status == "FAILED":
        raise RuntimeError(
            f"StartEngagementByAcceptingInvitationTask failed for invitation {invitation_id}. "
            f"Check CloudWatch logs for task {task_id}."
        )

    logger.info("Invitation %s accepted. Opportunity ID: %s", invitation_id, opportunity_id)

    # -----------------------------------------------------------------------
    # 5. Fetch full opportunity details
    # -----------------------------------------------------------------------
    if not opportunity_id:
        logger.warning("No opportunity ID from task response for invitation %s", invitation_id)
        return {
            "invitationId": invitation_id,
            "status": "accepted_no_opportunity",
        }

    opportunity = pc_client.get_opportunity(
        Catalog=PARTNER_CENTRAL_CATALOG,
        Identifier=opportunity_id,
    )
    logger.info("Fetched opportunity %s: '%s'",
                opportunity_id,
                opportunity.get("Project", {}).get("Title", ""))

    # -----------------------------------------------------------------------
    # 6. Create HubSpot deal
    # -----------------------------------------------------------------------
    hs_properties = partner_central_opportunity_to_hubspot(
        opportunity, invitation_id=invitation_id
    )
    deal = hubspot.create_deal(hs_properties)

    logger.info(
        "Created HubSpot deal %s '%s' from PC invitation %s / opportunity %s",
        deal["id"],
        hs_properties.get("dealname"),
        invitation_id,
        opportunity_id,
    )

    return {
        "invitationId": invitation_id,
        "partnerCentralOpportunityId": opportunity_id,
        "hubspotDealId": deal["id"],
        "hubspotDealName": hs_properties.get("dealname"),
        "status": "synced",
    }


def _poll_task(pc_client, task_id: str, current_opportunity_id: str | None) -> tuple[str, str | None]:
    """
    Poll a Partner Central async task until it reaches a terminal state.
    Returns (final_status, opportunity_id).
    """
    opportunity_id = current_opportunity_id

    for attempt in range(TASK_MAX_ATTEMPTS):
        time.sleep(TASK_POLL_INTERVAL * (attempt + 1))  # simple backoff

        try:
            task = pc_client.get_engagement_by_accepting_invitation_task(
                Catalog=PARTNER_CENTRAL_CATALOG,
                TaskIdentifier=task_id,
            )
        except Exception as exc:
            logger.warning("Error polling task %s (attempt %d): %s", task_id, attempt + 1, exc)
            continue

        status = task.get("TaskStatus", "")
        opportunity_id = task.get("OpportunityId") or opportunity_id
        logger.info("Task %s status: %s (attempt %d)", task_id, status, attempt + 1)

        if status in ("COMPLETE", "FAILED"):
            return status, opportunity_id

    logger.warning("Task %s did not complete within %d attempts", task_id, TASK_MAX_ATTEMPTS)
    return "UNKNOWN", opportunity_id
