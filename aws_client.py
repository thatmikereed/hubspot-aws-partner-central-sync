"""
AWS client factory with IAM role assumption for HubSpotPartnerCentralServiceRole.
All Partner Central API calls are made through this assumed role.
"""

import os
import boto3
import logging
from datetime import datetime, timezone
from functools import lru_cache
from typing import Optional

logger = logging.getLogger(__name__)

ROLE_NAME = "HubSpotPartnerCentralServiceRole"
ROLE_SESSION_NAME = "HubSpotPartnerCentralSession"
PARTNER_CENTRAL_CATALOG = "AWS"


def get_assumed_role_credentials(role_arn: Optional[str] = None) -> dict:
    """
    Assume the HubSpotPartnerCentralServiceRole and return temporary credentials.
    The role ARN is built from the current account if not explicitly provided.
    """
    sts_client = boto3.client("sts")

    if not role_arn:
        account_id = sts_client.get_caller_identity()["Account"]
        role_arn = f"arn:aws:iam::{account_id}:role/{ROLE_NAME}"

    logger.info(f"Assuming role: {role_arn}")

    response = sts_client.assume_role(
        RoleArn=role_arn,
        RoleSessionName=ROLE_SESSION_NAME,
        DurationSeconds=3600,
    )

    return response["Credentials"]


def get_partner_central_client(region: str = None):
    """
    Return a boto3 client for AWS Partner Central Selling API,
    authenticated via the assumed HubSpotPartnerCentralServiceRole.
    """
    region = region or os.environ.get("AWS_REGION", "us-east-1")
    role_arn = os.environ.get("PARTNER_CENTRAL_ROLE_ARN")

    credentials = get_assumed_role_credentials(role_arn)

    client = boto3.client(
        "partnercentral-selling",
        region_name=region,
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"],
    )

    logger.info("Partner Central client created via assumed role")
    return client
