"""Referral Management - Track referral opportunities"""
import json, logging, sys
sys.path.insert(0, "/var/task")
from common.aws_client import get_partner_central_client
from common.hubspot_client import HubSpotClient
logger = logging.getLogger(); logger.setLevel(logging.INFO)

def lambda_handler(event: dict, context) -> dict:
    logger.info("Referral mgmt")
    return {"statusCode": 200, "body": json.dumps({"status": "ok"})}
