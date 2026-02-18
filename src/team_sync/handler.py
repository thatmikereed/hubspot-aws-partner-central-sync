"""
Lambda handler: Opportunity Team Sync

Syncs Partner Central OpportunityTeam members to HubSpot deal team.

When AWS assigns team members (AWS sellers, SAs, etc.) to an opportunity in Partner Central,
those team members are synced to HubSpot so sales reps know who to contact at AWS.

Creates HubSpot contacts for AWS team members and associates them with the deal.

Triggered by:
- EventBridge "Opportunity Updated" events
- AWS Summary sync (checks for team changes)
- Manual API call
"""

from common.base_handler import BaseLambdaHandler
from common.aws_client import PARTNER_CENTRAL_CATALOG


class TeamSyncHandler(BaseLambdaHandler):
    """
    Handles opportunity team sync from Partner Central to HubSpot.

    When AWS assigns team members to an opportunity, syncs them to HubSpot
    as contacts and associates them with the corresponding deal.
    """

    def _execute(self, event: dict, context: dict) -> dict:
        # Extract opportunity ID from event
        detail = event.get("detail", {})
        opportunity_id = detail.get("opportunity", {}).get("identifier") or event.get(
            "opportunityId"
        )

        if not opportunity_id:
            return self._error_response("No opportunityId", 400)

        result = self._sync_team_for_opportunity(opportunity_id)
        return self._success_response(result)

    def _sync_team_for_opportunity(self, opportunity_id: str) -> dict:
        """Sync OpportunityTeam from PC to HubSpot deal team."""
        self.logger.info("Syncing team for opportunity %s", opportunity_id)

        # Fetch PC opportunity
        opportunity = self.pc_client.get_opportunity(
            Catalog=PARTNER_CENTRAL_CATALOG,
            Identifier=opportunity_id,
        )

        team_members = opportunity.get("OpportunityTeam", [])

        if not team_members:
            self.logger.info("No team members to sync for %s", opportunity_id)
            return {"opportunityId": opportunity_id, "teamMembersSynced": 0}

        # Find HubSpot deal
        deals = self.hubspot_client.search_deals_by_aws_opportunity_id(opportunity_id)
        if not deals:
            self.logger.warning("No HubSpot deal found for %s", opportunity_id)
            return {"error": "No HubSpot deal found"}

        deal_id = deals[0]["id"]

        # Sync each team member
        synced_count = 0
        for member in team_members:
            email = member.get("Email")
            if not email:
                continue

            try:
                # Create/update contact with "AWS Team" designation
                hs_contact = self._create_aws_team_contact(member)

                # Associate with deal
                self._associate_contact_with_deal(hs_contact["id"], deal_id)

                synced_count += 1
                self.logger.info("Synced AWS team member %s to deal %s", email, deal_id)
            except Exception as exc:
                self.logger.warning("Failed to sync team member %s: %s", email, exc)

        # Add note to deal documenting AWS team
        if synced_count > 0:
            team_note = "👥 AWS Team Updated\n\n" + "\n".join(
                [
                    f"• {m.get('FirstName', '')} {m.get('LastName', '')} - {m.get('BusinessTitle', 'AWS Team Member')}"
                    for m in team_members
                    if m.get("FirstName") or m.get("LastName")
                ]
            )
            self.hubspot_client.add_note_to_deal(deal_id, team_note)

        return {
            "opportunityId": opportunity_id,
            "dealId": deal_id,
            "teamMembersSynced": synced_count,
        }

    def _create_aws_team_contact(self, member: dict) -> dict:
        """Create or update a HubSpot contact for an AWS team member."""
        email = member.get("Email", "")
        first_name = member.get("FirstName", "")
        last_name = member.get("LastName", "")
        title = member.get("BusinessTitle", "AWS Team Member")
        phone = member.get("Phone", "")

        properties = {
            "email": email,
            "firstname": first_name,
            "lastname": last_name,
            "jobtitle": title,
            "company": "Amazon Web Services",
            "lifecyclestage": "other",  # AWS team members aren't leads/customers
        }
        if phone:
            properties["phone"] = phone

        # Check if exists
        try:
            search_response = self.hubspot_client.session.post(
                "https://api.hubapi.com/crm/v3/objects/contacts/search",
                json={
                    "filterGroups": [
                        {
                            "filters": [
                                {
                                    "propertyName": "email",
                                    "operator": "EQ",
                                    "value": email,
                                }
                            ]
                        }
                    ],
                    "limit": 1,
                },
            )
            search_response.raise_for_status()
            existing = search_response.json().get("results", [])

            if existing:
                contact_id = existing[0]["id"]
                response = self.hubspot_client.session.patch(
                    f"https://api.hubapi.com/crm/v3/objects/contacts/{contact_id}",
                    json={"properties": properties},
                )
                response.raise_for_status()
                return response.json()
            else:
                response = self.hubspot_client.session.post(
                    "https://api.hubapi.com/crm/v3/objects/contacts",
                    json={"properties": properties},
                )
                response.raise_for_status()
                return response.json()

        except Exception as exc:
            self.logger.warning(
                "Error creating/updating AWS team contact %s: %s", email, exc
            )
            raise

    def _associate_contact_with_deal(self, contact_id: str, deal_id: str):
        """Associate a contact with a deal."""
        try:
            url = f"https://api.hubapi.com/crm/v3/objects/contacts/{contact_id}/associations/deals/{deal_id}/contact_to_deal"
            response = self.hubspot_client.session.put(url)
            if response.status_code not in (200, 204, 409):
                response.raise_for_status()
        except Exception as exc:
            self.logger.warning(
                "Error associating contact %s with deal %s: %s",
                contact_id,
                deal_id,
                exc,
            )


def lambda_handler(event: dict, context: dict) -> dict:
    """
    Lambda entry point for team sync handler.

    Args:
        event: EventBridge event or API Gateway event with opportunityId
        context: Lambda context

    Returns:
        HTTP response with status and details
    """
    return TeamSyncHandler().handle(event, context)
