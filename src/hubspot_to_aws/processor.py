"""
HubSpot to AWS Partner Central processor module.

Extracted business logic for processing HubSpot deal events and syncing to AWS Partner Central.
This module is called by the event processor and can also be used by the existing handlers.
"""

import os
from logging import Logger
from typing import Any, Dict, Optional

from common.aws_client import PARTNER_CENTRAL_CATALOG
from common.events import SyncEvent
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
PC_TITLE_PROPERTY = "aws_opportunity_title"


def process_hubspot_deal_creation(
    sync_event: SyncEvent,
    hubspot_client: Any,
    pc_client: Any,
    logger: Logger,
) -> Dict[str, Any]:
    """
    Process HubSpot deal creation event.
    
    Args:
        sync_event: SyncEvent with deal creation data
        hubspot_client: HubSpot API client
        pc_client: Partner Central API client
        logger: Logger instance
        
    Returns:
        Processing result dict
    """
    deal_id = sync_event.object_id
    
    # Fetch deal with associations
    deal, company, contacts = hubspot_client.get_deal_with_associations(deal_id)
    deal_name = deal.get("properties", {}).get("dealname", "")
    
    logger.info(f"Processing deal creation {deal_id}: '{deal_name}'")
    
    # Check for AWS trigger tag
    if AWS_TRIGGER_TAG.lower() not in deal_name.lower():
        logger.info(f"Deal '{deal_name}' does not contain {AWS_TRIGGER_TAG} — skipping")
        return {
            "action": "skipped",
            "reason": "no_aws_tag",
            "dealId": deal_id,
            "dealName": deal_name,
        }
    
    # Idempotency: skip if already synced
    existing_pc_id = deal.get("properties", {}).get("aws_opportunity_id")
    if existing_pc_id:
        logger.info(f"Deal {deal_id} already synced to PC opportunity {existing_pc_id}")
        return {
            "action": "skipped",
            "reason": "already_synced",
            "dealId": deal_id,
            "partnerCentralOpportunityId": existing_pc_id,
        }
    
    # Build and submit the payload
    pc_payload = hubspot_deal_to_partner_central(deal, company, contacts)
    logger.info(
        f"Creating PC opportunity for deal {deal_id} with payload keys: "
        f"{list(pc_payload.keys())}"
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
    logger.info(f"Created PC opportunity {opportunity_id} for deal {deal_id}")
    
    # Associate solutions
    associated_solutions = _associate_solutions(
        deal, pc_client, opportunity_id, logger
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
        "action": "created",
        "hubspotDealId": deal_id,
        "dealName": deal_name,
        "partnerCentralOpportunityId": opportunity_id,
        "solutionsAssociated": len(associated_solutions),
        "solutionIds": associated_solutions,
    }


def process_hubspot_deal_update(
    sync_event: SyncEvent,
    hubspot_client: Any,
    pc_client: Any,
    logger: Logger,
) -> Dict[str, Any]:
    """
    Process HubSpot deal property change event.
    
    Args:
        sync_event: SyncEvent with deal update data
        hubspot_client: HubSpot API client
        pc_client: Partner Central API client
        logger: Logger instance
        
    Returns:
        Processing result dict
    """
    deal_id = sync_event.object_id
    
    # Fetch deal with associations
    deal, company, contacts = hubspot_client.get_deal_with_associations(deal_id)
    props = deal.get("properties", {})
    
    # Only sync deals that are already in PC
    opportunity_id = props.get("aws_opportunity_id")
    if not opportunity_id:
        logger.debug(f"Deal {deal_id} has no aws_opportunity_id — skipping update")
        return {
            "action": "skipped",
            "reason": "not_synced",
            "dealId": deal_id,
        }
    
    # The deal must still have #AWS to stay in sync scope
    deal_name = props.get("dealname", "")
    if AWS_TRIGGER_TAG.lower() not in deal_name.lower():
        logger.info(f"Deal {deal_id} no longer contains {AWS_TRIGGER_TAG} — skipping update")
        return {
            "action": "skipped",
            "reason": "no_aws_tag",
            "dealId": deal_id,
        }
    
    # Get changed property from event
    changed_property = sync_event.properties.get("propertyName", "")
    changed_properties = {changed_property} if changed_property else set()
    
    # Fetch current PC state to check review status and title
    current_pc_opp = _get_pc_opportunity(pc_client, opportunity_id, logger)
    if not current_pc_opp:
        logger.warning(f"Could not fetch PC opportunity {opportunity_id} — skipping update")
        return {
            "action": "error",
            "reason": "pc_fetch_failed",
            "dealId": deal_id,
            "partnerCentralOpportunityId": opportunity_id,
        }
    
    # Build the update payload
    update_payload, warnings = hubspot_deal_to_partner_central_update(
        deal, current_pc_opp, company, contacts, changed_properties
    )
    
    # Surface warnings back to HubSpot as notes
    for warning in warnings:
        logger.warning(f"Deal {deal_id}: {warning}")
        if "title cannot be changed" in warning.lower() or "immutable" in warning.lower():
            try:
                hubspot_client.add_note_to_deal(
                    deal_id,
                    f"🔒 AWS Partner Central — Title Change Blocked\n\n{warning}"
                )
                # Revert the display title in HubSpot to the canonical PC title
                canonical_title = current_pc_opp.get("Project", {}).get("Title", "")
                if canonical_title:
                    display_title = (
                        canonical_title if "#AWS" in canonical_title
                        else f"{canonical_title} #AWS"
                    )
                    hubspot_client.update_deal(deal_id, {
                        "dealname": display_title,
                        "aws_opportunity_title": canonical_title,
                    })
            except Exception as note_exc:
                logger.warning(f"Could not add note to deal {deal_id}: {note_exc}")
    
    if update_payload is None:
        return {
            "action": "blocked",
            "hubspotDealId": deal_id,
            "partnerCentralOpportunityId": opportunity_id,
            "warnings": warnings,
        }
    
    # Send the update
    pc_client.update_opportunity(**update_payload)
    logger.info(f"Updated PC opportunity {opportunity_id} from deal {deal_id}")
    
    hubspot_client.update_deal(deal_id, {"aws_sync_status": "synced"})
    
    return {
        "action": "updated",
        "hubspotDealId": deal_id,
        "partnerCentralOpportunityId": opportunity_id,
        "changedProperty": changed_property,
        "warnings": warnings,
    }


def _associate_solutions(
    deal: Dict[str, Any],
    pc_client: Any,
    opportunity_id: str,
    logger: Logger,
) -> list[str]:
    """
    Associate solutions with a Partner Central opportunity.
    
    Supports both single solution (from env var) and multi-solution auto-matching.
    
    Returns:
        List of associated solution IDs
    """
    solution_id = os.environ.get("PARTNER_CENTRAL_SOLUTION_ID")
    associated_solutions = []
    
    if solution_id:
        # Single solution from environment variable (backward compatibility)
        _associate_single_solution(pc_client, opportunity_id, solution_id, logger)
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
                    f"Associated {len(associated_solutions)} solutions "
                    f"with opportunity {opportunity_id}"
                )
            else:
                logger.warning(f"No solutions matched for deal {deal.get('id')}")
        except Exception as exc:
            logger.warning(f"Solution auto-matching failed: {exc}")
    
    if not associated_solutions:
        logger.warning(
            f"No solutions associated with opportunity {opportunity_id}. "
            f"Set PARTNER_CENTRAL_SOLUTION_ID or add aws_solution_ids to the deal."
        )
    
    return associated_solutions


def _associate_single_solution(
    pc_client: Any,
    opportunity_id: str,
    solution_id: str,
    logger: Logger,
) -> None:
    """Associate a single solution with the opportunity."""
    try:
        pc_client.associate_opportunity(
            Catalog=PARTNER_CENTRAL_CATALOG,
            OpportunityIdentifier=opportunity_id,
            RelatedEntityIdentifier=solution_id,
            RelatedEntityType="Solutions",
        )
        logger.info(f"Associated solution {solution_id} with opportunity {opportunity_id}")
    except Exception as exc:
        logger.warning(
            f"Could not associate solution {solution_id} with {opportunity_id}: {exc}"
        )


def _get_pc_opportunity(
    pc_client: Any,
    opportunity_id: str,
    logger: Logger,
) -> Optional[Dict[str, Any]]:
    """Fetch a Partner Central opportunity; return None on failure."""
    try:
        return pc_client.get_opportunity(
            Catalog=PARTNER_CENTRAL_CATALOG,
            Identifier=opportunity_id,
        )
    except Exception as exc:
        logger.error(f"Failed to fetch PC opportunity {opportunity_id}: {exc}")
        return None
