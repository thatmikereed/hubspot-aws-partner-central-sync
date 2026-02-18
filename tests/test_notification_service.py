"""
Tests for HubSpot User Notification Service

Tests notification creation, priority assignment, due dates, and integration helpers.
"""

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import Mock, MagicMock, patch, call

# Mock the imports before importing notification service
import sys
sys.modules['common'] = Mock()
sys.modules['common.hubspot_client'] = Mock()

from src.notification_service.notification_service import (
    HubSpotNotificationService,
    NotificationPriority,
    NotificationType
)
from src.notification_service.integration import (
    notify_from_invitation,
    notify_from_score_change,
    notify_from_status_change,
    notify_from_submission,
    notify_from_seller_assignment,
    notify_from_resources,
    notify_from_conflict
)


@pytest.fixture
def mock_hubspot():
    """Mock HubSpot client"""
    client = Mock()
    client.session = Mock()
    return client


@pytest.fixture
def notification_service(mock_hubspot):
    """Create notification service with mocked HubSpot"""
    return HubSpotNotificationService(mock_hubspot)


@pytest.fixture
def sample_deal():
    """Sample HubSpot deal"""
    return {
        "id": "123",
        "properties": {
            "dealname": "Acme Corp AWS Migration",
            "hubspot_owner_id": "456",
            "aws_opportunity_id": "O1234567"
        }
    }


# ============================================================================
# Core Notification Service Tests
# ============================================================================

def test_notify_new_opportunity_creates_task(notification_service, mock_hubspot):
    """Test that new opportunity notification creates HIGH priority task"""
    # Setup mock responses
    mock_hubspot.session.post.return_value.status_code = 200
    mock_hubspot.session.post.return_value.json.return_value = {"id": "task_123"}
    mock_hubspot.session.put.return_value.status_code = 200
    
    # Call notification
    result = notification_service.notify_new_opportunity(
        deal_id="123",
        opportunity_id="O1234567",
        deal_name="Test Deal",
        deal_owner_id="456"
    )
    
    # Verify task created
    assert mock_hubspot.session.post.call_count == 2  # task + note
    task_call = mock_hubspot.session.post.call_args_list[0]
    
    # Check task properties
    task_body = task_call[1]["json"]
    assert "🆕 New AWS Co-Sell Opportunity" in task_body["properties"]["hs_task_subject"]
    assert task_body["properties"]["hs_task_priority"] == "HIGH"
    assert task_body["properties"]["hubspot_owner_id"] == "456"
    
    # Verify result
    assert result["taskId"] == "task_123"
    assert result["priority"] == "HIGH"


def test_notify_engagement_score_high_priority_when_score_80_plus(notification_service, mock_hubspot):
    """Test that high scores (80+) with increase get HIGH priority"""
    mock_hubspot.session.post.return_value.status_code = 200
    mock_hubspot.session.post.return_value.json.return_value = {"id": "task_123"}
    mock_hubspot.session.put.return_value.status_code = 200
    
    notification_service.notify_engagement_score_change(
        deal_id="123",
        opportunity_id="O1234567",
        deal_name="Test Deal",
        deal_owner_id="456",
        old_score=75,
        new_score=85,
        delta=10
    )
    
    task_call = mock_hubspot.session.post.call_args_list[0]
    task_body = task_call[1]["json"]
    
    # Should be HIGH priority
    assert task_body["properties"]["hs_task_priority"] == "HIGH"
    # Should mention urgency
    assert "HIGH PRIORITY" in task_body["properties"]["hs_task_body"]


def test_notify_engagement_score_medium_priority_when_decrease(notification_service, mock_hubspot):
    """Test that score decrease gets MEDIUM priority"""
    mock_hubspot.session.post.return_value.status_code = 200
    mock_hubspot.session.post.return_value.json.return_value = {"id": "task_123"}
    mock_hubspot.session.put.return_value.status_code = 200
    
    notification_service.notify_engagement_score_change(
        deal_id="123",
        opportunity_id="O1234567",
        deal_name="Test Deal",
        deal_owner_id="456",
        old_score=85,
        new_score=70,
        delta=-15
    )
    
    task_call = mock_hubspot.session.post.call_args_list[0]
    task_body = task_call[1]["json"]
    
    assert task_body["properties"]["hs_task_priority"] == "MEDIUM"
    assert "decreased" in task_body["properties"]["hs_task_subject"].lower()


