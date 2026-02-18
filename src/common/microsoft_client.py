"""
Microsoft Partner Center client for Referrals API.

Authentication uses Azure AD App+User flow with delegated permissions.
Users must have Referral Admin or Referral User roles in Partner Center.
"""

import os
import logging
import requests
from typing import Optional
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

PARTNER_CENTER_API_BASE = "https://api.partner.microsoft.com/v1.0"


class MicrosoftPartnerCenterClient:
    """
    Client for Microsoft Partner Center Referrals API.
    Handles authentication and CRUD operations for referrals (opportunities).
    """

    def __init__(self, access_token: Optional[str] = None):
        """
        Initialize the Microsoft Partner Center client.
        
        Args:
            access_token: Azure AD access token with Partner Center API permissions.
                         If not provided, reads from MICROSOFT_ACCESS_TOKEN env var.
        """
        self.access_token = access_token or os.environ.get("MICROSOFT_ACCESS_TOKEN")
        if not self.access_token:
            raise ValueError("Microsoft access token is required")
        
        self.base_url = PARTNER_CENTER_API_BASE
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        })

    def create_referral(self, referral_data: dict) -> dict:
        """
        Create a new referral (opportunity) in Microsoft Partner Center.
        
        Args:
            referral_data: Referral payload with required fields:
                - name: Referral name
                - type: "Independent" or "Shared"
                - customerProfile: Customer information
                - consent: Sharing consent
                - details: Deal details (value, currency, close date)
        
        Returns:
            Created referral with id, eTag, and all fields
        
        Raises:
            requests.HTTPError: If the API request fails
        """
        url = f"{self.base_url}/engagements/referrals"
        logger.info("Creating Microsoft referral: %s", referral_data.get("name"))
        
        response = self.session.post(url, json=referral_data)
        response.raise_for_status()
        
        referral = response.json()
        logger.info("Created Microsoft referral with ID: %s", referral.get("id"))
        return referral

    def update_referral(self, referral_id: str, updates: dict, etag: str) -> dict:
        """
        Update an existing referral (partial update/PATCH).
        
        Args:
            referral_id: The referral ID
            updates: Dictionary of fields to update (only changed fields)
            etag: Current eTag value from the referral (for concurrency control)
        
        Returns:
            Updated referral with new eTag
        
        Raises:
            requests.HTTPError: If the API request fails (including eTag mismatch)
        """
        url = f"{self.base_url}/engagements/referrals/{referral_id}"
        logger.info("Updating Microsoft referral %s", referral_id)
        
        # Add eTag to headers for optimistic concurrency
        headers = {"If-Match": etag}
        
        response = self.session.patch(url, json=updates, headers=headers)
        response.raise_for_status()
        
        referral = response.json()
        logger.info("Updated Microsoft referral %s", referral_id)
        return referral

    def get_referral(self, referral_id: str) -> dict:
        """
        Get a referral by ID.
        
        Args:
            referral_id: The referral ID
        
        Returns:
            Referral data including id, eTag, and all fields
        
        Raises:
            requests.HTTPError: If the referral is not found or request fails
        """
        url = f"{self.base_url}/engagements/referrals/{referral_id}"
        logger.debug("Fetching Microsoft referral %s", referral_id)
        
        response = self.session.get(url)
        response.raise_for_status()
        
        return response.json()

    def list_referrals(
        self,
        status: Optional[str] = None,
        substatus: Optional[str] = None,
        order_by: str = "createdDateTime desc",
        top: int = 100,
        skip: int = 0,
    ) -> list[dict]:
        """
        List referrals with optional filtering.
        
        Args:
            status: Filter by status (New, Active, Closed)
            substatus: Filter by substatus (Pending, Received, Accepted, etc.)
            order_by: OData ordering (default: newest first)
            top: Number of results to return (max 100)
            skip: Number of results to skip (for pagination)
        
        Returns:
            List of referral objects
        
        Raises:
            requests.HTTPError: If the API request fails
        """
        url = f"{self.base_url}/engagements/referrals"
        
        params = {
            "$orderby": order_by,
            "$top": min(top, 100),
            "$skip": skip,
        }
        
        filters = []
        if status:
            filters.append(f"status eq '{status}'")
        if substatus:
            filters.append(f"substatus eq '{substatus}'")
        
        if filters:
            params["$filter"] = " and ".join(filters)
        
        logger.info("Listing Microsoft referrals with params: %s", params)
        
        response = self.session.get(url, params=params)
        response.raise_for_status()
        
        data = response.json()
        referrals = data.get("value", [])
        logger.info("Retrieved %d Microsoft referrals", len(referrals))
        return referrals

    def close(self):
        """Close the HTTP session."""
        self.session.close()


def get_microsoft_client() -> MicrosoftPartnerCenterClient:
    """
    Factory function to create a Microsoft Partner Center client.
    Reads access token from environment variables.
    
    Returns:
        MicrosoftPartnerCenterClient instance
    """
    return MicrosoftPartnerCenterClient()
