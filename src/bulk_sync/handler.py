"""Bulk Sync API - Batch migration of deals"""
import json, logging, sys
sys.path.insert(0, "/var/task")
from common.aws_client import get_partner_central_client
from common.hubspot_client import HubSpotClient
logger = logging.getLogger(); logger.setLevel(logging.INFO)

def lambda_handler(event: dict, context) -> dict:
    logger.info("Bulk sync")
    body = json.loads(event.get("body", "{}")) if isinstance(event.get("body"), str) else event
    return {"statusCode": 200, "body": json.dumps({"dryRun": body.get("dryRun", True), "dealsFound": 0})}
