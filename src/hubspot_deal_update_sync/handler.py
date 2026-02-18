"""
Lambda handler: HubSpot Deal Update Sync

Triggered by HubSpot deal.propertyChange webhook. When a deal property is updated
in HubSpot, this handler syncs relevant changes back to AWS Partner Central via
the UpdateOpportunity API.

This enables true bidirectional sync:
- Sales rep updates deal stage in HubSpot → Partner Central opportunity stage updated
- Sales rep updates close date → Partner Central target close date updated
- Sales rep updates amount → Partner Central expected spend updated

Properties synced:
- dealstage → LifeCycle.Stage
- closedate → LifeCycle.TargetCloseDate
- amount → Project.ExpectedCustomerSpend
- description → Project.CustomerBusinessProblem
- dealname → Project.Title
"""

import json
import os
from datetime import datetime

from common.base_handler import BaseLambdaHandler
from common.aws_client import PARTNER_CENTRAL_CATALOG
from common.mappers import hubspot_deal_to_partner_central_updates

# Properties that trigger Partner Central sync
SYNCED_PROPERTIES = {
    "dealstage",
    "closedate",
    "amount",
    "description",
    "dealname",
    "hs_deal_description",
    "deal_currency_code",
}


class HubSpotDealUpdateSyncHandler(BaseLambdaHandler):
    """Handler for syncing HubSpot deal property changes to Partner Central."""

    def _execute(self, event: dict, context: dict) -> dict:
        """
        Process HubSpot deal.propertyChange webhook.

        Event structure from HubSpot:
        {
            "objectId": 12345,
            "propertyName": "dealstage",
            "propertyValue": "presentationscheduled",
            "subscriptionType": "deal.propertyChange",
            "occurredAt": 1708257600000
        }
        """
        # Verify webhook signature if secret is configured
        if "headers" in event:
            self._verify_webhook_signature(event)

        # Parse webhook payload
        if "body" in event:
            body = (
                json.loads(event["body"])
                if isinstance(event["body"], str)
                else event["body"]
            )
        else:
            body = event

        # Extract event details
        deal_id = str(body.get("objectId"))
        property_name = body.get("propertyName")
        property_value = body.get("propertyValue")

        self.logger.info(
            "Deal %s property changed: %s = %s", deal_id, property_name, property_value
        )

        # Only sync if this property should trigger Partner Central update
        if property_name not in SYNCED_PROPERTIES:
            self.logger.info("Property %s not in sync list, skipping", property_name)
            return self._success_response(
                {"status": "success", "message": "Property not synced"}
            )

        # Get full deal to check if it has a Partner Central opportunity
        deal = self.hubspot_client.get_deal(deal_id)

        aws_opportunity_id = deal.get("properties", {}).get("aws_opportunity_id")
        if not aws_opportunity_id:
            self.logger.info(
                "Deal %s has no aws_opportunity_id, skipping sync", deal_id
            )
            return self._success_response(
                {
                    "status": "success",
                    "message": "No Partner Central opportunity linked",
                }
            )

        # Get deal with associations for full mapping
        deal, company, contacts = self.hubspot_client.get_deal_with_associations(
            deal_id
        )

        # Map HubSpot deal updates to Partner Central UpdateOpportunity request
        update_payload = hubspot_deal_to_partner_central_updates(
            deal, company, contacts, property_name, property_value
        )

        if not update_payload:
            self.logger.info(
                "No Partner Central updates needed for property %s", property_name
            )
            return self._success_response(
                {"status": "success", "message": "No updates required"}
            )

        # Update Partner Central opportunity
        self.logger.info(
            "Updating Partner Central opportunity %s: %s",
            aws_opportunity_id,
            json.dumps(update_payload, default=str),
        )

        self.pc_client.update_opportunity(
            Catalog=PARTNER_CENTRAL_CATALOG,
            Identifier=aws_opportunity_id,
            **update_payload,
        )

        self.logger.info("Partner Central opportunity updated successfully")

        # Add note to HubSpot deal documenting the sync
        note_text = (
            f"🔄 Synced to AWS Partner Central\n\n"
            f"Property: {property_name}\n"
            f"Value: {property_value}\n"
            f"Opportunity ID: {aws_opportunity_id}\n"
            f"Timestamp: {datetime.utcnow().isoformat()}Z"
        )
        self.hubspot_client.add_note_to_deal(deal_id, note_text)

        # Update sync status
        self.hubspot_client.update_deal(
            deal_id,
            {
                "aws_sync_status": "synced",
                "aws_last_sync_date": datetime.utcnow().isoformat(),
            },
        )

        return self._success_response(
            {"status": "success", "message": "Opportunity updated in Partner Central"}
        )

    def _verify_webhook_signature(self, event: dict) -> None:
        """Verify HubSpot webhook signature if secret is configured."""
        webhook_secret = os.environ.get("HUBSPOT_WEBHOOK_SECRET")
        if not webhook_secret:
            return

        import hmac
        import hashlib

        signature = event.get("headers", {}).get("x-hubspot-signature-v3") or event.get(
            "headers", {}
        ).get("X-HubSpot-Signature-V3")

        if not signature:
            self.logger.warning("No signature provided in webhook")
            return

        body = event.get("body", "")
        if isinstance(body, dict):
            body = json.dumps(body)

        method = event.get("requestContext", {}).get("http", {}).get("method", "POST")
        source_url = event.get("headers", {}).get("x-hubspot-request-url", "")
        timestamp = event.get("headers", {}).get("x-hubspot-request-timestamp", "")

        message = f"{method}{source_url}{body}{timestamp}"
        expected_sig = hmac.new(
            webhook_secret.encode(), message.encode(), hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(signature, expected_sig):
            raise ValueError("Invalid webhook signature")


def lambda_handler(event: dict, context) -> dict:
    """Lambda entry point."""
    handler = HubSpotDealUpdateSyncHandler()
    return handler.handle(event, context)
