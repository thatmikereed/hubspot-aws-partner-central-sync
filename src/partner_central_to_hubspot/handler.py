"""
Lambda handler: AWS Partner Central Invitations → HubSpot

Triggered on a schedule (EventBridge/CloudWatch Events) every N minutes.
Polls AWS Partner Central for pending EngagementInvitations, accepts each one,
fetches the associated opportunity, and creates (or updates) a HubSpot deal.
"""

import json
import logging
import os
import sys

sys.path.insert(0, "/var/task")

from common.aws_client import get_partner_central_client, PARTNER_CENTRAL_CATALOG
from common.hubspot_client import HubSpotClient
from common.mappers import partner_central_opportunity_to_hubspot

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def lambda_handler(event: dict, context) -> dict:
    """
    Scheduled Lambda: poll Partner Central for pending invitations.
    For each pending invitation:
      1. Accept the invitation.
      2. Fetch the associated opportunity.
      3. Create a HubSpot deal (if one doesn't already exist).
    """
    logger.info("Starting Partner Central invitation sync")

    pc_client = get_partner_central_client()
    hubspot = HubSpotClient()

    accepted = []
    errors = []

    try:
        invitations = _list_pending_invitations(pc_client)
        logger.info("Found %d pending invitation(s)", len(invitations))

        for invitation in invitations:
            invitation_id = invitation.get("Id", "")
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
    Page through all PENDING EngagementInvitations from Partner Central.
    Returns a flat list of invitation objects.
    """
    invitations = []
    paginator_token = None

    while True:
        kwargs = {
            "Catalog": PARTNER_CENTRAL_CATALOG,
            "ParticipantType": "Receiver",
            "MaxResults": 50,
        }
        if paginator_token:
            kwargs["NextToken"] = paginator_token

        response = pc_client.list_engagement_invitations(**kwargs)

        for inv in response.get("EngagementInvitationSummaries", []):
            if inv.get("Status") == "PENDING":
                invitations.append(inv)

        paginator_token = response.get("NextToken")
        if not paginator_token:
            break

    return invitations


def _process_invitation(invitation_id: str, pc_client, hubspot: HubSpotClient) -> dict | None:
    """
    Accept a single Partner Central invitation, retrieve the opportunity,
    and create/update the corresponding HubSpot deal.
    """
    # -----------------------------------------------------------------------
    # 1. Check for duplicate: has this invitation already been processed?
    # -----------------------------------------------------------------------
    existing_deals = hubspot.search_deals_by_aws_opportunity_id(f"inv-{invitation_id}")
    if existing_deals:
        logger.info("Invitation %s already synced to HubSpot — skipping", invitation_id)
        return None

    # -----------------------------------------------------------------------
    # 2. Fetch invitation details to get the opportunity identifier
    # -----------------------------------------------------------------------
    logger.info("Fetching details for invitation %s", invitation_id)
    inv_detail = pc_client.get_engagement_invitation(
        Catalog=PARTNER_CENTRAL_CATALOG,
        Identifier=invitation_id,
    )

    opportunity_id = (
        inv_detail.get("Payload", {})
        .get("OpportunityInvitation", {})
        .get("OpportunitySummary", {})
        .get("Id")
    )

    # -----------------------------------------------------------------------
    # 3. Accept the invitation
    # -----------------------------------------------------------------------
    logger.info("Accepting invitation %s", invitation_id)
    pc_client.accept_engagement_invitation(
        Catalog=PARTNER_CENTRAL_CATALOG,
        Identifier=invitation_id,
    )
    logger.info("Invitation %s accepted", invitation_id)

    # -----------------------------------------------------------------------
    # 4. Fetch full opportunity details
    # -----------------------------------------------------------------------
    if not opportunity_id:
        logger.warning("Could not extract opportunity ID from invitation %s", invitation_id)
        return {"invitationId": invitation_id, "status": "accepted_no_opportunity"}

    logger.info("Fetching opportunity %s", opportunity_id)
    opportunity = pc_client.get_opportunity(
        Catalog=PARTNER_CENTRAL_CATALOG,
        Identifier=opportunity_id,
    )

    # -----------------------------------------------------------------------
    # 5. Create HubSpot deal
    # -----------------------------------------------------------------------
    hs_properties = partner_central_opportunity_to_hubspot(opportunity, invitation_id=invitation_id)
    deal = hubspot.create_deal(hs_properties)

    logger.info(
        "Created HubSpot deal %s from PC invitation %s / opportunity %s",
        deal["id"],
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
