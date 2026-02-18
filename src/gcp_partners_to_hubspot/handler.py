"""
Lambda handler: Google Cloud CRM Partners API → HubSpot (Scheduled)

Polls GCP Partners API for new opportunities and syncs them to HubSpot.

This handler runs on a scheduled basis (e.g., every 5-15 minutes) and:
1. Lists opportunities from GCP Partners API that haven't been synced yet
2. Creates corresponding HubSpot deals with #GCP tag
3. Writes the GCP opportunity ID back to HubSpot for tracking
4. Updates existing HubSpot deals if GCP opportunities have changed

Note: Unlike AWS Partner Central which has "invitations" to accept,
GCP Partners API uses a simpler model where opportunities can be
directly queried. We filter by opportunities without an externalSystemId
or those that need updates.
"""

import json
import logging
import sys

sys.path.insert(0, "/var/task")

from common.gcp_client import get_gcp_partners_client, get_partner_id
from common.hubspot_client import HubSpotClient
from common.gcp_mappers import gcp_opportunity_to_hubspot_deal

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def lambda_handler(event: dict, context) -> dict:
    """
    Scheduled Lambda handler that polls GCP Partners API for opportunities
    and syncs them to HubSpot.
    """
    logger.info("Starting GCP → HubSpot sync")

    try:
        hubspot = HubSpotClient()
        gcp_client = get_gcp_partners_client()
        partner_id = get_partner_id()

        # List opportunities from GCP Partners API
        parent = f"partners/{partner_id}"
        opportunities_response = (
            gcp_client.partners()
            .opportunities()
            .list(parent=parent, pageSize=100)  # Adjust based on expected volume
            .execute()
        )

        opportunities = opportunities_response.get("opportunities", [])
        logger.info("Found %d opportunities in GCP Partners API", len(opportunities))

        synced = []
        skipped = []
        errors = []

        for opportunity in opportunities:
            try:
                result = _sync_opportunity_to_hubspot(opportunity, hubspot, gcp_client)
                if result:
                    synced.append(result)
                else:
                    skipped.append(opportunity.get("name"))
            except Exception as exc:
                logger.exception(
                    "Error syncing opportunity %s: %s", opportunity.get("name"), exc
                )
                errors.append(
                    {"opportunityName": opportunity.get("name"), "error": str(exc)}
                )

        logger.info(
            "GCP → HubSpot sync complete: %d synced, %d skipped, %d errors",
            len(synced),
            len(skipped),
            len(errors),
        )

        return {
            "statusCode": 200,
            "body": json.dumps(
                {
                    "synced": len(synced),
                    "skipped": len(skipped),
                    "errors": len(errors),
                    "results": synced,
                    "errorDetails": errors,
                },
                default=str,
            ),
        }

    except Exception as exc:
        logger.exception("Fatal error in GCP → HubSpot sync: %s", exc)
        return {"statusCode": 500, "body": json.dumps({"error": str(exc)}, default=str)}


