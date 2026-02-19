"""
Note sync processor module.

Extracted business logic for processing HubSpot note/engagement events and syncing to AWS Partner Central.
"""

from datetime import datetime, timezone
from logging import Logger
from typing import Any, Dict, Optional

from common.aws_client import PARTNER_CENTRAL_CATALOG
from common.events import SyncEvent


def process_note_creation(
    sync_event: SyncEvent,
    hubspot_client: Any,
    pc_client: Any,
    logger: Logger,
) -> Dict[str, Any]:
    """
    Process HubSpot note/engagement creation event.

    Syncs HubSpot deal notes to Partner Central Project notes and NextSteps.

    Args:
        sync_event: SyncEvent with note creation data
        hubspot_client: HubSpot API client
        pc_client: Partner Central API client
        logger: Logger instance

    Returns:
        Processing result dict
    """
    engagement_id = sync_event.object_id
    logger.info(f"Syncing engagement {engagement_id}")

    # Fetch engagement details
    try:
        response = hubspot_client.session.get(
            f"https://api.hubapi.com/crm/v3/objects/notes/{engagement_id}",
            params={"properties": "hs_note_body,hs_timestamp"},
        )
        response.raise_for_status()
        engagement = response.json()
    except Exception as exc:
        logger.warning(f"Could not fetch engagement {engagement_id}: {exc}")
        return {
            "action": "error",
            "reason": "engagement_fetch_failed",
            "engagementId": engagement_id,
            "error": str(exc),
        }

    props = engagement.get("properties", {})
    note_body = props.get("hs_note_body", "")
    timestamp = props.get("hs_timestamp", "")

    if not note_body:
        return {
            "action": "skipped",
            "reason": "empty_note",
            "engagementId": engagement_id,
        }

    # Get associated deal
    deal_id = _get_associated_deal(engagement_id, hubspot_client, logger)
    if not deal_id:
        logger.warning(f"No deal associated with engagement {engagement_id}")
        return {
            "action": "skipped",
            "reason": "no_deal_association",
            "engagementId": engagement_id,
        }

    # Get deal's PC opportunity ID
    deal, _, _ = hubspot_client.get_deal_with_associations(deal_id)
    opportunity_id = deal.get("properties", {}).get("aws_opportunity_id")

    if not opportunity_id:
        logger.info(f"Deal {deal_id} has no PC opportunity - skipping")
        return {
            "action": "skipped",
            "reason": "no_opportunity",
            "engagementId": engagement_id,
            "dealId": deal_id,
        }

    # Update Partner Central with the note
    _add_note_to_partner_central(
        opportunity_id, note_body, timestamp, pc_client, logger
    )

    return {
        "action": "synced",
        "engagementId": engagement_id,
        "dealId": deal_id,
        "opportunityId": opportunity_id,
    }


def _get_associated_deal(
    engagement_id: str,
    hubspot_client: Any,
    logger: Logger,
) -> Optional[str]:
    """Get the deal associated with an engagement."""
    try:
        url = f"https://api.hubapi.com/crm/v3/objects/notes/{engagement_id}/associations/deals"
        response = hubspot_client.session.get(url)
        response.raise_for_status()
        results = response.json().get("results", [])
        return results[0].get("id") if results else None
    except Exception as exc:
        logger.warning(
            f"Could not get deal association for engagement {engagement_id}: {exc}"
        )
        return None


def _add_note_to_partner_central(
    opportunity_id: str,
    note_body: str,
    timestamp: Optional[str],
    pc_client: Any,
    logger: Logger,
) -> None:
    """
    Add a note to Partner Central by updating LifeCycle.NextSteps.

    Partner Central doesn't have a native "notes" field, so we append to NextSteps
    with a timestamp prefix.
    """
    logger.info(f"Adding note to PC opportunity {opportunity_id}")

    try:
        # Fetch current opportunity
        opportunity = pc_client.get_opportunity(
            Catalog=PARTNER_CENTRAL_CATALOG,
            Identifier=opportunity_id,
        )

        current_next_steps = opportunity.get("LifeCycle", {}).get("NextSteps", "")

        # Format new note
        ts_str = timestamp or datetime.now(timezone.utc).isoformat()
        new_note = (
            f"\n\n[HubSpot Note - {ts_str}]\n{note_body[:500]}"  # Truncate to 500 chars
        )

        updated_next_steps = (current_next_steps + new_note)[:65535]  # API limit

        # Update PC
        pc_client.update_opportunity(
            Catalog=PARTNER_CENTRAL_CATALOG,
            Identifier=opportunity_id,
            LifeCycle={
                **opportunity.get("LifeCycle", {}),
                "NextSteps": updated_next_steps,
            },
            Customer=opportunity.get("Customer", {}),
            Project={
                k: v
                for k, v in opportunity.get("Project", {}).items()
                if k != "Title"  # Immutable field
            },
        )

        logger.info(f"Successfully added note to opportunity {opportunity_id}")

    except Exception as exc:
        logger.error(f"Failed to add note to opportunity {opportunity_id}: {exc}")
        raise