def test_notify_review_status_approved_high_priority(notification_service, mock_hubspot):
    """Test that Approved status gets HIGH priority"""
    mock_hubspot.session.post.return_value.status_code = 200
    mock_hubspot.session.post.return_value.json.return_value = {"id": "task_123"}
    mock_hubspot.session.put.return_value.status_code = 200
    
    notification_service.notify_review_status_change(
        deal_id="123",
        opportunity_id="O1234567",
        deal_name="Test Deal",
        deal_owner_id="456",
        old_status="Submitted",
        new_status="Approved"
    )
    
    task_call = mock_hubspot.session.post.call_args_list[0]
    task_body = task_call[1]["json"]
    
    assert task_body["properties"]["hs_task_priority"] == "HIGH"
    assert "✅" in task_body["properties"]["hs_task_subject"]
    assert "APPROVED" in task_body["properties"]["hs_task_body"]


def test_notify_review_status_action_required_high_priority(notification_service, mock_hubspot):
    """Test that Action Required status gets HIGH priority"""
    mock_hubspot.session.post.return_value.status_code = 200
    mock_hubspot.session.post.return_value.json.return_value = {"id": "task_123"}
    mock_hubspot.session.put.return_value.status_code = 200
    
    notification_service.notify_review_status_change(
        deal_id="123",
        opportunity_id="O1234567",
        deal_name="Test Deal",
        deal_owner_id="456",
        old_status="Submitted",
        new_status="Action Required",
        feedback="Need customer contact info"
    )
    
    task_call = mock_hubspot.session.post.call_args_list[0]
    task_body = task_call[1]["json"]
    
    assert task_body["properties"]["hs_task_priority"] == "HIGH"
    assert "ACTION REQUIRED" in task_body["properties"]["hs_task_body"]
    assert "Need customer contact info" in task_body["properties"]["hs_task_body"]


def test_notify_submission_confirmed_creates_task(notification_service, mock_hubspot):
    """Test submission confirmation notification"""
    mock_hubspot.session.post.return_value.status_code = 200
    mock_hubspot.session.post.return_value.json.return_value = {"id": "task_123"}
    mock_hubspot.session.put.return_value.status_code = 200
    
    notification_service.notify_submission_confirmed(
        deal_id="123",
        opportunity_id="O1234567",
        deal_name="Test Deal",
        deal_owner_id="456",
        involvement_type="Co-Sell"
    )
    
    task_call = mock_hubspot.session.post.call_args_list[0]
    task_body = task_call[1]["json"]
    
    assert "📤" in task_body["properties"]["hs_task_subject"]
    assert "Submitted to AWS" in task_body["properties"]["hs_task_subject"]
    assert "Co-Sell" in task_body["properties"]["hs_task_body"]


def test_notify_aws_seller_assigned_high_priority(notification_service, mock_hubspot):
    """Test AWS seller assignment notification"""
    mock_hubspot.session.post.return_value.status_code = 200
    mock_hubspot.session.post.return_value.json.return_value = {"id": "task_123"}
    mock_hubspot.session.put.return_value.status_code = 200
    
    notification_service.notify_aws_seller_assigned(
        deal_id="123",
        opportunity_id="O1234567",
        deal_name="Test Deal",
        deal_owner_id="456",
        seller_name="John Smith",
        seller_email="john@aws.amazon.com"
    )
    
    task_call = mock_hubspot.session.post.call_args_list[0]
    task_body = task_call[1]["json"]
    
    assert task_body["properties"]["hs_task_priority"] == "HIGH"
    assert "👤" in task_body["properties"]["hs_task_subject"]
    assert "John Smith" in task_body["properties"]["hs_task_subject"]
    assert "john@aws.amazon.com" in task_body["properties"]["hs_task_body"]


def test_notify_resources_available_low_priority(notification_service, mock_hubspot):
    """Test resources available notification"""
    mock_hubspot.session.post.return_value.status_code = 200
    mock_hubspot.session.post.return_value.json.return_value = {"id": "task_123"}
    mock_hubspot.session.put.return_value.status_code = 200
    
    notification_service.notify_resources_available(
        deal_id="123",
        opportunity_id="O1234567",
        deal_name="Test Deal",
        deal_owner_id="456",
        resource_count=3,
        resource_types=["Case Study", "Whitepaper"]
    )
    
    task_call = mock_hubspot.session.post.call_args_list[0]
    task_body = task_call[1]["json"]
    
    assert task_body["properties"]["hs_task_priority"] == "LOW"
    assert "📚" in task_body["properties"]["hs_task_subject"]
    assert "3 New AWS Resources" in task_body["properties"]["hs_task_subject"]


