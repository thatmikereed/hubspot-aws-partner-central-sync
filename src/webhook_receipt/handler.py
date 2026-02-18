"""
Webhook Receipt Handler

Receives webhooks from HubSpot/Microsoft, validates them, and enqueues
for async processing. Returns 200 immediately.

This handler should be FAST (< 100ms) to avoid webhook timeouts.
"""
import json
import logging
import os
from typing import Any, List

import boto3

from common.base_handler import BaseLambdaHandler
from common.events import EventSource, HubSpotWebhookEvent, SyncEvent

logger = logging.getLogger(__name__)

# Initialize SQS client (outside handler for connection reuse)
sqs = boto3.client("sqs")
HUBSPOT_QUEUE_URL = os.environ.get("HUBSPOT_SYNC_QUEUE_URL", "")
MICROSOFT_QUEUE_URL = os.environ.get("MICROSOFT_SYNC_QUEUE_URL", "")
AWS_QUEUE_URL = os.environ.get("AWS_SYNC_QUEUE_URL", "")


class WebhookReceiptHandler(BaseLambdaHandler):
    """Fast webhook receipt handler that enqueues events"""

    def _execute(self, event: dict, context: dict) -> dict:
        """
        Receive webhook, validate, enqueue, return immediately.

        Args:
            event: API Gateway event with webhook payload
            context: Lambda context

        Returns:
            HTTP 200 response (or error)
        """
        # Parse webhook body
        webhook_events = self._parse_webhook_body(event)

        # Detect source
        source = self._detect_source(event, webhook_events)

        # Convert to internal events and enqueue
        enqueued_count = 0
        errors = []

        if isinstance(webhook_events, list):
            events_to_process = webhook_events
        else:
            events_to_process = [webhook_events]

        for webhook_event in events_to_process:
            try:
                # Convert to internal event format
                sync_event = self._convert_to_sync_event(webhook_event, source)

                # Enqueue for processing
                self._enqueue_event(sync_event, source)
                enqueued_count += 1

                self.logger.info(
                    f"Enqueued event: {sync_event.event_type} for {sync_event.object_id}"
                )

            except Exception as e:
                self.logger.error(f"Failed to enqueue event: {e}", exc_info=True)
                errors.append({"event": webhook_event, "error": str(e)})

        # Return success even if some events failed to enqueue
        # (webhook provider will retry the entire batch)
        response_data = {
            "received": len(events_to_process),
            "enqueued": enqueued_count,
            "errors": len(errors),
        }

        if errors:
            response_data["error_details"] = errors[:5]  # Limit error details

        return self._success_response(response_data, status_code=200)

    def _detect_source(self, event: dict, webhook_events: Any) -> EventSource:
        """Detect event source from webhook structure"""
        # Check path
        path = event.get("path", "")
        if "/webhook/hubspot" in path:
            return EventSource.HUBSPOT
        elif "/webhook/microsoft" in path:
            return EventSource.MICROSOFT
        elif "/webhook/aws" in path:
            return EventSource.AWS

        # Check pathParameters for API Gateway v2
        path_params = event.get("pathParameters", {})
        if path_params:
            source_param = path_params.get("source", "").lower()
            if source_param == "hubspot":
                return EventSource.HUBSPOT
            elif source_param == "microsoft":
                return EventSource.MICROSOFT
            elif source_param == "aws":
                return EventSource.AWS

        # Check webhook structure
        if isinstance(webhook_events, list) and len(webhook_events) > 0:
            first_event = webhook_events[0]
            if "subscriptionType" in first_event:
                return EventSource.HUBSPOT
            elif "referenceId" in first_event:
                return EventSource.MICROSOFT

        # Default
        return EventSource.HUBSPOT

    def _convert_to_sync_event(
        self, webhook_event: dict, source: EventSource
    ) -> SyncEvent:
        """Convert webhook event to internal format"""
        if source == EventSource.HUBSPOT:
            hs_event = HubSpotWebhookEvent(**webhook_event)
            return hs_event.to_sync_event()
        elif source == EventSource.MICROSOFT:
            # Microsoft webhook format
            return SyncEvent(
                event_type=webhook_event.get("eventType", "microsoft.deal.creation"),
                source=EventSource.MICROSOFT,
                object_id=webhook_event.get("referenceId", ""),
                webhook_payload=webhook_event,
            )
        else:
            # Generic format
            return SyncEvent(
                event_type=webhook_event.get("eventType", "unknown"),
                source=source,
                object_id=webhook_event.get("objectId", ""),
                webhook_payload=webhook_event,
            )

    def _enqueue_event(self, sync_event: SyncEvent, source: EventSource):
        """Enqueue event to appropriate SQS queue"""
        # Select queue based on source
        if source == EventSource.HUBSPOT:
            queue_url = HUBSPOT_QUEUE_URL
        elif source == EventSource.MICROSOFT:
            queue_url = MICROSOFT_QUEUE_URL
        elif source == EventSource.AWS:
            queue_url = AWS_QUEUE_URL
        else:
            queue_url = HUBSPOT_QUEUE_URL  # Default

        if not queue_url:
            raise ValueError(f"Queue URL not configured for source: {source}")

        # Send to SQS
        message = sync_event.to_sqs_message()

        response = sqs.send_message(QueueUrl=queue_url, **message)

        self.logger.debug(f"SQS MessageId: {response['MessageId']}")


def lambda_handler(event: dict, context: dict) -> dict:
    """Lambda entry point"""
    return WebhookReceiptHandler().handle(event, context)
