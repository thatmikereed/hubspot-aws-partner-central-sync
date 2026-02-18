"""
Lambda handler: Submit Opportunity to AWS

Triggered manually (via API Gateway endpoint) or automatically when a deal
reaches a configured stage (e.g., "presentationscheduled").

Validates that the opportunity has all required fields, then calls
StartEngagementFromOpportunityTask to submit it to AWS for co-sell review.

Updates HubSpot with submission status and adds a note to the deal.
"""

import json
import os
import time
from datetime import datetime, timezone

from common.base_handler import BaseLambdaHandler
from common.aws_client import PARTNER_CENTRAL_CATALOG

# Configurable: default AWS submission parameters
DEFAULT_INVOLVEMENT_TYPE = os.environ.get("DEFAULT_INVOLVEMENT_TYPE", "Co-Sell")
DEFAULT_VISIBILITY = os.environ.get("DEFAULT_VISIBILITY", "Full")


class SubmitOpportunityHandler(BaseLambdaHandler):
    """Handler for submitting opportunities to AWS Partner Central."""

    def _execute(self, event: dict, context: dict) -> dict:
        """
        Entry point for opportunity submission.

        Expected event structure:
        {
            "dealId": "12345",  # HubSpot deal ID
            "involvementType": "Co-Sell",  # optional override
            "visibility": "Full"  # optional override
        }
        """
        # Parse input
        if "body" in event:  # API Gateway format
            body = (
                json.loads(event["body"])
                if isinstance(event["body"], str)
                else event["body"]
            )
        else:  # Direct invocation
            body = event

        deal_id = body.get("dealId")
        if not deal_id:
            return self._error_response("dealId is required", 400)

        involvement_type = body.get("involvementType", DEFAULT_INVOLVEMENT_TYPE)
        visibility = body.get("visibility", DEFAULT_VISIBILITY)

        # Validate involvement type
        if involvement_type not in ["Co-Sell", "For Visibility Only"]:
            return self._error_response(
                f"Invalid involvementType: {involvement_type}", 400
            )

        # Validate visibility
        if visibility not in ["Full", "Limited"]:
            return self._error_response(f"Invalid visibility: {visibility}", 400)

        # Submit the opportunity
        result = self._submit_opportunity(deal_id, involvement_type, visibility)

        return self._success_response(result)

    def _submit_opportunity(
        self, deal_id: str, involvement_type: str, visibility: str
    ) -> dict:
        """
        Validate and submit an opportunity to AWS Partner Central.
        """
        # Fetch the HubSpot deal
        deal, _, _ = self.hubspot_client.get_deal_with_associations(deal_id)
        props = deal.get("properties", {})

        opportunity_id = props.get("aws_opportunity_id")
        if not opportunity_id:
            raise ValueError(
                f"Deal {deal_id} has no aws_opportunity_id - create opportunity first"
            )

        # Check if already submitted
        review_status = props.get("aws_review_status", "")
        if review_status in ["Submitted", "In Review", "Approved", "Action Required"]:
            return {
                "status": "already_submitted",
                "message": f"Opportunity already in state: {review_status}",
                "dealId": deal_id,
                "opportunityId": opportunity_id,
            }

        # Fetch current PC opportunity state
        opportunity = self.pc_client.get_opportunity(
            Catalog=PARTNER_CENTRAL_CATALOG,
            Identifier=opportunity_id,
        )

        # Validate readiness
        validation_errors = self._validate_submission_ready(opportunity)
        if validation_errors:
            # Add note to HubSpot about validation failures
            note = "❌ Submission Validation Failed\n\n" + "\n".join(
                f"• {err}" for err in validation_errors
            )
            self.hubspot_client.add_note_to_deal(deal_id, note)

            return {
                "status": "validation_failed",
                "errors": validation_errors,
                "dealId": deal_id,
                "opportunityId": opportunity_id,
            }

        # Submit via StartEngagementFromOpportunityTask
        self.logger.info(
            "Submitting opportunity %s (involvement=%s, visibility=%s)",
            opportunity_id,
            involvement_type,
            visibility,
        )

        client_token = f"submit-{deal_id}-{int(time.time())}"

        task_response = self.pc_client.start_engagement_from_opportunity_task(
            Catalog=PARTNER_CENTRAL_CATALOG,
            Identifier=opportunity_id,
            ClientToken=client_token,
            AwsSubmission={
                "InvolvementType": involvement_type,
                "Visibility": visibility,
            },
        )

        task_id = task_response.get("TaskId") or task_response.get("Id")
        task_status = task_response.get("TaskStatus", "IN_PROGRESS")

        self.logger.info(
            "Submission task started: id=%s status=%s", task_id, task_status
        )

        # Poll task to completion (with timeout)
        if task_id and task_status not in ["COMPLETE", "FAILED"]:
            task_status = self._poll_task(task_id)

        if task_status == "FAILED":
            error_msg = "Submission task failed - check CloudWatch logs for details"
            self.hubspot_client.add_note_to_deal(
                deal_id, f"❌ AWS Submission Failed\n\n{error_msg}"
            )
            raise RuntimeError(error_msg)

        # Update HubSpot with success
        submission_date = datetime.now(timezone.utc).isoformat()

        self.hubspot_client.update_deal(
            deal_id,
            {
                "aws_review_status": "Submitted",
                "aws_submission_date": submission_date,
                "aws_involvement_type": involvement_type,
                "aws_visibility": visibility,
            },
        )

        # Add success note
        note = (
            f"✅ Submitted to AWS Partner Central\n\n"
            f"Involvement Type: {involvement_type}\n"
            f"Visibility: {visibility}\n"
            f"Submitted: {submission_date}\n\n"
            f"AWS will review and provide feedback within 1-2 business days."
        )
        self.hubspot_client.add_note_to_deal(deal_id, note)

        self.logger.info(
            "Successfully submitted opportunity %s for deal %s", opportunity_id, deal_id
        )

        return {
            "status": "submitted",
            "dealId": deal_id,
            "opportunityId": opportunity_id,
            "taskId": task_id,
            "involvementType": involvement_type,
            "visibility": visibility,
            "submissionDate": submission_date,
        }

    def _validate_submission_ready(self, opportunity: dict) -> list[str]:
        """
        Validate that an opportunity has all required fields for submission.
        Returns a list of validation error messages (empty if valid).
        """
        errors = []

        # Check Customer
        customer = opportunity.get("Customer", {})
        account = customer.get("Account", {})

        if not account.get("CompanyName"):
            errors.append("Customer.Account.CompanyName is required")
        if not account.get("Industry"):
            errors.append("Customer.Account.Industry is required")
        if not account.get("WebsiteUrl"):
            errors.append("Customer.Account.WebsiteUrl is required")

        # Check Project
        project = opportunity.get("Project", {})

        if not project.get("Title"):
            errors.append("Project.Title is required")

        business_problem = project.get("CustomerBusinessProblem", "")
        if len(business_problem) < 20:
            errors.append(
                f"Project.CustomerBusinessProblem too short ({len(business_problem)} chars, need 20+)"
            )

        if not project.get("DeliveryModels"):
            errors.append("Project.DeliveryModels is required")

        # Check LifeCycle
        lifecycle = opportunity.get("LifeCycle", {})

        if not lifecycle.get("Stage"):
            errors.append("LifeCycle.Stage is required")
        if not lifecycle.get("TargetCloseDate"):
            errors.append("LifeCycle.TargetCloseDate is required")

        # Check at least one solution is associated
        # Note: This requires a separate ListOpportunitySolutions call, which we skip for performance
        # Consider adding if submission failures occur due to missing solutions

        return errors

    def _poll_task(self, task_id: str, max_attempts: int = 10) -> str:
        """
        Poll a Partner Central async task until it reaches a terminal state.
        Returns final task status.
        """
        for attempt in range(max_attempts):
            time.sleep(2 * (attempt + 1))  # Exponential backoff

            try:
                task = self.pc_client.get_engagement_from_opportunity_task(
                    Catalog=PARTNER_CENTRAL_CATALOG,
                    TaskIdentifier=task_id,
                )
                status = task.get("TaskStatus", "")
                self.logger.info(
                    "Task %s status: %s (attempt %d)", task_id, status, attempt + 1
                )

                if status in ("COMPLETE", "FAILED"):
                    return status
            except Exception as exc:
                self.logger.warning("Error polling task %s: %s", task_id, exc)

        self.logger.warning(
            "Task %s did not complete within %d attempts", task_id, max_attempts
        )
        return "UNKNOWN"


def lambda_handler(event: dict, context) -> dict:
    """Lambda entry point."""
    handler = SubmitOpportunityHandler()
    return handler.handle(event, context)
