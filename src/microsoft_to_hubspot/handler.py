"""
Lambda handler: Microsoft Partner Center → HubSpot (Scheduled)

Polls Microsoft Partner Center for new or updated referrals and syncs them
to HubSpot as deals. Runs on a schedule (e.g., every 5-15 minutes).

This handler:
  1. Lists referrals from Microsoft Partner Center with recent activity
  2. For each referral not already synced to HubSpot, creates a new deal
  3. For referrals already synced, updates the HubSpot deal if status changed
  4. Uses microsoft_referral_id custom property for idempotency
"""

from datetime import datetime, timezone

from common.base_handler import BaseLambdaHandler
from common.microsoft_client import get_microsoft_client
from common.microsoft_mappers import microsoft_referral_to_hubspot_deal


class MicrosoftToHubSpotHandler(BaseLambdaHandler):
    """Handler for Microsoft Partner Center → HubSpot scheduled sync"""

    def __init__(self):
        super().__init__()
        self._microsoft_client = None

    @property
    def microsoft_client(self):
        """Lazy initialization of Microsoft client"""
        if self._microsoft_client is None:
            self._microsoft_client = get_microsoft_client()
        return self._microsoft_client

    def _execute(self, event: dict, context: dict) -> dict:
        """
        Scheduled Lambda handler that polls Microsoft Partner Center for referrals
        and syncs them to HubSpot.
        """
        self.logger.info("Starting Microsoft Partner Center → HubSpot sync")

        created_deals = []
        updated_deals = []
        errors = []

        try:
            # Fetch active referrals from Microsoft (New and Active statuses)
            # We'll process both to catch updates
            referrals = []

            # Get New referrals
            new_referrals = self.microsoft_client.list_referrals(
                status="New", order_by="createdDateTime desc", top=50
            )
            referrals.extend(new_referrals)

            # Get Active referrals
            active_referrals = self.microsoft_client.list_referrals(
                status="Active", order_by="updatedDateTime desc", top=50
            )
            referrals.extend(active_referrals)

            self.logger.info(
                "Retrieved %d referrals from Microsoft Partner Center", len(referrals)
            )

            # Process each referral
            for referral in referrals:
                try:
                    result = self._process_referral(referral)
                    if result:
                        if result["action"] == "created":
                            created_deals.append(result)
                        elif result["action"] == "updated":
                            updated_deals.append(result)
                except Exception as exc:
                    referral_id = referral.get("id", "unknown")
                    self.logger.exception(
                        "Error processing referral %s: %s", referral_id, exc
                    )
                    errors.append(
                        {
                            "referralId": referral_id,
                            "referralName": referral.get("name", ""),
                            "error": str(exc),
                        }
                    )

        except Exception as exc:
            self.logger.exception("Failed to list Microsoft referrals: %s", exc)
            errors.append({"error": f"Failed to list referrals: {exc}"})
        finally:
            self.microsoft_client.close()

        return self._success_response(
            {
                "created": len(created_deals),
                "updated": len(updated_deals),
                "errors": len(errors),
                "createdDeals": created_deals,
                "updatedDeals": updated_deals,
                "errorDetails": errors,
            }
        )

    def _process_referral(self, referral: dict) -> dict | None:
        """
        Process a single Microsoft referral:
        - If not in HubSpot, create a new deal
        - If already in HubSpot, update if status changed
        """
        referral_id = referral.get("id", "")
        referral_name = referral.get("name", "Untitled")
        status = referral.get("status", "New")
        substatus = referral.get("substatus", "Pending")

        self.logger.info(
            "Processing Microsoft referral %s: '%s' (status: %s/%s)",
            referral_id,
            referral_name,
            status,
            substatus,
        )

        # Check if this referral is already synced to HubSpot
        existing_deal = self._find_deal_by_microsoft_id(referral_id)

        if existing_deal:
            # Deal already exists - check if we need to update it
            return self._update_existing_deal(existing_deal, referral)
        else:
            # Create a new HubSpot deal
            return self._create_new_deal(referral)

    def _find_deal_by_microsoft_id(self, referral_id: str) -> dict | None:
        """
        Search HubSpot for a deal with the given microsoft_referral_id.
        Returns the deal if found, None otherwise.
        """
        try:
            # Search for deals with this Microsoft referral ID
            url = f"{self.hubspot_client.base_url}/crm/v3/objects/deals/search"
            payload = {
                "filterGroups": [
                    {
                        "filters": [
                            {
                                "propertyName": "microsoft_referral_id",
                                "operator": "EQ",
                                "value": referral_id,
                            }
                        ]
                    }
                ],
                "properties": [
                    "dealname",
                    "microsoft_referral_id",
                    "microsoft_status",
                    "microsoft_substatus",
                    "dealstage",
                ],
                "limit": 1,
            }

            response = self.hubspot_client.session.post(url, json=payload)
            response.raise_for_status()

            results = response.json().get("results", [])
            if results:
                return results[0]
            return None
        except Exception as exc:
            self.logger.warning(
                "Error searching for deal with Microsoft ID %s: %s", referral_id, exc
            )
            return None

    def _create_new_deal(self, referral: dict) -> dict:
        """
        Create a new HubSpot deal from a Microsoft referral.
        """
        referral_id = referral.get("id", "")

        # Convert Microsoft referral to HubSpot deal properties
        deal_properties = microsoft_referral_to_hubspot_deal(referral)

        self.logger.info(
            "Creating new HubSpot deal for Microsoft referral %s", referral_id
        )

        # Create the deal in HubSpot
        deal = self.hubspot_client.create_deal(deal_properties)
        deal_id = deal.get("id", "")

        self.logger.info(
            "Created HubSpot deal %s for Microsoft referral %s", deal_id, referral_id
        )

        # Add a note to the deal about the sync
        try:
            self.hubspot_client.add_note_to_deal(
                deal_id,
                f"✅ Synced from Microsoft Partner Center\n\n"
                f"Referral ID: {referral_id}\n"
                f"Synced at: {datetime.now(timezone.utc).isoformat()}",
            )
        except Exception as exc:
            self.logger.warning("Could not add note to deal %s: %s", deal_id, exc)

        return {
            "action": "created",
            "hubspotDealId": deal_id,
            "microsoftReferralId": referral_id,
            "dealName": deal_properties.get("dealname", ""),
            "status": referral.get("status", ""),
        }

    def _update_existing_deal(self, existing_deal: dict, referral: dict) -> dict | None:
        """
        Update an existing HubSpot deal if the Microsoft referral status changed.
        """
        deal_id = existing_deal.get("id", "")
        referral_id = referral.get("id", "")

        existing_props = existing_deal.get("properties", {})
        current_status = existing_props.get("microsoft_status", "")
        current_substatus = existing_props.get("microsoft_substatus", "")

        new_status = referral.get("status", "")
        new_substatus = referral.get("substatus", "")

        # Check if status changed
        if current_status == new_status and current_substatus == new_substatus:
            self.logger.debug("Deal %s status unchanged — skipping update", deal_id)
            return None

        self.logger.info(
            "Updating HubSpot deal %s - status changed from %s/%s to %s/%s",
            deal_id,
            current_status,
            current_substatus,
            new_status,
            new_substatus,
        )

        # Convert to HubSpot properties (this will update stage based on status)
        updated_properties = microsoft_referral_to_hubspot_deal(referral)

        # Only update the fields that should change
        update_fields = {
            "microsoft_status": updated_properties.get("microsoft_status"),
            "microsoft_substatus": updated_properties.get("microsoft_substatus"),
            "dealstage": updated_properties.get("dealstage"),
            "microsoft_sync_status": "synced",
        }

        # Update amount if it changed
        new_amount = updated_properties.get("amount")
        if new_amount and new_amount != existing_props.get("amount"):
            update_fields["amount"] = new_amount

        self.hubspot_client.update_deal(deal_id, update_fields)

        self.logger.info(
            "Updated HubSpot deal %s from Microsoft referral %s", deal_id, referral_id
        )

        return {
            "action": "updated",
            "hubspotDealId": deal_id,
            "microsoftReferralId": referral_id,
            "statusChange": f"{current_status}/{current_substatus} → {new_status}/{new_substatus}",
        }


def lambda_handler(event: dict, context) -> dict:
    """Lambda entry point"""
    handler = MicrosoftToHubSpotHandler()
    return handler.handle(event, context)
