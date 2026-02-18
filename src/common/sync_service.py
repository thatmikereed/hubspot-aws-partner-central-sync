"""
Sync orchestration service for bidirectional data synchronization.
Handles common sync patterns and conflict resolution.
"""

import logging
from datetime import datetime, timezone
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


class SyncOrchestrator:
    """
    Orchestrates synchronization between HubSpot and AWS Partner Central.
    Provides reusable sync patterns and conflict resolution.
    """

    def __init__(self, hubspot_client, pc_client):
        self.hubspot = hubspot_client
        self.pc = pc_client
        self.logger = logger

    def sync_deal_to_opportunity(
        self, deal_id: str, opportunity_id: str, force: bool = False
    ) -> Tuple[bool, Optional[str]]:
        """
        Sync HubSpot deal changes to Partner Central opportunity.

        Args:
            deal_id: HubSpot deal ID
            opportunity_id: Partner Central opportunity ID
            force: Force sync even if review status prevents it

        Returns:
            Tuple of (success: bool, error_message: Optional[str])
        """
        try:
            # Check review status
            if not force:
                review_status = self._get_review_status(opportunity_id)
                if review_status in ["Submitted", "In-Review"]:
                    return False, f"Cannot sync - opportunity is {review_status}"

            # Get current deal data
            deal = self.hubspot.get_deal(deal_id)

            # Transform to Partner Central format
            from common.mappers import hubspot_deal_to_partner_central_update

            pc_update = hubspot_deal_to_partner_central_update(deal)

            # Update Partner Central
            self.pc.update_opportunity(**pc_update)

            # Add sync timestamp
            self.hubspot.update_deal(
                deal_id, {"aws_last_sync": datetime.now(timezone.utc).isoformat()}
            )

            return True, None

        except Exception as e:
            self.logger.error(f"Sync failed: {e}", exc_info=True)
            return False, str(e)

    def sync_opportunity_to_deal(
        self, opportunity_id: str, deal_id: str
    ) -> Tuple[bool, Optional[str]]:
        """
        Sync Partner Central opportunity changes to HubSpot deal.

        Args:
            opportunity_id: Partner Central opportunity ID
            deal_id: HubSpot deal ID

        Returns:
            Tuple of (success: bool, error_message: Optional[str])
        """
        try:
            # Get current opportunity data
            opportunity = self.pc.get_opportunity(
                Catalog="AWS", Identifier=opportunity_id
            )

            # Transform to HubSpot format
            from common.mappers import partner_central_opportunity_to_hubspot

            hs_properties = partner_central_opportunity_to_hubspot(opportunity)

            # Update HubSpot
            self.hubspot.update_deal(deal_id, hs_properties)

            return True, None

        except Exception as e:
            self.logger.error(f"Sync failed: {e}", exc_info=True)
            return False, str(e)

    def _get_review_status(self, opportunity_id: str) -> Optional[str]:
        """Get current review status from Partner Central"""
        try:
            opp = self.pc.get_opportunity(Catalog="AWS", Identifier=opportunity_id)
            return opp.get("Lifecycle", {}).get("ReviewStatus")
        except Exception:
            return None
