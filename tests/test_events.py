"""
Tests for event schema and validation.
"""

import json
import uuid
from datetime import datetime, timezone

import pytest

from common.events import (
    EventType,
    EventSource,
    SyncEvent,
    EventBatch,
)


def test_event_type_enum():
    """Test EventType enum values."""
    assert EventType.DEAL_CREATION == "deal.creation"
    assert EventType.DEAL_PROPERTY_CHANGE == "deal.propertyChange"
    assert EventType.COMPANY_PROPERTY_CHANGE == "company.propertyChange"


def test_event_source_enum():
    """Test EventSource enum values."""
    assert EventSource.HUBSPOT == "hubspot"
    assert EventSource.AWS_PARTNER_CENTRAL == "aws"
    assert EventSource.MICROSOFT_PARTNER_CENTER == "microsoft"


def test_sync_event_creation():
    """Test creating a SyncEvent with minimal fields."""
    event = SyncEvent(
        event_type=EventType.DEAL_CREATION,
        event_source=EventSource.HUBSPOT,
        object_id="12345",
        object_type="deal",
    )
    
    assert event.event_type == EventType.DEAL_CREATION
    assert event.event_source == EventSource.HUBSPOT
    assert event.object_id == "12345"
    assert event.object_type == "deal"
    assert event.event_id is not None  # Auto-generated
    assert event.timestamp is not None  # Auto-generated
    assert event.properties == {}
    assert event.correlation_id is None
    assert event.attempt_count == 0


def test_sync_event_with_all_fields():
    """Test creating a SyncEvent with all fields."""
    event_id = str(uuid.uuid4())
    correlation_id = str(uuid.uuid4())
    timestamp = datetime.now(timezone.utc)
    
    event = SyncEvent(
        event_id=event_id,
        event_type=EventType.DEAL_PROPERTY_CHANGE,
        event_source=EventSource.HUBSPOT,
        timestamp=timestamp,
        object_id="67890",
        object_type="deal",
        properties={"propertyName": "dealname", "propertyValue": "Test Deal"},
        correlation_id=correlation_id,
        attempt_count=1,
    )
    
    assert event.event_id == event_id
    assert event.event_type == EventType.DEAL_PROPERTY_CHANGE
    assert event.object_id == "67890"
    assert event.properties == {"propertyName": "dealname", "propertyValue": "Test Deal"}
    assert event.correlation_id == correlation_id
    assert event.attempt_count == 1


def test_sync_event_to_sqs_message():
    """Test converting SyncEvent to SQS message format."""
    event = SyncEvent(
        event_type=EventType.DEAL_CREATION,
        event_source=EventSource.HUBSPOT,
        object_id="12345",
        object_type="deal",
    )
    
    sqs_message = event.to_sqs_message()
    
    assert "MessageBody" in sqs_message
    assert "MessageGroupId" in sqs_message
    assert "MessageDeduplicationId" in sqs_message
    
    # Verify FIFO attributes
    assert sqs_message["MessageGroupId"] == "12345"  # object_id
    assert sqs_message["MessageDeduplicationId"] == event.event_id
    
    # Verify body is valid JSON
    body = json.loads(sqs_message["MessageBody"])
    assert body["event_type"] == "deal.creation"
    assert body["object_id"] == "12345"


def test_sync_event_from_sqs_message():
    """Test creating SyncEvent from SQS message."""
    event = SyncEvent(
        event_type=EventType.DEAL_CREATION,
        event_source=EventSource.HUBSPOT,
        object_id="12345",
        object_type="deal",
        properties={"test": "value"},
    )
    
    # Convert to SQS message and back
    sqs_message = event.to_sqs_message()
    sqs_record = {"Body": sqs_message["MessageBody"]}
    
    restored_event = SyncEvent.from_sqs_message(sqs_record)
    
    assert restored_event.event_id == event.event_id
    assert restored_event.event_type == event.event_type
    assert restored_event.event_source == event.event_source
    assert restored_event.object_id == event.object_id
    assert restored_event.object_type == event.object_type
    assert restored_event.properties == event.properties


def test_sync_event_from_hubspot_webhook_deal_creation():
    """Test creating SyncEvent from HubSpot deal.creation webhook."""
    webhook_event = {
        "subscriptionType": "deal.creation",
        "objectId": "12345",
        "propertyName": "dealname",
        "propertyValue": "Test Deal #AWS",
        "changeSource": "CRM",
        "eventId": "67890",
        "portalId": "111",
        "appId": "222",
        "occurredAt": "2024-01-01T12:00:00Z",
    }
    
    event = SyncEvent.from_hubspot_webhook(webhook_event)
    
    assert event.event_type == EventType.DEAL_CREATION
    assert event.event_source == EventSource.HUBSPOT
    assert event.object_id == "12345"
    assert event.object_type == "deal"
    assert event.properties["subscriptionType"] == "deal.creation"
    assert event.properties["propertyName"] == "dealname"
    assert event.correlation_id is not None


