"""
HubSpot API client wrapper for deal/opportunity management.
Includes association fetching to pull company and contact data
needed for complete Partner Central Opportunity payloads.
"""

import os
import hmac
import hashlib
import logging
import requests
from typing import Optional

logger = logging.getLogger(__name__)

HUBSPOT_API_BASE = "https://api.hubapi.com"


class HubSpotClient:
    def __init__(self, access_token: Optional[str] = None):
        self.access_token = access_token or os.environ["HUBSPOT_ACCESS_TOKEN"]
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json",
            }
        )

    # ------------------------------------------------------------------
    # Deal CRUD
    # ------------------------------------------------------------------

    def get_deal(self, deal_id: str) -> dict:
        """Fetch a deal by ID with all relevant properties."""
        url = f"{HUBSPOT_API_BASE}/crm/v3/objects/deals/{deal_id}"
        params = {
            "properties": (
                "dealname,amount,closedate,dealstage,pipeline,description,"
                "hs_deal_description,hs_next_step,deal_currency_code,dealtype,"
                "company,website,industry,country,city,state,zip,address,"
                "aws_opportunity_id,aws_opportunity_arn,aws_opportunity_title,"
                "aws_review_status,aws_sync_status,aws_invitation_id,"
                "aws_industry,aws_delivery_models,aws_primary_needs,"
                "aws_use_case,aws_expected_spend"
            )
        }
        response = self.session.get(url, params=params)
        response.raise_for_status()
        return response.json()

    def get_deal_with_associations(self, deal_id: str) -> tuple[dict, Optional[dict], list[dict]]:
        """
        Fetch a deal plus its associated company and contacts.

        Returns:
            (deal, company, contacts)
            - deal: the deal object
            - company: the primary associated company object (or None)
            - contacts: list of associated contact objects (up to 10)
        """
        deal = self.get_deal(deal_id)

        # Fetch associated company IDs
        company = None
        company_ids = self._get_association_ids(deal_id, "deals", "companies")
        if company_ids:
            try:
                company = self.get_company(company_ids[0])
            except Exception as e:
                logger.warning("Could not fetch company %s: %s", company_ids[0], e)

        # Fetch associated contact IDs (max 10 for PC)
        contacts = []
        contact_ids = self._get_association_ids(deal_id, "deals", "contacts")
        for cid in contact_ids[:10]:
            try:
                contacts.append(self.get_contact(cid))
            except Exception as e:
                logger.warning("Could not fetch contact %s: %s", cid, e)

        return deal, company, contacts

    def create_deal(self, properties: dict) -> dict:
        """Create a new deal in HubSpot."""
        url = f"{HUBSPOT_API_BASE}/crm/v3/objects/deals"
        payload = {"properties": properties}
        response = self.session.post(url, json=payload)
        response.raise_for_status()
        result = response.json()
        logger.info("Created HubSpot deal: %s", result["id"])
        return result

    def update_deal(self, deal_id: str, properties: dict) -> dict:
        """Update an existing deal's properties."""
        url = f"{HUBSPOT_API_BASE}/crm/v3/objects/deals/{deal_id}"
        payload = {"properties": properties}
        response = self.session.patch(url, json=payload)
        response.raise_for_status()
        return response.json()

    def add_note_to_deal(self, deal_id: str, note_body: str) -> dict:
        """
        Create a Note engagement and associate it with a deal.
        Used to surface warnings (e.g. title-immutability) back to sales reps.
        """
        # 1. Create the note
        note_url = f"{HUBSPOT_API_BASE}/crm/v3/objects/notes"
        from datetime import datetime, timezone
        payload = {
            "properties": {
                "hs_note_body": note_body,
                "hs_timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
        }
        response = self.session.post(note_url, json=payload)
        response.raise_for_status()
        note_id = response.json()["id"]

        # 2. Associate the note with the deal
        assoc_url = (
            f"{HUBSPOT_API_BASE}/crm/v3/objects/notes/{note_id}"
            f"/associations/deals/{deal_id}/note_to_deal"
        )
        self.session.put(assoc_url)
        return {"noteId": note_id}

    def search_deals_by_aws_opportunity_id(self, aws_opportunity_id: str) -> list:
        """Search for deals that already have a given AWS opportunity ID."""
        url = f"{HUBSPOT_API_BASE}/crm/v3/objects/deals/search"
        payload = {
            "filterGroups": [
                {
                    "filters": [
                        {
                            "propertyName": "aws_opportunity_id",
                            "operator": "EQ",
                            "value": aws_opportunity_id,
                        }
                    ]
                }
            ],
            "properties": ["dealname", "aws_opportunity_id"],
            "limit": 1,
        }
        response = self.session.post(url, json=payload)
        response.raise_for_status()
        return response.json().get("results", [])

    def search_deals_by_aws_invitation_id(self, invitation_id: str) -> list:
        """Search for deals that were created from a specific PC invitation."""
        url = f"{HUBSPOT_API_BASE}/crm/v3/objects/deals/search"
        payload = {
            "filterGroups": [
                {
                    "filters": [
                        {
                            "propertyName": "aws_invitation_id",
                            "operator": "EQ",
                            "value": invitation_id,
                        }
                    ]
                }
            ],
            "properties": ["dealname", "aws_invitation_id"],
            "limit": 1,
        }
        response = self.session.post(url, json=payload)
        response.raise_for_status()
        return response.json().get("results", [])

    # ------------------------------------------------------------------
    # Company
    # ------------------------------------------------------------------

    def get_company(self, company_id: str) -> dict:
        """Fetch a company by ID."""
        url = f"{HUBSPOT_API_BASE}/crm/v3/objects/companies/{company_id}"
        params = {
            "properties": (
                "name,domain,website,industry,country,city,state,zip,address,"
                "phone,numberofemployees,annualrevenue"
            )
        }
        response = self.session.get(url, params=params)
        response.raise_for_status()
        return response.json()

    # ------------------------------------------------------------------
    # Contact
    # ------------------------------------------------------------------

    def get_contact(self, contact_id: str) -> dict:
        """Fetch a contact by ID."""
        url = f"{HUBSPOT_API_BASE}/crm/v3/objects/contacts/{contact_id}"
        params = {
            "properties": "firstname,lastname,email,phone,mobilephone,jobtitle"
        }
        response = self.session.get(url, params=params)
        response.raise_for_status()
        return response.json()

    # ------------------------------------------------------------------
    # Associations
    # ------------------------------------------------------------------

    def _get_association_ids(self, object_id: str, from_type: str, to_type: str) -> list[str]:
        """Return a list of associated object IDs."""
        url = f"{HUBSPOT_API_BASE}/crm/v3/associations/{from_type}/{to_type}/batch/read"
        payload = {"inputs": [{"id": object_id}]}
        try:
            response = self.session.post(url, json=payload)
            response.raise_for_status()
            results = response.json().get("results", [])
            if not results:
                return []
            return [assoc["id"] for assoc in results[0].get("to", [])]
        except Exception as e:
            logger.warning("Could not fetch %s→%s associations for %s: %s", from_type, to_type, object_id, e)
            return []

    # ------------------------------------------------------------------
    # Custom properties setup (one-time)
    # ------------------------------------------------------------------

    def create_custom_properties(self) -> list[str]:
        """
        Ensure all required custom properties exist on the Deal object.
        Call this once during initial setup / deployment.
        """
        properties_to_create = [
            # AWS sync metadata
            {
                "name": "aws_opportunity_id",
                "label": "AWS Opportunity ID",
                "type": "string",
                "fieldType": "text",
                "groupName": "dealinformation",
                "description": "The AWS Partner Central Opportunity ID (e.g. O1234567)",
            },
            {
                "name": "aws_opportunity_arn",
                "label": "AWS Opportunity ARN",
                "type": "string",
                "fieldType": "text",
                "groupName": "dealinformation",
                "description": "The AWS Partner Central Opportunity ARN",
            },
            {
                "name": "aws_opportunity_title",
                "label": "AWS Opportunity Title",
                "type": "string",
                "fieldType": "text",
                "groupName": "dealinformation",
                "description": (
                    "The canonical opportunity title as it exists in Partner Central. "
                    "This is immutable after submission — if the HubSpot deal name differs, "
                    "the title will NOT be updated in Partner Central."
                ),
            },
            {
                "name": "aws_review_status",
                "label": "AWS Review Status",
                "type": "string",
                "fieldType": "text",
                "groupName": "dealinformation",
                "description": "AWS Partner Central Lifecycle.ReviewStatus",
            },
            {
                "name": "aws_sync_status",
                "label": "AWS Sync Status",
                "type": "string",
                "fieldType": "text",
                "groupName": "dealinformation",
                "description": "Sync status with AWS Partner Central (synced / error / blocked)",
            },
            {
                "name": "aws_invitation_id",
                "label": "AWS Invitation ID",
                "type": "string",
                "fieldType": "text",
                "groupName": "dealinformation",
                "description": "AWS Partner Central Engagement Invitation ID (AWS-originated deals only)",
            },
            # Partner-configurable fields for richer PC payloads
            {
                "name": "aws_industry",
                "label": "AWS Industry Override",
                "type": "string",
                "fieldType": "text",
                "groupName": "dealinformation",
                "description": (
                    "Override the industry sent to Partner Central. "
                    "Must be a valid PC industry value (e.g. 'Software and Internet', 'Healthcare'). "
                    "If blank, the value is derived from the associated company."
                ),
            },
            {
                "name": "aws_delivery_models",
                "label": "AWS Delivery Models",
                "type": "string",
                "fieldType": "text",
                "groupName": "dealinformation",
                "description": (
                    "Comma-separated delivery models for Partner Central. "
                    "Valid values: SaaS or PaaS, BYOL or AMI, Managed Services, "
                    "Professional Services, Resell, Other"
                ),
            },
            {
                "name": "aws_primary_needs",
                "label": "AWS Primary Needs",
                "type": "string",
                "fieldType": "text",
                "groupName": "dealinformation",
                "description": (
                    "Comma-separated primary needs from AWS for Partner Central co-sell. "
                    "e.g. 'Co-Sell - Deal Support,Co-Sell - Technical Consultation'"
                ),
            },
            {
                "name": "aws_use_case",
                "label": "AWS Customer Use Case",
                "type": "string",
                "fieldType": "text",
                "groupName": "dealinformation",
                "description": "AWS Partner Central CustomerUseCase value for this deal.",
            },
            {
                "name": "aws_expected_spend",
                "label": "AWS Expected Monthly Spend (USD)",
                "type": "number",
                "fieldType": "number",
                "groupName": "dealinformation",
                "description": (
                    "Expected monthly AWS spend for this deal. Used as "
                    "Project.ExpectedCustomerSpend[0].Amount in Partner Central."
                ),
            },
            # New properties for enhanced features
            {
                "name": "aws_engagement_score",
                "label": "AWS Engagement Score",
                "type": "number",
                "fieldType": "number",
                "groupName": "dealinformation",
                "description": (
                    "AWS's engagement score for this opportunity (0-100). "
                    "Higher scores indicate stronger AWS interest in co-selling."
                ),
            },
            {
                "name": "aws_submission_date",
                "label": "AWS Submission Date",
                "type": "datetime",
                "fieldType": "date",
                "groupName": "dealinformation",
                "description": "Date when the opportunity was submitted to AWS for review",
            },
            {
                "name": "aws_involvement_type",
                "label": "AWS Involvement Type",
                "type": "string",
                "fieldType": "text",
                "groupName": "dealinformation",
                "description": "Co-Sell or For Visibility Only",
            },
            {
                "name": "aws_visibility",
                "label": "AWS Visibility",
                "type": "string",
                "fieldType": "text",
                "groupName": "dealinformation",
                "description": "Full or Limited - how much data AWS can see",
            },
            {
                "name": "aws_seller_name",
                "label": "AWS Seller Name",
                "type": "string",
                "fieldType": "text",
                "groupName": "dealinformation",
                "description": "Name of the AWS seller assigned to this opportunity",
            },
            {
                "name": "aws_next_steps",
                "label": "AWS Recommended Next Steps",
                "type": "string",
                "fieldType": "textarea",
                "groupName": "dealinformation",
                "description": "Next steps recommended by AWS",
            },
            {
                "name": "aws_last_summary_sync",
                "label": "AWS Last Summary Sync",
                "type": "datetime",
                "fieldType": "date",
                "groupName": "dealinformation",
                "description": "Last time AWS Opportunity Summary was fetched",
            },
            {
                "name": "aws_solution_ids",
                "label": "AWS Solution IDs (Override)",
                "type": "string",
                "fieldType": "text",
                "groupName": "dealinformation",
                "description": (
                    "Comma-separated Solution IDs to associate with this opportunity. "
                    "If blank, solutions are auto-matched based on use case."
                ),
            },
        ]

        url = f"{HUBSPOT_API_BASE}/crm/v3/properties/deals"
        created = []
        for prop in properties_to_create:
            try:
                response = self.session.post(url, json=prop)
                if response.status_code == 409:
                    logger.info("Property already exists: %s", prop["name"])
                else:
                    response.raise_for_status()
                    created.append(prop["name"])
                    logger.info("Created property: %s", prop["name"])
            except requests.HTTPError as e:
                logger.warning("Could not create property %s: %s", prop["name"], e)

        return created

    # ------------------------------------------------------------------
    # Webhook signature verification
    # ------------------------------------------------------------------

    def verify_webhook_signature(self, payload: bytes, signature: str, secret: str) -> bool:
        """Verify HubSpot webhook v3 HMAC-SHA256 signature."""
        expected = hmac.new(
            secret.encode("utf-8"), payload, hashlib.sha256
        ).hexdigest()
        sig_clean = signature.lstrip("sha256=")
        return hmac.compare_digest(expected, sig_clean)