def test_notify_conflict_detected_high_priority(notification_service, mock_hubspot):
    """Test conflict detection notification"""
    mock_hubspot.session.post.return_value.status_code = 200
    mock_hubspot.session.post.return_value.json.return_value = {"id": "task_123"}
    mock_hubspot.session.put.return_value.status_code = 200
    
    conflicts = ["Stage mismatch", "Amount differs by 20%"]
    
    notification_service.notify_conflict_detected(
        deal_id="123",
        opportunity_id="O1234567",
        deal_name="Test Deal",
        deal_owner_id="456",
        conflicts=conflicts
    )
    
    task_call = mock_hubspot.session.post.call_args_list[0]
    task_body = task_call[1]["json"]
    
    assert task_body["properties"]["hs_task_priority"] == "HIGH"
    assert "⚠️" in task_body["properties"]["hs_task_subject"]
    assert "Stage mismatch" in task_body["properties"]["hs_task_body"]


def test_task_associated_with_deal(notification_service, mock_hubspot):
    """Test that task is associated with the deal"""
    mock_hubspot.session.post.return_value.status_code = 200
    mock_hubspot.session.post.return_value.json.return_value = {"id": "task_123"}
    mock_hubspot.session.put.return_value.status_code = 200
    
    notification_service.notify_new_opportunity(
        deal_id="123",
        opportunity_id="O1234567",
        deal_name="Test Deal",
        deal_owner_id="456"
    )
    
    # Check that PUT was called to associate
    put_calls = [call for call in mock_hubspot.session.put.call_args_list]
    assert len(put_calls) >= 1
    
    # Verify association URL contains task and deal IDs
    assoc_url = put_calls[0][0][0]
    assert "tasks/task_123/associations/deals/123" in assoc_url


def test_note_created_with_task(notification_service, mock_hubspot):
    """Test that a note is also created alongside the task"""
    mock_hubspot.session.post.return_value.status_code = 200
    mock_hubspot.session.post.side_effect = [
        Mock(status_code=200, json=lambda: {"id": "task_123"}),
        Mock(status_code=200, json=lambda: {"id": "note_456"})
    ]
    mock_hubspot.session.put.return_value.status_code = 200
    
    notification_service.notify_new_opportunity(
        deal_id="123",
        opportunity_id="O1234567",
        deal_name="Test Deal",
        deal_owner_id="456"
    )
    
    # Verify both task and note were created
    assert mock_hubspot.session.post.call_count == 2
    
    # First call should be task
    task_url = mock_hubspot.session.post.call_args_list[0][0][0]
    assert "/objects/tasks" in task_url
    
    # Second call should be note
    note_url = mock_hubspot.session.post.call_args_list[1][0][0]
    assert "/objects/notes" in note_url


def test_due_date_calculated_correctly(notification_service, mock_hubspot):
    """Test that due dates are calculated based on priority"""
    mock_hubspot.session.post.return_value.status_code = 200
    mock_hubspot.session.post.return_value.json.return_value = {"id": "task_123"}
    mock_hubspot.session.put.return_value.status_code = 200
    
    before_time = datetime.now(timezone.utc)
    
    notification_service.notify_new_opportunity(
        deal_id="123",
        opportunity_id="O1234567",
        deal_name="Test Deal",
        deal_owner_id="456"
    )
    
    after_time = datetime.now(timezone.utc)
    
    task_call = mock_hubspot.session.post.call_args_list[0]
    task_body = task_call[1]["json"]
    due_date_ms = task_body["properties"]["hs_timestamp"]
    due_date = datetime.fromtimestamp(due_date_ms / 1000, tz=timezone.utc)
    
    # HIGH priority = 24 hours
    expected_min = before_time + timedelta(hours=23, minutes=59)
    expected_max = after_time + timedelta(hours=24, minutes=1)
    
    assert expected_min <= due_date <= expected_max


# ============================================================================
# Integration Helper Tests
# ============================================================================

