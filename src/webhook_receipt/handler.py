"""
Webhook Receipt Handler - Fast webhook receiver for async architecture.

This handler:
1. Receives HubSpot webhooks (<100ms response time)
2. Validates event structure
3. Converts to internal SyncEvent format
4. Enqueues to SQS FIFO queue
5. Returns 200 OK immediately

Extends BaseLambdaHandler for consistent error handling and client initialization.
"""

import logging
import os
import time
from typing import Any, Dict

import boto3

from common.base_handler import BaseLambdaHandler
from common.events import SyncEvent

# Configure logger
logger = logging.getLogger(__name__)


class WebhookReceiptHandler(BaseLambdaHandler):
    """
    Fast webhook receipt handler that enqueues events to SQS.

    Design goals:
    - Response time < 100ms
    - No external API calls (HubSpot, Partner Central)
    - Minimal processing
    - High reliability
    """

    def __init__(self):
        super().__init__()
        self.sqs_client = boto3.client("sqs")
        self.queue_url = os.environ.get("SQS_QUEUE_URL")

        if not self.queue_url:
            raise ValueError("SQS_QUEUE_URL environment variable is required")

    def _execute(self, event: Dict[str, Any], context: Any) -> Dict[str, Any]:
        """
        Process webhook and enqueue to SQS.

        Args:
            event: API Gateway event with webhook payload
            context: Lambda context

        Returns:
            HTTP response with status
        """
        start_time = time.time()

        # Verify webhook signature (fast operation)
        self._verify_signature(event)

        # Parse webhook body
        webhook_events = self._parse_webhook_body(event)

        if not webhook_events:
            self.logger.warning("No webhook events found in payload")
            return self._success_response({"message": "No events to process"})

        # Convert to SyncEvents and enqueue
        enqueued = []
        errors = []

        for webhook_event in webhook_events:
            try:
                # Convert to internal event format
                sync_event = SyncEvent.from_hubspot_webhook(webhook_event)

                # Send to SQS
                response = self._enqueue_event(sync_event)

                enqueued.append(
                    {
                        "eventId": sync_event.event_id,
                        "objectId": sync_event.object_id,
                        "eventType": sync_event.event_type,
                        "messageId": response.get("MessageId"),
                    }
                )

            except Exception as exc:
                self.logger.error(
                    f"Error processing webhook event {webhook_event.get('objectId')}: {exc}",
                    exc_info=True,
                )
                errors.append(
                    {
                        "objectId": webhook_event.get("objectId"),
                        "error": str(exc),
                    }
                )

        # Calculate response time
        elapsed_ms = (time.time() - start_time) * 1000

        self.logger.info(
            f"Processed {len(enqueued)} events, {len(errors)} errors "
            f"in {elapsed_ms:.1f}ms"
        )

        # Return success even if some events failed (we logged them)
        return self._success_response(
            {
                "message": "Webhook received",
                "enqueued": len(enqueued),
                "errors": len(errors),
                "processingTimeMs": elapsed_ms,
                "events": enqueued,
                "errorDetails": errors if errors else None,
            }
        )

    def _enqueue_event(self, sync_event: SyncEvent) -> Dict[str, Any]:
        """
        Send event to SQS FIFO queue.

        Args:
            sync_event: SyncEvent to enqueue

        Returns:
            SQS SendMessage response
        """
        sqs_message = sync_event.to_sqs_message()

        response = self.sqs_client.send_message(QueueUrl=self.queue_url, **sqs_message)

        self.logger.debug(
            f"Enqueued event {sync_event.event_id} to SQS: "
            f"MessageId={response.get('MessageId')}"
        )

        return response

    def _verify_signature(self, event: Dict[str, Any]) -> None:
        """
        Verify HubSpot webhook signature if secret is configured.

        This is a fast operation that doesn't require external API calls.

        Args:
            event: API Gateway event

        Raises:
            ValueError: If signature verification fails
        """
        secret = os.environ.get("HUBSPOT_WEBHOOK_SECRET")
        if not secret:
            self.logger.debug("No webhook secret configured, skipping verification")
            return

        headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
        signature = headers.get("x-hubspot-signature-v3", "")

        if not signature:
            self.logger.warning(
                "Missing HubSpot signature header — proceeding without verification"
            )
            return

        body = (event.get("body") or "").encode("utf-8")

        # Use HubSpot client's signature verification
        if not self.hubspot_client.verify_webhook_signature(body, signature, secret):
            raise ValueError("Invalid HubSpot webhook signature")

        self.logger.debug("Webhook signature verified")


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Lambda entry point for webhook receipt handler.

    Args:
        event: API Gateway event with webhook payload
        context: Lambda context

    Returns:
        HTTP response with status
    """
    handler = WebhookReceiptHandler()
    return handler.handle(event, context)
