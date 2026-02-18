"""HubSpot to AWS sync processing logic"""
import logging
import os
from typing import Dict, Any, Tuple

from common.events import SyncEvent, EventType
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

logger = logging.getLogger(__name__)

AWS_TRIGGER_TAG = "#AWS"


def process_hubspot_event(
    sync_event: SyncEvent, hubspot_client, pc_client
) -> Dict[str, Any]:
    """
    Process HubSpot webhook event (deal creation/update).

    This is the core business logic extracted from the existing handler.
    """
    event_type = sync_event.event_type
    deal_id = sync_event.object_id

    logger.info(f"Processing {event_type} for deal {deal_id}")

    if event_type == EventType.DEAL_CREATION:
        return _handle_deal_creation(deal_id, hubspot_client, pc_client)
    elif event_type == EventType.DEAL_UPDATE:
        return _handle_deal_update(
            deal_id, sync_event.webhook_payload, hubspot_client, pc_client
        )
    else:
        return {"status": "skipped", "reason": "unsupported_event_type"}


def _handle_deal_creation(
    deal_id: str, hubspot_client, pc_client
) -> Dict[str, Any]:
    """
    Handle deal creation event.

    Fetch the HubSpot deal, check for #AWS tag, and create a Partner Central
    Opportunity. On success, associates the configured PC solution and writes
    the Opportunity ID + title back to HubSpot.
    """
    deal, company, contacts = hubspot_client.get_deal_with_associations(deal_id)
    deal_name = deal.get("properties", {}).get("dealname", "")

    logger.info("Processing deal creation %s: '%s'", deal_id, deal_name)

    if AWS_TRIGGER_TAG.lower() not in deal_name.lower():
        logger.info(
            "Deal '%s' does not contain %s — skipping", deal_name, AWS_TRIGGER_TAG
        )
        return {"status": "skipped", "reason": "no_aws_tag"}

    # Idempotency: skip if already synced
    existing_pc_id = deal.get("properties", {}).get("aws_opportunity_id")
    if existing_pc_id:
        logger.info(
            "Deal %s already synced to PC opportunity %s", deal_id, existing_pc_id
        )
        return {
            "status": "skipped",
            "reason": "already_synced",
            "opportunity_id": existing_pc_id,
        }

    # Build and submit the payload
    pc_payload = hubspot_deal_to_partner_central(deal, company, contacts)
    logger.info(
        "Creating PC opportunity for deal %s with payload keys: %s",
        deal_id,
        list(pc_payload.keys()),
    )

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

    # Associate solutions (single from env var, or multiple via auto-matching)
    solution_id = os.environ.get("PARTNER_CENTRAL_SOLUTION_ID")
    associated_solutions = []

    if solution_id:
        # Single solution from environment variable (backward compatibility)
        _associate_solution(pc_client, opportunity_id, solution_id)
        associated_solutions = [solution_id]
    else:
        # Multi-solution auto-matching
        try:
            available_solutions = get_cached_solutions(pc_client)
            matched_solution_ids = match_solutions(deal, available_solutions)

            if matched_solution_ids:
                result = associate_multiple_solutions(
                    pc_client, opportunity_id, matched_solution_ids
                )
                associated_solutions = result["succeeded"]
                logger.info(
                    "Associated %d solutions with opportunity %s",
                    len(associated_solutions),
                    opportunity_id,
                )
            else:
                logger.warning("No solutions matched for deal %s", deal_id)
        except Exception as exc:
            logger.warning("Solution auto-matching failed: %s", exc)

    if not associated_solutions:
        logger.warning(
            "No solutions associated with opportunity %s. "
            "Set PARTNER_CENTRAL_SOLUTION_ID or add aws_solution_ids to the deal.",
            opportunity_id,
        )

    # Write the PC Opportunity ID and canonical title back to HubSpot
    hubspot_client.update_deal(
        deal_id,
        {
            "aws_opportunity_id": opportunity_id,
            "aws_opportunity_title": pc_payload["Project"]["Title"],
            "aws_sync_status": "synced",
            "aws_review_status": "Pending Submission",
        },
    )

    return {
        "status": "created",
        "deal_id": deal_id,
        "deal_name": deal_name,
        "opportunity_id": opportunity_id,
        "solutions_associated": len(associated_solutions),
        "solution_ids": associated_solutions,
    }


