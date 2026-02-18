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

import time
import uuid

from common.base_handler import BaseLambdaHandler
from common.aws_client import PARTNER_CENTRAL_CATALOG
from common.mappers import partner_central_opportunity_to_hubspot
from common.validators import validate_partner_central_id

# How long to wait for a task to complete (seconds)
TASK_POLL_INTERVAL = 2
TASK_MAX_ATTEMPTS = 10


class PartnerCentralToHubSpotHandler(BaseLambdaHandler):
    """
    Handles scheduled sync of Partner Central engagement invitations to HubSpot.

    Accepts pending invitations and creates corresponding deals.
    """

    def _execute(self, event: dict, context: dict) -> dict:
        self.logger.info("Starting Partner Central invitation sync")

        accepted = []
        errors = []

        try:
            invitations = self._list_pending_invitations()
            self.logger.info("Found %d pending invitation(s)", len(invitations))

            for invitation in invitations:
                invitation_id = invitation.get("Id") or invitation.get("Arn", "")
                try:
                    result = self._process_invitation(invitation_id)
                    if result:
                        accepted.append(result)
                except Exception as exc:
                    self.logger.exception("Error processing invitation %s: %s", invitation_id, exc)
                    errors.append({"invitationId": invitation_id, "error": str(exc)})

        except Exception as exc:
            self.logger.exception("Fatal error listing invitations: %s", exc)
            return self._error_response(str(exc), 500)

        summary = {
            "invitationsProcessed": len(accepted),
            "errors": len(errors),
            "results": accepted,
            "errorDetails": errors,
        }
        self.logger.info("Sync complete: invitations=%d errors=%d", len(accepted), len(errors))
        return self._success_response(summary)


    def _list_pending_invitations(self) -> list:
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

            response = self.pc_client.list_engagement_invitations(**kwargs)

            for inv in response.get("EngagementInvitationSummaries", []):
                if inv.get("Status", "").upper() == "PENDING":
                    invitations.append(inv)

            next_token = response.get("NextToken")
            if not next_token:
                break

        return invitations


    def _process_invitation(self, invitation_id: str) -> dict | None:
        """
        Accept one Partner Central invitation via StartEngagementByAcceptingInvitationTask,
        then create a HubSpot deal from the opportunity.
        """
        # Validate invitation ID
        try:
            invitation_id = validate_partner_central_id(invitation_id, "Invitation ID")
        except ValueError as e:
            self.logger.error(f"Invalid invitation ID: {e}")
            raise
        
        # -----------------------------------------------------------------------
        # 1. Deduplicate: skip if already synced
        # -----------------------------------------------------------------------
        existing = self.hubspot_client.search_deals_by_aws_invitation_id(invitation_id)
        if existing:
            self.logger.info("Invitation %s already synced (deal %s) — skipping",
                        invitation_id, existing[0].get("id"))
            return None

        # -----------------------------------------------------------------------
        # 2. Fetch invitation details (for logging / opportunity ID extraction)
        # -----------------------------------------------------------------------
        self.logger.info("Fetching invitation details: %s", invitation_id)
        inv_detail = self.pc_client.get_engagement_invitation(
            Catalog=PARTNER_CENTRAL_CATALOG,
            Identifier=invitation_id,
        )
        self.logger.info("Invitation payload type: %s",
                    inv_detail.get("PayloadType", "unknown"))

        # -----------------------------------------------------------------------
        # 3. Accept via StartEngagementByAcceptingInvitationTask
        #    (this is the correct API — AcceptEngagementInvitation does NOT exist)
        # -----------------------------------------------------------------------
        client_token = f"hs-accept-{uuid.uuid4()}"
        self.logger.info("Accepting invitation %s via StartEngagementByAcceptingInvitationTask", invitation_id)

        task_response = self.pc_client.start_engagement_by_accepting_invitation_task(
            Catalog=PARTNER_CENTRAL_CATALOG,
            Identifier=invitation_id,
            ClientToken=client_token,
        )

        task_id = task_response.get("TaskId") or task_response.get("Id")
        opportunity_id = task_response.get("OpportunityId")
        task_status = task_response.get("TaskStatus", "")

        self.logger.info("Task started: id=%s status=%s opportunity=%s",
                    task_id, task_status, opportunity_id)

        # -----------------------------------------------------------------------
        # 4. Poll task to completion (some tasks complete synchronously)
        # -----------------------------------------------------------------------
        if task_id and task_status not in ("COMPLETE", "FAILED"):
            task_status, opportunity_id = self._poll_task(task_id, opportunity_id)

        if task_status == "FAILED":
            raise RuntimeError(
                f"StartEngagementByAcceptingInvitationTask failed for invitation {invitation_id}. "
                f"Check CloudWatch logs for task {task_id}."
            )

        self.logger.info("Invitation %s accepted. Opportunity ID: %s", invitation_id, opportunity_id)

        # -----------------------------------------------------------------------
        # 5. Fetch full opportunity details
        # -----------------------------------------------------------------------
        if not opportunity_id:
            self.logger.warning("No opportunity ID from task response for invitation %s", invitation_id)
            return {
                "invitationId": invitation_id,
                "status": "accepted_no_opportunity",
            }

        opportunity = self.pc_client.get_opportunity(
            Catalog=PARTNER_CENTRAL_CATALOG,
            Identifier=opportunity_id,
        )
        self.logger.info("Fetched opportunity %s: '%s'",
                    opportunity_id,
                    opportunity.get("Project", {}).get("Title", ""))

        # -----------------------------------------------------------------------
        # 6. Create HubSpot deal
        # -----------------------------------------------------------------------
        hs_properties = partner_central_opportunity_to_hubspot(
            opportunity, invitation_id=invitation_id
        )
        deal = self.hubspot_client.create_deal(hs_properties)

        self.logger.info(
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


    def _poll_task(self, task_id: str, current_opportunity_id: str | None) -> tuple[str, str | None]:
        """
        Poll a Partner Central async task until it reaches a terminal state.
        Returns (final_status, opportunity_id).
        """
        opportunity_id = current_opportunity_id

        for attempt in range(TASK_MAX_ATTEMPTS):
            time.sleep(TASK_POLL_INTERVAL * (attempt + 1))  # simple backoff

            try:
                task = self.pc_client.get_engagement_by_accepting_invitation_task(
                    Catalog=PARTNER_CENTRAL_CATALOG,
                    TaskIdentifier=task_id,
                )
            except Exception as exc:
                self.logger.warning("Error polling task %s (attempt %d): %s", task_id, attempt + 1, exc)
                continue

            status = task.get("TaskStatus", "")
            opportunity_id = task.get("OpportunityId") or opportunity_id
            self.logger.info("Task %s status: %s (attempt %d)", task_id, status, attempt + 1)

            if status in ("COMPLETE", "FAILED"):
                return status, opportunity_id

        self.logger.warning("Task %s did not complete within %d attempts", task_id, TASK_MAX_ATTEMPTS)
        return "UNKNOWN", opportunity_id


# Lambda entry point
def lambda_handler(event: dict, context: dict) -> dict:
    """
    Lambda entry point for Partner Central to HubSpot handler.

    Args:
        event: EventBridge scheduled event
        context: Lambda context

    Returns:
        HTTP response with status and details
    """
    handler = PartnerCentralToHubSpotHandler()
    return handler.handle(event, context)
