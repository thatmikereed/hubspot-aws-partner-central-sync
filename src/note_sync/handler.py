"""
Lambda handler: Deal Activity/Note Sync

Syncs HubSpot deal notes and activities to Partner Central Project notes and NextSteps.

When sales reps add notes in HubSpot, they're automatically synced to Partner Central
so AWS can see the latest deal status and next actions.

Triggered by:
- HubSpot webhook: engagement.creation (when note is added)
- Manual API call for retroactive sync
"""

from datetime import datetime, timezone

from common.aws_client import PARTNER_CENTRAL_CATALOG
from common.base_handler import BaseLambdaHandler


class NoteSyncHandler(BaseLambdaHandler):
    """
    Handles HubSpot note/engagement sync to Partner Central.

    Event types:
    1. HubSpot webhook (engagement.creation)
    2. Manual API call {"dealId": "123"}
    """

    def _execute(self, event: dict, context: dict) -> dict:
        """
        Sync HubSpot deal notes to Partner Central.
        """
        body = self._parse_webhook_body(event)

        # HubSpot webhook event
        if isinstance(body, list):
            synced = []
            for webhook_event in body:
                if "engagement.creation" in webhook_event.get("subscriptionType", ""):
                    engagement_id = webhook_event.get("objectId")
                    result = self._sync_engagement(engagement_id)
                    if result:
                        synced.append(result)

            return self._success_response({"synced": len(synced), "results": synced})

        # Manual API call
        deal_id = body.get("dealId")
        if deal_id:
            result = self._sync_all_notes_for_deal(deal_id)
            return self._success_response(result)

        return self._error_response("No dealId provided", 400)

    def _sync_engagement(self, engagement_id: str) -> dict | None:
        """Sync a single HubSpot engagement (note) to Partner Central."""
        self.logger.info("Syncing engagement %s", engagement_id)

        # Fetch engagement details
        try:
            response = self.hubspot_client.session.get(
                f"https://api.hubapi.com/crm/v3/objects/notes/{engagement_id}",
                params={"properties": "hs_note_body,hs_timestamp"},
            )
            response.raise_for_status()
            engagement = response.json()
        except Exception as exc:
            self.logger.warning("Could not fetch engagement %s: %s", engagement_id, exc)
            return None

        props = engagement.get("properties", {})
        note_body = props.get("hs_note_body", "")
        timestamp = props.get("hs_timestamp", "")

        if not note_body:
            return None

        # Get associated deal
        deal_id = self._get_associated_deal(engagement_id)
        if not deal_id:
            self.logger.warning("No deal associated with engagement %s", engagement_id)
            return None

        # Get deal's PC opportunity ID
        deal, _, _ = self.hubspot_client.get_deal_with_associations(deal_id)
        opportunity_id = deal.get("properties", {}).get("aws_opportunity_id")

        if not opportunity_id:
            self.logger.info("Deal %s has no PC opportunity - skipping", deal_id)
            return None

        # Update Partner Central with the note
        self._add_note_to_partner_central(opportunity_id, note_body, timestamp)

        return {
            "engagementId": engagement_id,
            "dealId": deal_id,
            "opportunityId": opportunity_id,
            "status": "synced",
        }

    def _sync_all_notes_for_deal(self, deal_id: str) -> dict:
        """Retroactive sync: get all notes for a deal and sync to PC."""
        self.logger.info("Syncing all notes for deal %s", deal_id)

        # Get deal's PC opportunity ID
        deal, _, _ = self.hubspot_client.get_deal_with_associations(deal_id)
        opportunity_id = deal.get("properties", {}).get("aws_opportunity_id")

        if not opportunity_id:
            return {"error": "Deal has no Partner Central opportunity"}

        # Fetch all notes for the deal
        try:
            response = self.hubspot_client.session.post(
                "https://api.hubapi.com/crm/v3/objects/notes/search",
                json={
                    "filterGroups": [
                        {
                            "filters": [
                                {
                                    "propertyName": "associations.deal",
                                    "operator": "EQ",
                                    "value": deal_id,
                                }
                            ]
                        }
                    ],
                    "properties": ["hs_note_body", "hs_timestamp"],
                    "sorts": [
                        {"propertyName": "hs_timestamp", "direction": "DESCENDING"}
                    ],
                    "limit": 50,
                },
            )
            response.raise_for_status()
            notes = response.json().get("results", [])
        except Exception as exc:
            self.logger.warning("Could not fetch notes for deal %s: %s", deal_id, exc)
            notes = []

        if not notes:
            return {"dealId": deal_id, "notesSynced": 0}

        # Consolidate notes into a single update
        consolidated = "\n\n---\n\n".join(
            [
                f"[{n.get('properties', {}).get('hs_timestamp', 'Unknown')}]\n{n.get('properties', {}).get('hs_note_body', '')}"
                for n in notes[:10]  # Limit to most recent 10
            ]
        )

        self._add_note_to_partner_central(opportunity_id, consolidated, None)

        return {
            "dealId": deal_id,
            "opportunityId": opportunity_id,
            "notesSynced": len(notes),
        }

    def _get_associated_deal(self, engagement_id: str) -> str | None:
        """Get the deal associated with an engagement."""
        try:
            url = f"https://api.hubapi.com/crm/v3/objects/notes/{engagement_id}/associations/deals"
            response = self.hubspot_client.session.get(url)
            response.raise_for_status()
            results = response.json().get("results", [])
            return results[0].get("id") if results else None
        except Exception:
            return None

    def _add_note_to_partner_central(
        self, opportunity_id: str, note_body: str, timestamp: str | None
    ):
        """
        Add a note to Partner Central by updating LifeCycle.NextSteps.

        Partner Central doesn't have a native "notes" field, so we append to NextSteps
        with a timestamp prefix.
        """
        self.logger.info("Adding note to PC opportunity %s", opportunity_id)

        try:
            # Fetch current opportunity
            opportunity = self.pc_client.get_opportunity(
                Catalog=PARTNER_CENTRAL_CATALOG,
                Identifier=opportunity_id,
            )

            current_next_steps = opportunity.get("LifeCycle", {}).get("NextSteps", "")

            # Format new note
            ts_str = timestamp or datetime.now(timezone.utc).isoformat()
            new_note = f"\n\n[HubSpot Note - {ts_str}]\n{note_body[:500]}"  # Truncate to 500 chars

            updated_next_steps = (current_next_steps + new_note)[:65535]  # API limit

            # Update PC
            self.pc_client.update_opportunity(
                Catalog=PARTNER_CENTRAL_CATALOG,
                Identifier=opportunity_id,
                LifeCycle={
                    "Stage": opportunity.get("LifeCycle", {}).get("Stage", "Prospect"),
                    "TargetCloseDate": opportunity.get("LifeCycle", {}).get(
                        "TargetCloseDate"
                    ),
                    "NextSteps": updated_next_steps,
                },
            )

            self.logger.info("Successfully updated NextSteps for %s", opportunity_id)

        except Exception as exc:
            self.logger.warning("Failed to add note to PC %s: %s", opportunity_id, exc)
            raise


# Lambda entry point
def lambda_handler(event: dict, context: dict) -> dict:
    """
    Lambda entry point for note sync handler.

    Args:
        event: API Gateway event with webhook payload
        context: Lambda context

    Returns:
        HTTP response with status and details
    """
    return NoteSyncHandler().handle(event, context)
