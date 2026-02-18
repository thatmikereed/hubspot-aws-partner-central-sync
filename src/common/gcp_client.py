"""
Google Cloud CRM Partners API client factory.
All GCP Partners API calls are made through this client with service account authentication.
"""

import os
import logging
from typing import Optional
from google.oauth2 import service_account
from googleapiclient.discovery import build

logger = logging.getLogger(__name__)

# GCP Partners API constants
GCP_PARTNERS_API_SERVICE = "cloudcrmpartners"
GCP_PARTNERS_API_VERSION = "v1"
GCP_SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]


def get_gcp_credentials(service_account_key_path: Optional[str] = None):
    """
    Get GCP service account credentials for Partners API access.

    Args:
        service_account_key_path: Path to service account JSON key file.
            If not provided, uses GOOGLE_APPLICATION_CREDENTIALS env var.

    Returns:
        Service account credentials object
    """
    key_path = service_account_key_path or os.environ.get(
        "GOOGLE_APPLICATION_CREDENTIALS"
    )

    if not key_path:
        raise ValueError(
            "GCP service account credentials not found. "
            "Set GOOGLE_APPLICATION_CREDENTIALS environment variable or provide key path."
        )

    logger.info(f"Loading GCP credentials from: {key_path}")

    credentials = service_account.Credentials.from_service_account_file(
        key_path, scopes=GCP_SCOPES
    )

    return credentials


def get_gcp_partners_client(service_account_key_path: Optional[str] = None):
    """
    Return a Google API client for Cloud CRM Partners API.

    Args:
        service_account_key_path: Optional path to service account JSON key file

    Returns:
        Google API client for Cloud CRM Partners API
    """
    credentials = get_gcp_credentials(service_account_key_path)

    client = build(
        GCP_PARTNERS_API_SERVICE,
        GCP_PARTNERS_API_VERSION,
        credentials=credentials,
        cache_discovery=False,
    )

    logger.info("GCP Partners API client created successfully")
    return client


def get_partner_id() -> str:
    """
    Get the GCP Partner ID from environment variables.

    Returns:
        Partner ID string (e.g., "12345")

    Raises:
        ValueError: If GCP_PARTNER_ID is not set
    """
    partner_id = os.environ.get("GCP_PARTNER_ID")
    if not partner_id:
        raise ValueError(
            "GCP_PARTNER_ID environment variable is required. "
            "This is your Google Cloud Partner ID from the Partners Portal."
        )
    return partner_id
