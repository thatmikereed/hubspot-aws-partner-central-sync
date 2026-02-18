"""Event schemas for async processing"""
import json
import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field, field_validator


class EventType(str, Enum):
    """Supported event types"""

    DEAL_CREATION = "deal.creation"
    DEAL_UPDATE = "deal.propertyChange"
    COMPANY_UPDATE = "company.propertyChange"
    CONTACT_UPDATE = "contact.propertyChange"
    ENGAGEMENT_CREATION = "engagement.creation"
    MICROSOFT_DEAL_CREATION = "microsoft.deal.creation"
    MICROSOFT_DEAL_UPDATE = "microsoft.deal.propertyChange"


class EventSource(str, Enum):
    """Event sources"""

    HUBSPOT = "hubspot"
    MICROSOFT = "microsoft"
    AWS = "aws"
    SCHEDULED = "scheduled"


class SyncEvent(BaseModel):
    """Base event model for all sync operations"""

    event_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique event identifier",
    )
    event_type: EventType
    source: EventSource
    timestamp: datetime = Field(
        default_factory=datetime.utcnow, description="Event timestamp"
    )
    object_id: str = Field(..., description="ID of the object (deal, company, etc)")
    property_name: Optional[str] = Field(
        None, description="For property change events"
    )
    property_value: Optional[str] = Field(None, description="New value for property")
    webhook_payload: Dict[str, Any] = Field(
        default_factory=dict, description="Original webhook payload"
    )
    retry_count: int = Field(0, description="Number of processing attempts")
    correlation_id: Optional[str] = Field(
        None, description="For tracing related events"
    )

    @field_validator("event_id", mode="before")
    @classmethod
    def set_event_id(cls, v):
        """Generate event ID if not provided"""
        return v or str(uuid.uuid4())

    @field_validator("timestamp", mode="before")
    @classmethod
    def set_timestamp(cls, v):
        """Set timestamp if not provided"""
        return v or datetime.utcnow()

    def to_sqs_message(self) -> dict:
        """Convert to SQS message format"""
        return {
            "MessageBody": self.model_dump_json(),
            "MessageGroupId": self.object_id,  # FIFO: group by object
            "MessageDeduplicationId": self.event_id,
        }

    @classmethod
    def from_sqs_message(cls, message: dict) -> "SyncEvent":
        """Parse from SQS message"""
        body = json.loads(message["Body"])
        return cls(**body)


class HubSpotWebhookEvent(BaseModel):
    """HubSpot webhook event structure"""

    objectId: int
    propertyName: Optional[str] = None
    propertyValue: Optional[str] = None
    subscriptionType: str
    occurredAt: int

    def to_sync_event(self) -> SyncEvent:
        """Convert HubSpot webhook to internal event format"""
        return SyncEvent(
            event_type=EventType(self.subscriptionType),
            source=EventSource.HUBSPOT,
            object_id=str(self.objectId),
            property_name=self.propertyName,
            property_value=self.propertyValue,
            webhook_payload=self.model_dump(),
            timestamp=datetime.fromtimestamp(self.occurredAt / 1000),
        )