def _sync_opportunity_to_hubspot(
    opportunity: dict, hubspot: HubSpotClient, gcp_client
) -> dict | None:
    """
    Sync a single GCP opportunity to HubSpot.

    Logic:
    1. If opportunity has externalSystemId starting with "hubspot-deal-",
       it originated from HubSpot → skip to avoid circular sync
    2. Otherwise, check if a HubSpot deal already exists with this GCP opportunity ID
    3. If deal exists, update it; if not, create a new deal

    Args:
        opportunity: GCP opportunity dict
        hubspot: HubSpot client
        gcp_client: GCP Partners API client

    Returns:
        Result dict if synced, None if skipped
    """
    opp_name = opportunity.get("name", "")
    opp_id = opp_name.split("/")[-1] if "/" in opp_name else opp_name
    external_id = opportunity.get("externalSystemId", "")

    logger.info("Processing GCP opportunity %s (external ID: %s)", opp_id, external_id)

    # Skip opportunities that originated from HubSpot (avoid circular sync)
    if external_id and external_id.startswith("hubspot-deal-"):
        logger.info("Opportunity %s originated from HubSpot — skipping", opp_id)
        return None

    # Fetch the associated lead for company information
    lead_name = opportunity.get("lead")
    lead = None
    if lead_name:
        try:
            lead = gcp_client.partners().leads().get(name=lead_name).execute()
        except Exception as exc:
            logger.warning("Could not fetch lead %s: %s", lead_name, exc)

    # Map GCP opportunity to HubSpot deal properties
    deal_properties = gcp_opportunity_to_hubspot_deal(opportunity, lead)

    # Check if deal already exists in HubSpot
    existing_deal = _find_hubspot_deal_by_gcp_id(hubspot, opp_id)

    if existing_deal:
        # Update existing deal
        deal_id = existing_deal["id"]
        logger.info(
            "Updating existing HubSpot deal %s for GCP opportunity %s", deal_id, opp_id
        )

        hubspot.update_deal(deal_id, deal_properties)

        return {
            "action": "updated",
            "hubspotDealId": deal_id,
            "gcpOpportunityId": opp_id,
            "gcpOpportunityName": opp_name,
        }
    else:
        # Create new deal
        logger.info("Creating new HubSpot deal for GCP opportunity %s", opp_id)

        created_deal = hubspot.create_deal(deal_properties)
        deal_id = created_deal["id"]

        # Optionally associate contacts/company if available in lead
        if lead:
            _associate_lead_contacts_to_deal(hubspot, deal_id, lead)

        return {
            "action": "created",
            "hubspotDealId": deal_id,
            "gcpOpportunityId": opp_id,
            "gcpOpportunityName": opp_name,
        }


def _find_hubspot_deal_by_gcp_id(
    hubspot: HubSpotClient, gcp_opp_id: str
) -> dict | None:
    """
    Search for a HubSpot deal that has the given GCP opportunity ID.

    Args:
        hubspot: HubSpot client
        gcp_opp_id: GCP opportunity ID to search for

    Returns:
        Deal dict if found, None otherwise
    """
    try:
        # Search for deals with matching gcp_opportunity_id property
        search_request = {
            "filterGroups": [
                {
                    "filters": [
                        {
                            "propertyName": "gcp_opportunity_id",
                            "operator": "EQ",
                            "value": gcp_opp_id,
                        }
                    ]
                }
            ],
            "properties": ["dealname", "gcp_opportunity_id", "dealstage"],
            "limit": 1,
        }

        response = hubspot.search_deals(search_request)
        results = response.get("results", [])

        return results[0] if results else None

    except Exception as exc:
        logger.warning("Error searching for deal with GCP ID %s: %s", gcp_opp_id, exc)
        return None


def _associate_lead_contacts_to_deal(
    hubspot: HubSpotClient, deal_id: str, lead: dict
) -> None:
    """
    Associate contacts from GCP lead to HubSpot deal.

    This is a best-effort operation - if it fails, we log but don't fail the sync.

    Args:
        hubspot: HubSpot client
        deal_id: HubSpot deal ID
        lead: GCP lead dict
    """
    try:
        contact_info = lead.get("contact", {})
        email = contact_info.get("email")

        if not email:
            logger.debug("Lead has no contact email — skipping contact association")
            return

        # Try to find or create contact in HubSpot
        # Search for contact by email
        search_request = {
            "filterGroups": [
                {
                    "filters": [
                        {"propertyName": "email", "operator": "EQ", "value": email}
                    ]
                }
            ],
            "properties": ["firstname", "lastname", "email"],
            "limit": 1,
        }

        response = hubspot.search_contacts(search_request)
        results = response.get("results", [])

        if results:
            contact_id = results[0]["id"]
            logger.info("Found existing contact %s for email %s", contact_id, email)
        else:
            # Create new contact
            contact_props = {
                "email": email,
                "firstname": contact_info.get("givenName", ""),
                "lastname": contact_info.get("familyName", ""),
            }
            if contact_info.get("phone"):
                contact_props["phone"] = contact_info["phone"]

            created_contact = hubspot.create_contact(contact_props)
            contact_id = created_contact["id"]
            logger.info("Created new contact %s for email %s", contact_id, email)

        # Associate contact with deal
        hubspot.associate_contact_to_deal(deal_id, contact_id)
        logger.info("Associated contact %s with deal %s", contact_id, deal_id)

    except Exception as exc:
        logger.warning("Could not associate lead contacts to deal %s: %s", deal_id, exc)
