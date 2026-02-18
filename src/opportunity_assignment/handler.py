"""
Lambda handler for managing opportunity assignment in AWS Partner Central.

This handler provides:
1. Automatic sync of HubSpot deal owner changes to Partner Central
2. Manual opportunity assignment API
3. Team member management (add/remove)

Triggers:
- HubSpot webhook (deal owner change)
- API Gateway POST /assign-opportunity
- API Gateway POST /opportunity-team/add
- API Gateway POST /opportunity-team/remove
"""

import json

from common.base_handler import BaseLambdaHandler


class OpportunityAssignmentHandler(BaseLambdaHandler):
    """Handler for opportunity assignment operations."""

    def _execute(self, event: dict, context: dict) -> dict:
        """
        Handle opportunity assignment operations.

        Args:
            event: API Gateway or webhook event
            context: Lambda context

        Returns:
            HTTP response with operation result
        """
        path = event.get("path", "")
        http_method = event.get("httpMethod", "")
        body = json.loads(event.get("body", "{}"))

        self.logger.info(f"Assignment operation: {http_method} {path}")

        if "webhook/deal-owner" in path:
            return self._handle_deal_owner_webhook(body)
        elif "/assign-opportunity" in path:
            return self._handle_manual_assignment(body)
        elif "/opportunity-team/add" in path:
            return self._handle_add_team_member(body)
        elif "/opportunity-team/remove" in path:
            return self._handle_remove_team_member(body)
        else:
            return self._error_response("Unknown operation", 400)

    def _handle_deal_owner_webhook(self, webhook_body: dict) -> dict:
        """
        Handle HubSpot deal owner change webhook.

        Args:
            webhook_body: Webhook payload

        Returns:
            HTTP response
        """
        deal_id = webhook_body.get("objectId")
        new_owner_id = webhook_body.get("propertyValue")

        if not deal_id or not new_owner_id:
            return self._error_response("Missing deal ID or owner ID", 400)

        self.logger.info(f"Deal {deal_id} owner changed to {new_owner_id}")

        deal = self.hubspot_client.get_deal(deal_id)
        if not deal:
            return self._error_response("Deal not found", 404)

        properties = deal.get("properties", {})
        opportunity_id = properties.get("aws_opportunity_id")

        if not opportunity_id:
            self.logger.info(f"Deal {deal_id} has no AWS opportunity, skipping")
            return self._success_response({"message": "No AWS opportunity to sync"})

        owner = self.hubspot_client.get_owner(new_owner_id)
        if not owner:
            self.logger.error(f"Owner {new_owner_id} not found")
            return self._error_response("Owner not found", 404)

        owner_email = owner.get("email")
        if not owner_email:
            self.logger.error(f"Owner {new_owner_id} has no email")
            return self._error_response("Owner has no email", 400)

        try:
            self.pc_client.assign_opportunity(
                Catalog="AWS",
                Identifier=opportunity_id,
                Assignee={
                    "Email": owner_email,
                    "FirstName": owner.get("firstName", ""),
                    "LastName": owner.get("lastName", ""),
                },
            )

            self.logger.info(f"Assigned opportunity {opportunity_id} to {owner_email}")

        except Exception as e:
            self.logger.error(f"Failed to assign opportunity: {e}")
            return self._error_response(f"Assignment failed: {str(e)}", 500)

        self.hubspot_client.update_deal(
            deal_id,
            {
                "aws_assigned_partner_user": owner_email,
                "aws_team_last_sync": self.hubspot_client.now_timestamp_ms(),
            },
        )

        note_text = f"""🔄 Opportunity Reassigned in AWS Partner Central

**New Owner:** {owner.get('firstName', '')} {owner.get('lastName', '')}
**Email:** {owner_email}

The opportunity has been reassigned in AWS Partner Central."""

        self.hubspot_client.create_deal_note(deal_id, note_text)

        return self._success_response(
            {
                "message": "Assignment successful",
                "dealId": deal_id,
                "opportunityId": opportunity_id,
                "assignee": owner_email,
            }
        )

    def _handle_manual_assignment(self, body: dict) -> dict:
        """
        Handle manual opportunity assignment API call.

        Args:
            body: Request body with dealId, assigneeEmail, role

        Returns:
            HTTP response
        """
        deal_id = body.get("dealId")
        assignee_email = body.get("assigneeEmail")
        role = body.get("role", "Primary Contact")

        if not deal_id or not assignee_email:
            return self._error_response("Missing dealId or assigneeEmail", 400)

        self.logger.info(f"Manual assignment: deal {deal_id} to {assignee_email}")

        deal = self.hubspot_client.get_deal(deal_id)
        if not deal:
            return self._error_response("Deal not found", 404)

        opportunity_id = deal.get("properties", {}).get("aws_opportunity_id")
        if not opportunity_id:
            return self._error_response("Deal has no AWS opportunity", 400)

        name_parts = assignee_email.split("@")[0].split(".")
        first_name = name_parts[0].capitalize() if name_parts else ""
        last_name = name_parts[1].capitalize() if len(name_parts) > 1 else ""

        try:
            self.pc_client.assign_opportunity(
                Catalog="AWS",
                Identifier=opportunity_id,
                Assignee={
                    "Email": assignee_email,
                    "FirstName": first_name,
                    "LastName": last_name,
                },
            )
        except Exception as e:
            self.logger.error(f"Failed to assign opportunity: {e}")
            return self._error_response(f"Assignment failed: {str(e)}", 500)

        self.hubspot_client.update_deal(
            deal_id,
            {
                "aws_assigned_partner_user": assignee_email,
                "aws_team_last_sync": self.hubspot_client.now_timestamp_ms(),
            },
        )

        note_text = f"""👤 Opportunity Assigned

**Assignee:** {assignee_email}
**Role:** {role}

The opportunity has been assigned in AWS Partner Central."""

        self.hubspot_client.create_deal_note(deal_id, note_text)

        return self._success_response(
            {
                "message": "Assignment successful",
                "dealId": deal_id,
                "opportunityId": opportunity_id,
                "assignee": assignee_email,
            }
        )

    def _handle_add_team_member(self, body: dict) -> dict:
        """Handle adding a team member to an opportunity."""
        self.logger.info("Add team member requested - not yet fully implemented")
        return {
            "statusCode": 501,
            "body": json.dumps(
                {
                    "message": "Team member management not yet fully implemented",
                    "note": "Use AssignOpportunity for primary contact assignment",
                }
            ),
        }

    def _handle_remove_team_member(self, body: dict) -> dict:
        """Handle removing a team member from an opportunity."""
        self.logger.info("Remove team member requested - not yet fully implemented")
        return {
            "statusCode": 501,
            "body": json.dumps(
                {
                    "message": "Team member management not yet fully implemented",
                    "note": "Use AssignOpportunity to change primary contact",
                }
            ),
        }


def lambda_handler(event: dict, context: dict) -> dict:
    """Lambda handler entry point."""
    handler = OpportunityAssignmentHandler()
    return handler.handle(event, context)
