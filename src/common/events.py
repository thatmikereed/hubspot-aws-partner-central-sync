"""
Event schema definitions for async event-driven architecture.

Provides Pydantic models for event validation and SQS message conversion.
"""

import json
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class EventType(str, Enum):
    """Types of sync events that can be processed."""

    DEAL_CREATION = "deal.creation"
    DEAL_PROPERTY_CHANGE = "deal.propertyChange"
    COMPANY_PROPERTY_CHANGE = "company.propertyChange"
    CONTACT_PROPERTY_CHANGE = "contact.propertyChange"
    NOTE_CREATION = "note.creation"
    ENGAGEMENT_CREATION = "engagement.creation"


class EventSource(str, Enum):
    """Source systems that can generate events."""

    HUBSPOT = "hubspot"
    AWS_PARTNER_CENTRAL = "aws"
    MICROSOFT_PARTNER_CENTER = "microsoft"
    GCP_PARTNERS = "gcp"


class SyncEvent(BaseModel):
    """
    Base event model for all sync operations.

    This model provides:
    - Standard event structure
    - Validation
    - Correlation tracking
    - SQS message conversion
    """

    # Event identification
    event_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique event identifier for deduplication",
    )
    event_type: EventType = Field(description="Type of event (e.g., deal.creation)")
    event_source: EventSource = Field(
        description="Source system that generated the event"
    )

    # Event timing
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Event creation timestamp (UTC)",
    )

    # Event payload
    object_id: str = Field(
        description="ID of the object being synced (e.g., deal ID, company ID)"
    )
    object_type: str = Field(
        description="Type of object (e.g., deal, company, contact)"
    )
    properties: Dict[str, Any] = Field(
        default_factory=dict, description="Event-specific properties and metadata"
    )

    # Correlation tracking
    correlation_id: Optional[str] = Field(
        default=None, description="Correlation ID for tracing related events"
    )

    # Retry tracking
    attempt_count: int = Field(default=0, description="Number of processing attempts")

    model_config = ConfigDict(
        use_enum_values=True, json_encoders={datetime: lambda v: v.isoformat()}
    )

    @field_validator("timestamp", mode="before")
    @classmethod
    def parse_timestamp(cls, v):
        """Parse timestamp from string if needed."""
        if isinstance(v, str):
            return datetime.fromisoformat(v.replace("Z", "+00:00"))
        return v

    def to_sqs_message(self) -> Dict[str, Any]:
        """
        Convert event to SQS message format.

        Returns:
            Dict with MessageBody, MessageGroupId, and MessageDeduplicationId
        """
        return {
            "MessageBody": self.model_dump_json(),
            "MessageGroupId": self.object_id,  # FIFO ordering by object
            "MessageDeduplicationId": self.event_id,  # Content-based deduplication
        }

    @classmethod
    def from_sqs_message(cls, message: Dict[str, Any]) -> "SyncEvent":
        """
        Create event from SQS message.

        Args:
            message: SQS message dict with 'Body' field

        Returns:
            SyncEvent instance
        """
        body = message.get("Body", "{}")
        if isinstance(body, str):
            data = json.loads(body)
        else:
            data = body
        return cls(**data)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return self.model_dump(mode="json")

    @classmethod
    def from_hubspot_webhook(
        cls, webhook_event: Dict[str, Any], correlation_id: Optional[str] = None
    ) -> "SyncEvent":
        """
        Create event from HubSpot webhook payload.

        Args:
            webhook_event: HubSpot webhook event dict
            correlation_id: Optional correlation ID for tracing

        Returns:
            SyncEvent instance
        """
        subscription_type = webhook_event.get("subscriptionType", "")
        object_id = str(webhook_event.get("objectId", ""))

        # Determine event type from subscription type
        event_type_map = {
            "deal.creation": EventType.DEAL_CREATION,
            "deal.propertyChange": EventType.DEAL_PROPERTY_CHANGE,
            "company.propertyChange": EventType.COMPANY_PROPERTY_CHANGE,
            "contact.propertyChange": EventType.CONTACT_PROPERTY_CHANGE,
            "note.creation": EventType.NOTE_CREATION,
            "engagement.creation": EventType.ENGAGEMENT_CREATION,
        }

        event_type = event_type_map.get(
            subscription_type, EventType.DEAL_PROPERTY_CHANGE  # Default fallback
        )

        # Determine object type from event type
        object_type = "deal"
        if "company" in subscription_type:
            object_type = "company"
        elif "contact" in subscription_type:
            object_type = "contact"
        elif "note" in subscription_type:
            object_type = "note"
        elif "engagement" in subscription_type:
            object_type = "engagement"

        return cls(
            event_type=event_type,
            event_source=EventSource.HUBSPOT,
            object_id=object_id,
            object_type=object_type,
            properties={
                "subscriptionType": subscription_type,
                "propertyName": webhook_event.get("propertyName"),
                "propertyValue": webhook_event.get("propertyValue"),
                "changeSource": webhook_event.get("changeSource"),
                "eventId": webhook_event.get("eventId"),
                "portalId": webhook_event.get("portalId"),
                "appId": webhook_event.get("appId"),
                "occurredAt": webhook_event.get("occurredAt"),
            },
            correlation_id=correlation_id or str(uuid.uuid4()),
        )


class EventBatch(BaseModel):
    """
    Batch of events for processing.

    Used when processing multiple events together.
    """

    events: list[SyncEvent] = Field(description="List of events in the batch")
    batch_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()), description="Unique batch identifier"
    )

    def to_sqs_messages(self) -> list[Dict[str, Any]]:
        """Convert all events to SQS messages."""
        return [event.to_sqs_message() for event in self.events]