def _handle_deal_update(
    deal_id: str, webhook_event: dict, hubspot_client, pc_client
) -> Dict[str, Any]:
    """
    Sync a HubSpot deal property change to Partner Central.

    Handles the title-immutability rule:
      - If the changed property is 'dealname', the title is NOT sent to PC.
      - A note is added to the HubSpot deal to inform the sales rep.
    """
    deal, company, contacts = hubspot_client.get_deal_with_associations(deal_id)
    props = deal.get("properties", {})

    # Only sync deals that are already in PC
    opportunity_id = props.get("aws_opportunity_id")
    if not opportunity_id:
        logger.debug("Deal %s has no aws_opportunity_id — skipping update", deal_id)
        return {"status": "skipped", "reason": "no_opportunity_id"}

    # The deal must still have #AWS to stay in sync scope
    deal_name = props.get("dealname", "")
    if AWS_TRIGGER_TAG.lower() not in deal_name.lower():
        logger.info(
            "Deal %s no longer contains %s — skipping update",
            deal_id,
            AWS_TRIGGER_TAG,
        )
        return {"status": "skipped", "reason": "no_aws_tag"}

    changed_property = webhook_event.get("propertyName", "")
    changed_properties = {changed_property} if changed_property else set()

    # Fetch current PC state to check review status and title
    current_pc_opp = _get_pc_opportunity(pc_client, opportunity_id)
    if not current_pc_opp:
        logger.warning(
            "Could not fetch PC opportunity %s — skipping update", opportunity_id
        )
        return {"status": "error", "reason": "failed_to_fetch_opportunity"}

    # Build the update payload (mappers handles all blocking/stripping logic)
    update_payload, warnings = hubspot_deal_to_partner_central_update(
        deal, current_pc_opp, company, contacts, changed_properties
    )

    # Surface warnings back to HubSpot as notes
    for warning in warnings:
        logger.warning("Deal %s: %s", deal_id, warning)
        if (
            "title cannot be changed" in warning.lower()
            or "immutable" in warning.lower()
        ):
            try:
                hubspot_client.add_note_to_deal(
                    deal_id,
                    f"🔒 AWS Partner Central — Title Change Blocked\n\n{warning}",
                )
                # Revert the display title in HubSpot to the canonical PC title
                canonical_title = current_pc_opp.get("Project", {}).get("Title", "")
                if canonical_title:
                    # Append #AWS if missing
                    display_title = (
                        canonical_title
                        if "#AWS" in canonical_title
                        else f"{canonical_title} #AWS"
                    )
                    hubspot_client.update_deal(
                        deal_id,
                        {
                            "dealname": display_title,
                            "aws_opportunity_title": canonical_title,
                        },
                    )
            except Exception as note_exc:
                logger.warning("Could not add note to deal %s: %s", deal_id, note_exc)

    if update_payload is None:
        return {
            "status": "blocked",
            "deal_id": deal_id,
            "opportunity_id": opportunity_id,
            "warnings": warnings,
        }

    # Send the update
    pc_client.update_opportunity(**update_payload)
    logger.info("Updated PC opportunity %s from deal %s", opportunity_id, deal_id)

    hubspot_client.update_deal(deal_id, {"aws_sync_status": "synced"})

    return {
        "status": "updated",
        "deal_id": deal_id,
        "opportunity_id": opportunity_id,
        "changed_property": changed_property,
        "warnings": warnings,
    }


def _associate_solution(pc_client, opportunity_id: str, solution_id: str) -> None:
    """Associate the partner's PC solution with the opportunity."""
    try:
        pc_client.associate_opportunity(
            Catalog=PARTNER_CENTRAL_CATALOG,
            OpportunityIdentifier=opportunity_id,
            RelatedEntityIdentifier=solution_id,
            RelatedEntityType="Solutions",
        )
        logger.info(
            "Associated solution %s with opportunity %s", solution_id, opportunity_id
        )
    except Exception as exc:
        logger.warning(
            "Could not associate solution %s with %s: %s",
            solution_id,
            opportunity_id,
            exc,
        )


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
