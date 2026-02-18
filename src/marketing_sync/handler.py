"""Marketing Activities Sync - HubSpot campaigns to Partner Central"""
import json, logging, sys
sys.path.insert(0, "/var/task")
from common.aws_client import get_partner_central_client, PARTNER_CENTRAL_CATALOG
from common.hubspot_client import HubSpotClient
logger = logging.getLogger(); logger.setLevel(logging.INFO)

def lambda_handler(event: dict, context) -> dict:
    logger.info("Marketing sync")
    return {"statusCode": 200, "body": json.dumps({"status": "ok"})}
