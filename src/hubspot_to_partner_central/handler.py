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

import os
import uuid

from common.base_handler import BaseLambdaHandler
from common.aws_client import PARTNER_CENTRAL_CATALOG
from common.mappers import (
    hubspot_deal_to_partner_central,
    hubspot_deal_to_partner_central_update,
)
from common.solution_matcher import (
    match_solutions,
    associate_multiple_solutions,
    get_cached_solutions,
)

AWS_TRIGGER_TAG = "#AWS"

# HubSpot custom property that holds the PC title to detect renames
PC_TITLE_PROPERTY = "aws_opportunity_title"


class HubSpotToPartnerCentralHandler(BaseLambdaHandler):
    """
    Handles HubSpot webhooks for deal creation and property changes.

    Syncs HubSpot deals to AWS Partner Central opportunities.
    """

    def _execute(self, event: dict, context: dict) -> dict:
        webhook_events = self._parse_webhook_body(event)
        self._verify_signature(event)

        processed = []
        errors = []

        for webhook_event in webhook_events:
            event_type = webhook_event.get("subscriptionType", "")
            object_id = str(webhook_event.get("objectId", ""))

            try:
                if "deal.creation" in event_type:
                    result = self._handle_deal_creation(object_id)
                elif "deal.propertyChange" in event_type:
                    result = self._handle_deal_update(object_id, webhook_event)
                else:
                    self.logger.debug("Skipping unhandled event type: %s", event_type)
                    result = None

                if result:
                    processed.append(result)

            except Exception as exc:
                self.logger.exception("Error processing %s for deal %s: %s", event_type, object_id, exc)
                errors.append({"dealId": object_id, "eventType": event_type, "error": str(exc)})

        return self._success_response(
            {
                "processed": len(processed),
                "skipped": len(webhook_events) - len(processed) - len(errors),
                "errors": len(errors),
                "results": processed,
                "errorDetails": errors,
            }
        )


    # ---------------------------------------------------------------------------
    # deal.creation handler
    # ---------------------------------------------------------------------------

    def _handle_deal_creation(self, deal_id: str) -> dict | None:
        """
        Fetch the HubSpot deal, check for #AWS tag, and create a Partner Central
        Opportunity. On success, associates the configured PC solution and writes
        the Opportunity ID + title back to HubSpot.
        """
        deal, company, contacts = self.hubspot_client.get_deal_with_associations(deal_id)
        deal_name = deal.get("properties", {}).get("dealname", "")

        self.logger.info("Processing deal creation %s: '%s'", deal_id, deal_name)

        if AWS_TRIGGER_TAG.lower() not in deal_name.lower():
            self.logger.info("Deal '%s' does not contain %s — skipping", deal_name, AWS_TRIGGER_TAG)
            return None

        # Idempotency: skip if already synced
        existing_pc_id = deal.get("properties", {}).get("aws_opportunity_id")
        if existing_pc_id:
            self.logger.info("Deal %s already synced to PC opportunity %s", deal_id, existing_pc_id)
            return None

        # Build and submit the payload
        pc_payload = hubspot_deal_to_partner_central(deal, company, contacts)
        self.logger.info("Creating PC opportunity for deal %s with payload keys: %s",
                    deal_id, list(pc_payload.keys()))

        pc_response = self.pc_client.create_opportunity(
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
        self.logger.info("Created PC opportunity %s for deal %s", opportunity_id, deal_id)

        # Associate solutions (single from env var, or multiple via auto-matching)
        solution_id = os.environ.get("PARTNER_CENTRAL_SOLUTION_ID")
        associated_solutions = []
        
        if solution_id:
            # Single solution from environment variable (backward compatibility)
            self._associate_solution(opportunity_id, solution_id)
            associated_solutions = [solution_id]
        else:
            # Multi-solution auto-matching
            try:
                available_solutions = get_cached_solutions(self.pc_client)
                matched_solution_ids = match_solutions(deal, available_solutions)
                
                if matched_solution_ids:
                    result = associate_multiple_solutions(self.pc_client, opportunity_id, matched_solution_ids)
                    associated_solutions = result["succeeded"]
                    self.logger.info("Associated %d solutions with opportunity %s",
                               len(associated_solutions), opportunity_id)
                else:
                    self.logger.warning("No solutions matched for deal %s", deal_id)
            except Exception as exc:
                self.logger.warning("Solution auto-matching failed: %s", exc)

        if not associated_solutions:
            self.logger.warning(
                "No solutions associated with opportunity %s. "
                "Set PARTNER_CENTRAL_SOLUTION_ID or add aws_solution_ids to the deal.",
                opportunity_id
            )

        # Write the PC Opportunity ID and canonical title back to HubSpot
        self.hubspot_client.update_deal(
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
            "solutionsAssociated": len(associated_solutions),
            "solutionIds": associated_solutions,
        }


    # ---------------------------------------------------------------------------
    # deal.propertyChange handler
    # ---------------------------------------------------------------------------

    def _handle_deal_update(self, deal_id: str, webhook_event: dict) -> dict | None:
        """
        Sync a HubSpot deal property change to Partner Central.

        Handles the title-immutability rule:
          - If the changed property is 'dealname', the title is NOT sent to PC.
          - A note is added to the HubSpot deal to inform the sales rep.
        """
        deal, company, contacts = self.hubspot_client.get_deal_with_associations(deal_id)
        props = deal.get("properties", {})

        # Only sync deals that are already in PC
        opportunity_id = props.get("aws_opportunity_id")
        if not opportunity_id:
            self.logger.debug("Deal %s has no aws_opportunity_id — skipping update", deal_id)
            return None

        # The deal must still have #AWS to stay in sync scope
        deal_name = props.get("dealname", "")
        if AWS_TRIGGER_TAG.lower() not in deal_name.lower():
            self.logger.info("Deal %s no longer contains %s — skipping update", deal_id, AWS_TRIGGER_TAG)
            return None

        changed_property = webhook_event.get("propertyName", "")
        changed_properties = {changed_property} if changed_property else set()

        # Fetch current PC state to check review status and title
        current_pc_opp = self._get_pc_opportunity(opportunity_id)
        if not current_pc_opp:
            self.logger.warning("Could not fetch PC opportunity %s — skipping update", opportunity_id)
            return None

        # Build the update payload (mappers handles all blocking/stripping logic)
        update_payload, warnings = hubspot_deal_to_partner_central_update(
            deal, current_pc_opp, company, contacts, changed_properties
        )

        # Surface warnings back to HubSpot as notes
        for warning in warnings:
            self.logger.warning("Deal %s: %s", deal_id, warning)
            if "title cannot be changed" in warning.lower() or "immutable" in warning.lower():
                try:
                    self.hubspot_client.add_note_to_deal(
                        deal_id,
                        f"🔒 AWS Partner Central — Title Change Blocked\n\n{warning}"
                    )
                    # Revert the display title in HubSpot to the canonical PC title
                    canonical_title = current_pc_opp.get("Project", {}).get("Title", "")
                    if canonical_title:
                        # Append #AWS if missing
                        display_title = canonical_title if "#AWS" in canonical_title else f"{canonical_title} #AWS"
                        self.hubspot_client.update_deal(deal_id, {
                            "dealname": display_title,
                            "aws_opportunity_title": canonical_title,
                        })
                except Exception as note_exc:
                    self.logger.warning("Could not add note to deal %s: %s", deal_id, note_exc)

        if update_payload is None:
            return {
                "action": "blocked",
                "hubspotDealId": deal_id,
                "partnerCentralOpportunityId": opportunity_id,
                "warnings": warnings,
            }

        # Send the update
        self.pc_client.update_opportunity(**update_payload)
        self.logger.info("Updated PC opportunity %s from deal %s", opportunity_id, deal_id)

        self.hubspot_client.update_deal(deal_id, {"aws_sync_status": "synced"})

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

    def _associate_solution(self, opportunity_id: str, solution_id: str) -> None:
        """Associate the partner's PC solution with the opportunity."""
        try:
            self.pc_client.associate_opportunity(
                Catalog=PARTNER_CENTRAL_CATALOG,
                OpportunityIdentifier=opportunity_id,
                RelatedEntityIdentifier=solution_id,
                RelatedEntityType="Solutions",
            )
            self.logger.info("Associated solution %s with opportunity %s", solution_id, opportunity_id)
        except Exception as exc:
            self.logger.warning("Could not associate solution %s with %s: %s", solution_id, opportunity_id, exc)

    def _get_pc_opportunity(self, opportunity_id: str) -> dict | None:
        """Fetch a Partner Central opportunity; return None on failure."""
        try:
            return self.pc_client.get_opportunity(
                Catalog=PARTNER_CENTRAL_CATALOG,
                Identifier=opportunity_id,
            )
        except Exception as exc:
            self.logger.error("Failed to fetch PC opportunity %s: %s", opportunity_id, exc)
            return None

    def _verify_signature(self, event: dict) -> None:
        """Verify HubSpot webhook signature if secret is configured."""
        secret = os.environ.get("HUBSPOT_WEBHOOK_SECRET")
        if not secret:
            return
        headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
        signature = headers.get("x-hubspot-signature-v3", "")
        if not signature:
            self.logger.warning("Missing HubSpot signature header — proceeding without verification")
            return
        body = (event.get("body") or "").encode("utf-8")
        if not self.hubspot_client.verify_webhook_signature(body, signature, secret):
            raise ValueError("Invalid HubSpot webhook signature")


# Lambda entry point
def lambda_handler(event: dict, context: dict) -> dict:
    """
    Lambda entry point for HubSpot to Partner Central handler.

    Args:
        event: API Gateway event with webhook payload
        context: Lambda context

    Returns:
        HTTP response with status and details
    """
    handler = HubSpotToPartnerCentralHandler()
    return handler.handle(event, context)