def test_sync_event_from_hubspot_webhook_deal_property_change():
    """Test creating SyncEvent from HubSpot deal.propertyChange webhook."""
    webhook_event = {
        "subscriptionType": "deal.propertyChange",
        "objectId": "54321",
        "propertyName": "amount",
        "propertyValue": "50000",
    }
    
    event = SyncEvent.from_hubspot_webhook(webhook_event)
    
    assert event.event_type == EventType.DEAL_PROPERTY_CHANGE
    assert event.event_source == EventSource.HUBSPOT
    assert event.object_id == "54321"
    assert event.object_type == "deal"


def test_sync_event_from_hubspot_webhook_company_property_change():
    """Test creating SyncEvent from HubSpot company.propertyChange webhook."""
    webhook_event = {
        "subscriptionType": "company.propertyChange",
        "objectId": "99999",
        "propertyName": "industry",
        "propertyValue": "Technology",
    }
    
    event = SyncEvent.from_hubspot_webhook(webhook_event)
    
    assert event.event_type == EventType.COMPANY_PROPERTY_CHANGE
    assert event.event_source == EventSource.HUBSPOT
    assert event.object_id == "99999"
    assert event.object_type == "company"


def test_sync_event_from_hubspot_webhook_note_creation():
    """Test creating SyncEvent from HubSpot engagement.creation webhook."""
    webhook_event = {
        "subscriptionType": "engagement.creation",
        "objectId": "88888",
    }
    
    event = SyncEvent.from_hubspot_webhook(webhook_event)
    
    assert event.event_type == EventType.ENGAGEMENT_CREATION
    assert event.event_source == EventSource.HUBSPOT
    assert event.object_id == "88888"
    assert event.object_type == "engagement"


def test_sync_event_to_dict():
    """Test converting SyncEvent to dictionary."""
    event = SyncEvent(
        event_type=EventType.DEAL_CREATION,
        event_source=EventSource.HUBSPOT,
        object_id="12345",
        object_type="deal",
    )
    
    event_dict = event.to_dict()
    
    assert isinstance(event_dict, dict)
    assert event_dict["event_type"] == "deal.creation"
    assert event_dict["event_source"] == "hubspot"
    assert event_dict["object_id"] == "12345"
    assert event_dict["object_type"] == "deal"


def test_event_batch_creation():
    """Test creating an EventBatch."""
    events = [
        SyncEvent(
            event_type=EventType.DEAL_CREATION,
            event_source=EventSource.HUBSPOT,
            object_id=str(i),
            object_type="deal",
        )
        for i in range(5)
    ]
    
    batch = EventBatch(events=events)
    
    assert len(batch.events) == 5
    assert batch.batch_id is not None


def test_event_batch_to_sqs_messages():
    """Test converting EventBatch to SQS messages."""
    events = [
        SyncEvent(
            event_type=EventType.DEAL_CREATION,
            event_source=EventSource.HUBSPOT,
            object_id=str(i),
            object_type="deal",
        )
        for i in range(3)
    ]
    
    batch = EventBatch(events=events)
    sqs_messages = batch.to_sqs_messages()
    
    assert len(sqs_messages) == 3
    for msg in sqs_messages:
        assert "MessageBody" in msg
        assert "MessageGroupId" in msg
        assert "MessageDeduplicationId" in msg


def test_timestamp_parsing():
    """Test timestamp parsing from string."""
    timestamp_str = "2024-01-01T12:00:00Z"
    
    event = SyncEvent(
        event_type=EventType.DEAL_CREATION,
        event_source=EventSource.HUBSPOT,
        object_id="12345",
        object_type="deal",
        timestamp=timestamp_str,
    )
    
    assert isinstance(event.timestamp, datetime)


def test_correlation_id_in_from_hubspot_webhook():
    """Test custom correlation ID is preserved."""
    custom_correlation_id = str(uuid.uuid4())
    
    webhook_event = {
        "subscriptionType": "deal.creation",
        "objectId": "12345",
    }
    
    event = SyncEvent.from_hubspot_webhook(
        webhook_event,
        correlation_id=custom_correlation_id
    )
    
    assert event.correlation_id == custom_correlation_id


def test_event_roundtrip_preserves_data():
    """Test that event data is preserved through SQS conversion."""
    original_event = SyncEvent(
        event_type=EventType.DEAL_PROPERTY_CHANGE,
        event_source=EventSource.HUBSPOT,
        object_id="12345",
        object_type="deal",
        properties={
            "propertyName": "dealname",
            "propertyValue": "Test Deal",
            "nested": {"key": "value"},
        },
        attempt_count=2,
    )
    
    # Convert to SQS and back
    sqs_message = original_event.to_sqs_message()
    sqs_record = {"Body": sqs_message["MessageBody"]}
    restored_event = SyncEvent.from_sqs_message(sqs_record)
    
    # Verify all fields preserved
    assert restored_event.event_id == original_event.event_id
    assert restored_event.event_type == original_event.event_type
    assert restored_event.event_source == original_event.event_source
    assert restored_event.object_id == original_event.object_id
    assert restored_event.object_type == original_event.object_type
    assert restored_event.properties == original_event.properties
    assert restored_event.attempt_count == original_event.attempt_count
