"""
Base handler class implementing Template Method pattern for Lambda functions.
Provides consistent error handling, client initialization, and logging.
"""

from abc import ABC, abstractmethod
import base64
import json
import logging
import os
from typing import Any


class BaseLambdaHandler(ABC):
    """
    Abstract base class for Lambda handlers with common functionality.

    Subclasses must implement _execute() method with their specific logic.
    """

    def __init__(self):
        self.logger = logging.getLogger(self.__class__.__name__)
        self.logger.setLevel(os.getenv("LOG_LEVEL", "INFO"))
        self._hubspot_client = None
        self._pc_client = None

    @property
    def hubspot_client(self):
        """Lazy initialization of HubSpot client"""
        if self._hubspot_client is None:
            from common.hubspot_client import HubSpotClient

            self._hubspot_client = HubSpotClient()
        return self._hubspot_client

    @property
    def pc_client(self):
        """Lazy initialization of Partner Central client"""
        if self._pc_client is None:
            from common.aws_client import get_partner_central_client

            self._pc_client = get_partner_central_client()
        return self._pc_client

    def handle(self, event: dict, context: dict) -> dict:
        """
        Main entry point for Lambda handler (Template Method).

        Args:
            event: Lambda event dict
            context: Lambda context

        Returns:
            HTTP response dict with statusCode and body
        """
        try:
            self.logger.info(f"Received event: {json.dumps(event, default=str)}")
            result = self._execute(event, context)
            self.logger.info("Handler completed successfully")
            return result
        except Exception as e:
            self.logger.error(f"Handler error: {e}", exc_info=True)
            return self._error_response(str(e), 500)

    @abstractmethod
    def _execute(self, event: dict, context: dict) -> dict:
        """
        Subclasses implement their specific business logic here.

        Args:
            event: Lambda event dict
            context: Lambda context

        Returns:
            HTTP response dict
        """
        pass

    def _success_response(self, data: Any, status_code: int = 200) -> dict:
        """Standard success response format"""
        return {
            "statusCode": status_code,
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*",
            },
            "body": json.dumps(data, default=str),
        }

    def _error_response(self, message: str, status_code: int) -> dict:
        """Standard error response format"""
        return {
            "statusCode": status_code,
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*",
            },
            "body": json.dumps({"error": message}),
        }

    def _parse_webhook_body(self, event: dict) -> Any:
        """Parse webhook body handling base64 encoding"""
        body = event.get("body", "")

        if event.get("isBase64Encoded"):
            body = base64.b64decode(body).decode("utf-8")

        if isinstance(body, str):
            if body:  # Only parse non-empty strings
                return json.loads(body)
            return {}

        return body
