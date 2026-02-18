"""
Lambda handler: Submit Opportunity to AWS

Triggered manually (via API Gateway endpoint) or automatically when a deal
reaches a configured stage (e.g., "presentationscheduled").

Validates that the opportunity has all required fields, then calls
StartEngagementFromOpportunityTask to submit it to AWS for co-sell review.

Updates HubSpot with submission status and adds a note to the deal.
"""

import json
import logging
import os
import sys
import time

sys.path.insert(0, "/var/task")

from common.aws_client import get_partner_central_client, PARTNER_CENTRAL_CATALOG
from common.hubspot_client import HubSpotClient

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Configurable: default AWS submission parameters
DEFAULT_INVOLVEMENT_TYPE = os.environ.get("DEFAULT_INVOLVEMENT_TYPE", "Co-Sell")
DEFAULT_VISIBILITY = os.environ.get("DEFAULT_VISIBILITY", "Full")


def lambda_handler(event: dict, context) -> dict:
    """
    Entry point for opportunity submission.
    
    Expected event structure:
    {
        "dealId": "12345",  # HubSpot deal ID
        "involvementType": "Co-Sell",  # optional override
        "visibility": "Full"  # optional override
    }
    """
    logger.info("Received submission request: %s", json.dumps(event, default=str))
    
    try:
        # Parse input
        if "body" in event:  # API Gateway format
            body = json.loads(event["body"]) if isinstance(event["body"], str) else event["body"]
        else:  # Direct invocation
            body = event
        
        deal_id = body.get("dealId")
        if not deal_id:
            return _response(400, {"error": "dealId is required"})
        
        involvement_type = body.get("involvementType", DEFAULT_INVOLVEMENT_TYPE)
        visibility = body.get("visibility", DEFAULT_VISIBILITY)
        
        # Validate involvement type
        if involvement_type not in ["Co-Sell", "For Visibility Only"]:
            return _response(400, {"error": f"Invalid involvementType: {involvement_type}"})
        
        # Validate visibility
        if visibility not in ["Full", "Limited"]:
            return _response(400, {"error": f"Invalid visibility: {visibility}"})
        
        # Submit the opportunity
        hubspot = HubSpotClient()
        pc_client = get_partner_central_client()
        
        result = _submit_opportunity(deal_id, involvement_type, visibility, hubspot, pc_client)
        
        return _response(200, result)
        
    except Exception as exc:
        logger.exception("Error submitting opportunity: %s", exc)
        return _response(500, {"error": str(exc)})


def _submit_opportunity(
    deal_id: str,
    involvement_type: str,
    visibility: str,
    hubspot: HubSpotClient,
    pc_client
) -> dict:
    """
    Validate and submit an opportunity to AWS Partner Central.
    """
    # Fetch the HubSpot deal
    deal, _, _ = hubspot.get_deal_with_associations(deal_id)
    props = deal.get("properties", {})
    
    opportunity_id = props.get("aws_opportunity_id")
    if not opportunity_id:
        raise ValueError(f"Deal {deal_id} has no aws_opportunity_id - create opportunity first")
    
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
    opportunity = pc_client.get_opportunity(
        Catalog=PARTNER_CENTRAL_CATALOG,
        Identifier=opportunity_id,
    )
    
    # Validate readiness
    validation_errors = _validate_submission_ready(opportunity)
    if validation_errors:
        # Add note to HubSpot about validation failures
        note = "❌ Submission Validation Failed\n\n" + "\n".join(f"• {err}" for err in validation_errors)
        hubspot.add_note_to_deal(deal_id, note)
        
        return {
            "status": "validation_failed",
            "errors": validation_errors,
            "dealId": deal_id,
            "opportunityId": opportunity_id,
        }
    
    # Submit via StartEngagementFromOpportunityTask
    logger.info("Submitting opportunity %s (involvement=%s, visibility=%s)",
                opportunity_id, involvement_type, visibility)
    
    client_token = f"submit-{deal_id}-{int(time.time())}"
    
    task_response = pc_client.start_engagement_from_opportunity_task(
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
    
    logger.info("Submission task started: id=%s status=%s", task_id, task_status)
    
    # Poll task to completion (with timeout)
    if task_id and task_status not in ["COMPLETE", "FAILED"]:
        task_status = _poll_task(pc_client, task_id)
    
    if task_status == "FAILED":
        error_msg = "Submission task failed - check CloudWatch logs for details"
        hubspot.add_note_to_deal(deal_id, f"❌ AWS Submission Failed\n\n{error_msg}")
        raise RuntimeError(error_msg)
    
    # Update HubSpot with success
    from datetime import datetime, timezone
    submission_date = datetime.now(timezone.utc).isoformat()
    
    hubspot.update_deal(deal_id, {
        "aws_review_status": "Submitted",
        "aws_submission_date": submission_date,
        "aws_involvement_type": involvement_type,
        "aws_visibility": visibility,
    })
    
    # Add success note
    note = (
        f"✅ Submitted to AWS Partner Central\n\n"
        f"Involvement Type: {involvement_type}\n"
        f"Visibility: {visibility}\n"
        f"Submitted: {submission_date}\n\n"
        f"AWS will review and provide feedback within 1-2 business days."
    )
    hubspot.add_note_to_deal(deal_id, note)
    
    logger.info("Successfully submitted opportunity %s for deal %s", opportunity_id, deal_id)
    
    return {
        "status": "submitted",
        "dealId": deal_id,
        "opportunityId": opportunity_id,
        "taskId": task_id,
        "involvementType": involvement_type,
        "visibility": visibility,
        "submissionDate": submission_date,
    }


def _validate_submission_ready(opportunity: dict) -> list[str]:
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
        errors.append(f"Project.CustomerBusinessProblem too short ({len(business_problem)} chars, need 20+)")
    
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


def _poll_task(pc_client, task_id: str, max_attempts: int = 10) -> str:
    """
    Poll a Partner Central async task until it reaches a terminal state.
    Returns final task status.
    """
    for attempt in range(max_attempts):
        time.sleep(2 * (attempt + 1))  # Exponential backoff
        
        try:
            task = pc_client.get_engagement_from_opportunity_task(
                Catalog=PARTNER_CENTRAL_CATALOG,
                TaskIdentifier=task_id,
            )
            status = task.get("TaskStatus", "")
            logger.info("Task %s status: %s (attempt %d)", task_id, status, attempt + 1)
            
            if status in ("COMPLETE", "FAILED"):
                return status
        except Exception as exc:
            logger.warning("Error polling task %s: %s", task_id, exc)
    
    logger.warning("Task %s did not complete within %d attempts", task_id, max_attempts)
    return "UNKNOWN"


def _response(status_code: int, body: dict) -> dict:
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body, default=str),
    }