def test_notify_from_invitation_helper(mock_hubspot, sample_deal):
    """Test invitation notification helper"""
    mock_hubspot.session.post.return_value.status_code = 200
    mock_hubspot.session.post.return_value.json.return_value = {"id": "task_123"}
    mock_hubspot.session.put.return_value.status_code = 200
    
    result = notify_from_invitation(
        hubspot_client=mock_hubspot,
        deal_id="123",
        opportunity_id="O1234567",
        deal=sample_deal
    )
    
    # Should have called the API
    assert mock_hubspot.session.post.called


def test_notify_from_score_change_helper(mock_hubspot, sample_deal):
    """Test score change notification helper"""
    mock_hubspot.session.post.return_value.status_code = 200
    mock_hubspot.session.post.return_value.json.return_value = {"id": "task_123"}
    mock_hubspot.session.put.return_value.status_code = 200
    
    result = notify_from_score_change(
        hubspot_client=mock_hubspot,
        deal_id="123",
        opportunity_id="O1234567",
        deal=sample_deal,
        old_score=70,
        new_score=85
    )
    
    assert mock_hubspot.session.post.called


def test_notify_from_status_change_helper(mock_hubspot, sample_deal):
    """Test status change notification helper"""
    mock_hubspot.session.post.return_value.status_code = 200
    mock_hubspot.session.post.return_value.json.return_value = {"id": "task_123"}
    mock_hubspot.session.put.return_value.status_code = 200
    
    result = notify_from_status_change(
        hubspot_client=mock_hubspot,
        deal_id="123",
        opportunity_id="O1234567",
        deal=sample_deal,
        old_status="Submitted",
        new_status="Approved"
    )
    
    assert mock_hubspot.session.post.called


# ============================================================================
# Error Handling Tests
# ============================================================================

def test_notification_handles_api_error_gracefully(notification_service, mock_hubspot):
    """Test that API errors don't crash the notification system"""
    mock_hubspot.session.post.side_effect = Exception("API Error")
    
    result = notification_service.notify_new_opportunity(
        deal_id="123",
        opportunity_id="O1234567",
        deal_name="Test Deal",
        deal_owner_id="456"
    )
    
    # Should return failure status without crashing
    assert result["status"] == "failed"
    assert "API Error" in result["error"]


def test_notification_continues_if_association_fails(notification_service, mock_hubspot):
    """Test that notification succeeds even if association fails"""
    mock_hubspot.session.post.return_value.status_code = 200
    mock_hubspot.session.post.return_value.json.return_value = {"id": "task_123"}
    mock_hubspot.session.put.return_value.status_code = 400  # Association fails
    
    result = notification_service.notify_new_opportunity(
        deal_id="123",
        opportunity_id="O1234567",
        deal_name="Test Deal",
        deal_owner_id="456"
    )
    
    # Should still succeed - association is best-effort
    assert result["taskId"] == "task_123"


# ============================================================================
# Content Tests
# ============================================================================

def test_notification_includes_action_items(notification_service, mock_hubspot):
    """Test that notifications include actionable next steps"""
    mock_hubspot.session.post.return_value.status_code = 200
    mock_hubspot.session.post.return_value.json.return_value = {"id": "task_123"}
    mock_hubspot.session.put.return_value.status_code = 200
    
    notification_service.notify_new_opportunity(
        deal_id="123",
        opportunity_id="O1234567",
        deal_name="Test Deal",
        deal_owner_id="456"
    )
    
    task_call = mock_hubspot.session.post.call_args_list[0]
    task_body = task_call[1]["json"]
    body_text = task_body["properties"]["hs_task_body"]
    
    # Should contain action items
    assert "Next Steps:" in body_text
    assert "1." in body_text  # Numbered list
    assert "2." in body_text


def test_notification_includes_emoji(notification_service, mock_hubspot):
    """Test that notifications use emojis for visual identification"""
    mock_hubspot.session.post.return_value.status_code = 200
    mock_hubspot.session.post.return_value.json.return_value = {"id": "task_123"}
    mock_hubspot.session.put.return_value.status_code = 200
    
    notification_service.notify_new_opportunity(
        deal_id="123",
        opportunity_id="O1234567",
        deal_name="Test Deal",
        deal_owner_id="456"
    )
    
    task_call = mock_hubspot.session.post.call_args_list[0]
    task_body = task_call[1]["json"]
    subject = task_body["properties"]["hs_task_subject"]
    
    # Should contain emoji
    assert any(char in subject for char in ["🆕", "📤", "✅", "⚠️", "❌"])


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
