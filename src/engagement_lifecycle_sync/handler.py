"""
Lambda handler for syncing AWS Partner Central engagement lifecycle to HubSpot.

This handler:
1. Fetches all HubSpot deals with AWS opportunities
2. For each opportunity, gets engagement status and details
3. Syncs engagement lifecycle data to HubSpot properties
4. Creates timeline events for status changes
5. Syncs team members

Trigger: Scheduled (every 30 minutes) or EventBridge (real-time)
"""

import json
from datetime import datetime
from typing import Optional

from common.base_handler import BaseLambdaHandler


class EngagementLifecycleSyncHandler(BaseLambdaHandler):
    """Handler for syncing engagement lifecycle from Partner Central to HubSpot."""

    def _execute(self, event: dict, context: dict) -> dict:
        """
        Sync engagement lifecycle data from Partner Central to HubSpot.

        Args:
            event: EventBridge or manual invocation event
            context: Lambda context

        Returns:
            Summary of sync operation
        """
        self.logger.info("Starting engagement lifecycle sync")

        # Get all deals with AWS opportunities
        deals = self.hubspot_client.get_all_deals_with_property("aws_opportunity_id")
        self.logger.info(f"Found {len(deals)} deals with AWS opportunities")

        synced_count = 0
        skipped_count = 0
        errors = []

        for deal in deals:
            try:
                deal_id = deal["id"]
                properties = deal.get("properties", {})
                opportunity_id = properties.get("aws_opportunity_id")

                if not opportunity_id:
                    skipped_count += 1
                    continue

                # Get engagements for this opportunity
                try:
                    engagements_response = self.pc_client.list_engagements(
                        Identifier=[opportunity_id], Catalog="AWS"
                    )
                except Exception as e:
                    self.logger.error(
                        f"Failed to list engagements for {opportunity_id}: {e}"
                    )
                    errors.append(f"Deal {deal_id}: {str(e)}")
                    skipped_count += 1
                    continue

                engagements = engagements_response.get("EngagementSummaryList", [])

                if not engagements:
                    self.logger.debug(
                        f"No engagements found for opportunity {opportunity_id}"
                    )
                    skipped_count += 1
                    continue

                # Use the first (most recent) engagement
                engagement_summary = engagements[0]
                engagement_id = engagement_summary.get("Id")

                # Get full engagement details
                try:
                    engagement = self.pc_client.get_engagement(
                        Catalog="AWS", Identifier=engagement_id
                    )
                except Exception as e:
                    self.logger.error(f"Failed to get engagement {engagement_id}: {e}")
                    errors.append(f"Deal {deal_id}: {str(e)}")
                    skipped_count += 1
                    continue

                # Get engagement members
                team_members = []
                try:
                    members_response = self.pc_client.list_engagement_members(
                        Catalog="AWS", Identifier=engagement_id
                    )
                    team_members = members_response.get("EngagementMemberList", [])
                except Exception as e:
                    self.logger.warning(f"Failed to get engagement members: {e}")

                # Extract engagement data
                engagement_status = engagement.get("Status", "Unknown")
                created_date = engagement.get("CreatedDate")

                # Build team member list
                team_emails = [m.get("Email") for m in team_members if m.get("Email")]
                team_string = ", ".join(team_emails[:10])  # Max 10

                # Check if status changed (for timeline event)
                current_status = properties.get("aws_engagement_status")
                status_changed = current_status != engagement_status

                # Update HubSpot properties
                update_properties = {
                    "aws_engagement_id": engagement_id,
                    "aws_engagement_status": engagement_status,
                    "aws_engagement_team": team_string if team_string else None,
                    "aws_last_engagement_sync": self.hubspot_client.now_timestamp_ms(),
                }

                if created_date:
                    update_properties["aws_engagement_kickoff_date"] = (
                        self._iso_to_hubspot_timestamp(created_date)
                    )

                # Remove None values
                update_properties = {
                    k: v for k, v in update_properties.items() if v is not None
                }

                self.hubspot_client.update_deal(deal_id, update_properties)

                # Create timeline event if status changed
                if status_changed:
                    self._create_status_change_timeline_event(
                        deal_id, current_status, engagement_status
                    )

                    # Also create a note
                    note_text = self._create_engagement_status_note(
                        engagement_status, team_emails
                    )
                    self.hubspot_client.create_deal_note(deal_id, note_text)

                synced_count += 1
                self.logger.info(f"Synced engagement {engagement_id} to deal {deal_id}")

            except Exception as e:
                self.logger.error(
                    f"Error syncing deal {deal.get('id')}: {e}", exc_info=True
                )
                errors.append(f"Deal {deal.get('id')}: {str(e)}")

        result = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "dealsProcessed": len(deals),
            "engagementsSynced": synced_count,
            "dealsSkipped": skipped_count,
            "errors": errors[:10],  # Limit error list
        }

        self.logger.info(f"Engagement lifecycle sync complete: {json.dumps(result)}")

        return self._success_response(result)

    def _iso_to_hubspot_timestamp(self, iso_string: str) -> int:
        """Convert ISO 8601 string to HubSpot timestamp (milliseconds since epoch)."""
        try:
            dt = datetime.fromisoformat(iso_string.replace("Z", "+00:00"))
            return int(dt.timestamp() * 1000)
        except Exception:
            return None

    def _create_status_change_timeline_event(
        self, deal_id: str, old_status: Optional[str], new_status: str
    ) -> None:
        """Create a timeline event for engagement status change."""
        status_emoji = {
            "Active": "🚀",
            "Completed": "✅",
            "Cancelled": "❌",
            "Pending": "⏳",
        }

        emoji = status_emoji.get(new_status, "🔄")
        title = f"{emoji} AWS Engagement Status: {new_status}"

        # Note: HubSpot timeline events require custom event definition
        # For now, we'll just create a note (done in main handler)
        self.logger.info(f"Would create timeline event: {title}")

    def _create_engagement_status_note(
        self, status: str, team_members: list[str]
    ) -> str:
        """Create a note describing the engagement status."""
        status_emoji = {
            "Active": "🚀",
            "Completed": "✅",
            "Cancelled": "❌",
            "Pending": "⏳",
        }

        emoji = status_emoji.get(status, "🔄")
        note = f"""{emoji} AWS Engagement Status Updated

**Status:** {status}

"""

        if team_members:
            note += "**Team Members:**\n"
            for email in team_members[:10]:
                note += f"- {email}\n"
            note += "\n"

        note += f"*Synced from AWS Partner Central on {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}*"

        return note


def lambda_handler(event: dict, context: dict) -> dict:
    """Lambda entry point."""
    handler = EngagementLifecycleSyncHandler()
    return handler.handle(event, context)
