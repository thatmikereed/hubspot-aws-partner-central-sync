"""
HubSpot API client wrapper for deal/opportunity management.
"""

import os
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

    def get_deal(self, deal_id: str) -> dict:
        """Fetch a deal by ID with all standard properties."""
        url = f"{HUBSPOT_API_BASE}/crm/v3/objects/deals/{deal_id}"
        params = {
            "properties": "dealname,amount,closedate,dealstage,pipeline,description,hs_object_id,aws_opportunity_id,aws_opportunity_arn"
        }
        response = self.session.get(url, params=params)
        response.raise_for_status()
        return response.json()

    def create_deal(self, properties: dict) -> dict:
        """Create a new deal in HubSpot."""
        url = f"{HUBSPOT_API_BASE}/crm/v3/objects/deals"
        payload = {"properties": properties}
        response = self.session.post(url, json=payload)
        response.raise_for_status()
        result = response.json()
        logger.info(f"Created HubSpot deal: {result['id']}")
        return result

    def update_deal(self, deal_id: str, properties: dict) -> dict:
        """Update an existing deal's properties."""
        url = f"{HUBSPOT_API_BASE}/crm/v3/objects/deals/{deal_id}"
        payload = {"properties": properties}
        response = self.session.patch(url, json=payload)
        response.raise_for_status()
        return response.json()

    def search_deals_by_aws_opportunity_id(self, aws_opportunity_id: str) -> list:
        """Search for deals that already have a given AWS opportunity ID to avoid duplicates."""
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

    def create_custom_properties(self):
        """
        Ensure required custom properties exist on the Deal object.
        Call this once during initial setup.
        """
        properties_to_create = [
            {
                "name": "aws_opportunity_id",
                "label": "AWS Opportunity ID",
                "type": "string",
                "fieldType": "text",
                "groupName": "dealinformation",
                "description": "The AWS Partner Central Opportunity ID",
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
                "name": "aws_sync_status",
                "label": "AWS Sync Status",
                "type": "string",
                "fieldType": "text",
                "groupName": "dealinformation",
                "description": "Sync status with AWS Partner Central",
            },
            {
                "name": "aws_invitation_id",
                "label": "AWS Invitation ID",
                "type": "string",
                "fieldType": "text",
                "groupName": "dealinformation",
                "description": "AWS Partner Central Invitation ID (if deal originated from AWS)",
            },
        ]

        url = f"{HUBSPOT_API_BASE}/crm/v3/properties/deals"
        created = []
        for prop in properties_to_create:
            try:
                response = self.session.post(url, json=prop)
                if response.status_code == 409:
                    logger.info(f"Property already exists: {prop['name']}")
                else:
                    response.raise_for_status()
                    created.append(prop["name"])
                    logger.info(f"Created property: {prop['name']}")
            except requests.HTTPError as e:
                logger.warning(f"Could not create property {prop['name']}: {e}")

        return created

    def verify_webhook_signature(self, payload: bytes, signature: str, secret: str) -> bool:
        """Verify HubSpot webhook HMAC signature."""
        import hmac
        import hashlib

        expected = hmac.new(
            secret.encode("utf-8"), payload, hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected, signature.lstrip("sha256="))
