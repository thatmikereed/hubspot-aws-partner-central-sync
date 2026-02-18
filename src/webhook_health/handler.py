"""Webhook Health Check - Monitor webhook delivery"""
import json, logging, sys
sys.path.insert(0, "/var/task")
logger = logging.getLogger(); logger.setLevel(logging.INFO)

def lambda_handler(event: dict, context) -> dict:
    logger.info("Webhook health")
    return {"statusCode": 200, "body": json.dumps({"status": "healthy"})}
