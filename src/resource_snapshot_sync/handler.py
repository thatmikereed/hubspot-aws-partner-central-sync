"""
Lambda handler: Engagement Resource Snapshot Sync

Syncs AWS Partner Central engagement resources (whitepapers, case studies,
solution briefs) to HubSpot deals as notes and attachments.

Scheduled Lambda that:
1. Fetches all deals with active AWS opportunities
2. Calls GetResourceSnapshot for each engagement
3. Syncs resource metadata to HubSpot as deal notes
4. Links resources to the deal for sales rep access

Resources synced:
- Solution briefs and presentations
- Customer case studies
- Technical whitepapers
- Reference architectures
- Training materials
"""

import json
from datetime import datetime

from common.base_handler import BaseLambdaHandler
from common.aws_client import PARTNER_CENTRAL_CATALOG


class ResourceSnapshotSyncHandler(BaseLambdaHandler):
    """Handler for syncing AWS engagement resource snapshots to HubSpot."""

    def _execute(self, event: dict, context: dict) -> dict:
        """
        Scheduled sync of AWS engagement resource snapshots.

        For each HubSpot deal with an active AWS opportunity, fetch the
        resource snapshot and sync new resources as notes.
        """
        self.logger.info("Starting resource snapshot sync")

        synced = []
        errors = []

        # Get all deals with AWS opportunities
        deals = self._list_eligible_deals()
        self.logger.info("Found %d eligible deals to sync resources for", len(deals))

        for deal in deals:
            deal_id = deal["id"]
            props = deal.get("properties", {})
            aws_opportunity_id = props.get("aws_opportunity_id")

            if not aws_opportunity_id:
                continue

            try:
                # Get the engagement ID from the opportunity
                opp_response = self.pc_client.get_opportunity(
                    Catalog=PARTNER_CENTRAL_CATALOG,
                    Identifier=aws_opportunity_id,
                )

                # Check if opportunity has an engagement
                lifecycle = opp_response.get("LifeCycle", {})
                review_status = lifecycle.get("ReviewStatus")

                # Only sync resources for opportunities that have been submitted
                if review_status not in [
                    "Submitted",
                    "In review",
                    "Action Required",
                    "Approved",
                ]:
                    self.logger.info(
                        "Skipping deal %s - opportunity not yet submitted (status: %s)",
                        deal_id,
                        review_status,
                    )
                    continue

                # List engagements for this opportunity
                engagements_response = self.pc_client.list_engagements(
                    Catalog=PARTNER_CENTRAL_CATALOG,
                    OpportunityIdentifier=[aws_opportunity_id],
                )

                engagements = engagements_response.get("EngagementSummaryList", [])

                if not engagements:
                    self.logger.info(
                        "No engagements found for opportunity %s", aws_opportunity_id
                    )
                    continue

                # Get resource snapshot for the first engagement
                engagement_id = engagements[0].get("Id")

                try:
                    snapshot = self.pc_client.get_resource_snapshot(
                        Catalog=PARTNER_CENTRAL_CATALOG,
                        EngagementIdentifier=engagement_id,
                    )

                    # Sync resources to HubSpot
                    synced_count = self._sync_resources_to_hubspot(
                        deal_id, snapshot, props
                    )

                    synced.append(
                        {
                            "dealId": deal_id,
                            "engagementId": engagement_id,
                            "resourcesSynced": synced_count,
                        }
                    )

                    self.logger.info(
                        "Synced %d resources for deal %s", synced_count, deal_id
                    )

                except self.pc_client.exceptions.ResourceNotFoundException:
                    self.logger.info(
                        "No resource snapshot found for engagement %s", engagement_id
                    )

            except Exception as e:
                error_msg = f"Error syncing resources for deal {deal_id}: {str(e)}"
                self.logger.error(error_msg, exc_info=True)
                errors.append({"dealId": deal_id, "error": str(e)})

        result = {
            "synced": synced,
            "syncedCount": len(synced),
            "errors": errors,
            "errorCount": len(errors),
        }

        self.logger.info("Resource sync complete: %s", json.dumps(result, default=str))

        return self._success_response(result)

    def _list_eligible_deals(self) -> list[dict]:
        """
        List all HubSpot deals with AWS opportunities in active states.

        Returns list of deal objects with properties.
        """
        # Search for deals with aws_opportunity_id set
        url = "https://api.hubapi.com/crm/v3/objects/deals/search"

        payload = {
            "filterGroups": [
                {
                    "filters": [
                        {
                            "propertyName": "aws_opportunity_id",
                            "operator": "HAS_PROPERTY",
                        },
                        {
                            "propertyName": "dealstage",
                            "operator": "NEQ",
                            "value": "closedlost",
                        },
                        {
                            "propertyName": "dealstage",
                            "operator": "NEQ",
                            "value": "closedwon",
                        },
                    ]
                }
            ],
            "properties": [
                "dealname",
                "aws_opportunity_id",
                "aws_synced_resources",
                "aws_last_resource_sync",
            ],
            "limit": 100,
        }

        response = self.hubspot_client.session.post(url, json=payload)
        response.raise_for_status()

        return response.json().get("results", [])

    def _sync_resources_to_hubspot(
        self, deal_id: str, snapshot: dict, deal_props: dict
    ) -> int:
        """
        Sync resources from Partner Central snapshot to HubSpot deal.

        Returns count of new resources synced.
        """
        resources = snapshot.get("Resources", [])

        if not resources:
            self.logger.info("No resources in snapshot for deal %s", deal_id)
            return 0

        # Track which resources we've already synced
        synced_resources_str = deal_props.get("aws_synced_resources", "")
        synced_resource_ids = (
            set(synced_resources_str.split(",")) if synced_resources_str else set()
        )

        new_resources = []

        for resource in resources:
            resource_id = resource.get("Id", "")
            resource_type = resource.get("Type", "Unknown")
            resource_name = resource.get("Name", "Untitled Resource")
            resource_url = resource.get("Url", "")
            resource_description = resource.get("Description", "")

            # Skip if already synced
            if resource_id in synced_resource_ids:
                continue

            # Create note in HubSpot
            note_text = self._format_resource_note(
                resource_name, resource_type, resource_description, resource_url
            )

            self.hubspot_client.add_note_to_deal(deal_id, note_text)

            new_resources.append(resource_id)
            synced_resource_ids.add(resource_id)

        # Update deal with new synced resources list
        if new_resources:
            self.hubspot_client.update_deal(
                deal_id,
                {
                    "aws_synced_resources": ",".join(synced_resource_ids),
                    "aws_last_resource_sync": datetime.utcnow().isoformat(),
                },
            )

        return len(new_resources)

    def _format_resource_note(
        self, name: str, resource_type: str, description: str, url: str
    ) -> str:
        """Format a resource as a HubSpot note."""
        icon = self._get_resource_icon(resource_type)

        note = f"{icon} AWS Resource: {name}\n\n"
        note += f"**Type:** {resource_type}\n\n"

        if description:
            note += f"**Description:** {description}\n\n"

        if url:
            note += f"**Link:** {url}\n\n"

        note += f"*Synced from AWS Partner Central on {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}*"

        return note

    def _get_resource_icon(self, resource_type: str) -> str:
        """Get emoji icon for resource type."""
        icons = {
            "Case Study": "📄",
            "Whitepaper": "📃",
            "Solution Brief": "📋",
            "Reference Architecture": "🏗️",
            "Training Material": "🎓",
            "Presentation": "📊",
            "Video": "🎥",
        }
        return icons.get(resource_type, "📎")


def lambda_handler(event: dict, context) -> dict:
    """Lambda entry point."""
    handler = ResourceSnapshotSyncHandler()
    return handler.handle(event, context)
