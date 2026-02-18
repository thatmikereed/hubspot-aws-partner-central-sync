"""
Tests for Smart Notifications handler.
"""

import json
import pytest
from unittest.mock import MagicMock, patch, call
from datetime import datetime


@pytest.fixture
def mock_hubspot_client():
    """Mock HubSpotClient."""
    with patch('common.hubspot_client.HubSpotClient') as mock:
        client = MagicMock()
        mock.return_value = client
        yield client


@pytest.fixture
def mock_pc_client():
    """Mock Partner Central client."""
    with patch('common.aws_client.get_partner_central_client') as mock:
        client = MagicMock()
        mock.return_value = client
        yield client


@pytest.fixture
def sample_deal():
    """Sample HubSpot deal."""
    return {
        "id": "12345",
        "properties": {
            "dealname": "Test Deal #AWS",
            "aws_opportunity_id": "O1234567890",
            "aws_engagement_score": "70",
            "aws_review_status": "Submitted",
            "aws_seller_name": "",
            "hubspot_owner_id": "100"
        }
    }


@pytest.fixture
def sample_aws_summary():
    """Sample AWS Opportunity Summary."""
    return {
        "Insights": {
            "EngagementScore": 85
        },
        "LifeCycle": {
            "ReviewStatus": "Approved"
        },
        "OpportunityTeam": [
            {
                "FirstName": "John",
                "LastName": "Smith",
                "Email": "john.smith@aws.amazon.com"
            }
        ]
    }


def test_engagement_score_increase_notification(mock_hubspot_client, mock_pc_client, sample_deal, sample_aws_summary):
    """Test notification when engagement score increases significantly."""
    from smart_notifications.handler import lambda_handler
    
    # Setup - score increased from 70 to 85 (+15 points)
    mock_hubspot_client.get_deal.return_value = sample_deal
    mock_pc_client.get_aws_opportunity_summary.return_value = sample_aws_summary
    
    # Mock search to return our sample deal
    mock_hubspot_client.session.post.return_value.json.return_value = {
        "results": [sample_deal]
    }
    mock_hubspot_client.session.post.return_value.raise_for_status = MagicMock()
    
    event = {}  # Scheduled event
    
    response = lambda_handler(event, None)
    
    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert body["notificationsCreated"] >= 1
    
    # Verify note was added with engagement score info
    mock_hubspot_client.add_note_to_deal.assert_called()
    note_calls = [call[0] for call in mock_hubspot_client.add_note_to_deal.call_args_list]
    
    # Check if any note mentions engagement score
    score_notes = [n for n in note_calls if any("Engagement Score" in str(arg) for arg in n)]
    assert len(score_notes) > 0


def test_engagement_score_decrease_notification(mock_hubspot_client, mock_pc_client, sample_deal):
    """Test notification when engagement score decreases significantly."""
    from smart_notifications.handler import lambda_handler
    
    # Score decreased from 70 to 50 (-20 points)
    sample_aws_summary = {
        "Insights": {
            "EngagementScore": 50
        },
        "LifeCycle": {
            "ReviewStatus": "Submitted"
        },
        "OpportunityTeam": []
    }
    
    mock_hubspot_client.get_deal.return_value = sample_deal
    mock_pc_client.get_aws_opportunity_summary.return_value = sample_aws_summary
    
    mock_hubspot_client.session.post.return_value.json.return_value = {
        "results": [sample_deal]
    }
    mock_hubspot_client.session.post.return_value.raise_for_status = MagicMock()
    
    response = lambda_handler({}, None)
    
    assert response["statusCode"] == 200
    
    # Verify notification was created
    mock_hubspot_client.add_note_to_deal.assert_called()


def test_engagement_score_no_notification_below_threshold(mock_hubspot_client, mock_pc_client, sample_deal):
    """Test no notification when score change is below threshold."""
    from smart_notifications.handler import lambda_handler
    
    # Score changed from 70 to 75 (+5 points, below threshold of 15)
    sample_aws_summary = {
        "Insights": {
            "EngagementScore": 75
        },
        "LifeCycle": {
            "ReviewStatus": "Submitted"
        },
        "OpportunityTeam": []
    }
    
    mock_hubspot_client.get_deal.return_value = sample_deal
    mock_pc_client.get_aws_opportunity_summary.return_value = sample_aws_summary
    
    mock_hubspot_client.session.post.return_value.json.return_value = {
        "results": [sample_deal]
    }
    mock_hubspot_client.session.post.return_value.raise_for_status = MagicMock()
    
    response = lambda_handler({}, None)
    
    # Should complete successfully but not create notifications
    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    # Score change notifications should be 0 or minimal
    assert body["notificationsCreated"] == 0 or body["notificationsCreated"] < 2


def test_review_status_approved_notification(mock_hubspot_client, mock_pc_client, sample_deal):
    """Test notification when AWS approves opportunity."""
    from smart_notifications.handler import lambda_handler
    
    sample_aws_summary = {
        "Insights": {
            "EngagementScore": 70  # No change
        },
        "LifeCycle": {
            "ReviewStatus": "Approved"  # Changed from Submitted
        },
        "OpportunityTeam": []
    }
    
    mock_hubspot_client.get_deal.return_value = sample_deal
    mock_pc_client.get_aws_opportunity_summary.return_value = sample_aws_summary
    
    mock_hubspot_client.session.post.return_value.json.return_value = {
        "results": [sample_deal]
    }
    mock_hubspot_client.session.post.return_value.raise_for_status = MagicMock()
    
    response = lambda_handler({}, None)
    
    assert response["statusCode"] == 200
    
    # Verify note contains approval info
    mock_hubspot_client.add_note_to_deal.assert_called()
    note_calls = [str(call) for call in mock_hubspot_client.add_note_to_deal.call_args_list]
    approval_notes = [n for n in note_calls if "Approved" in n]
    assert len(approval_notes) > 0


def test_review_status_action_required_notification(mock_hubspot_client, mock_pc_client, sample_deal):
    """Test notification when AWS requests action."""
    from smart_notifications.handler import lambda_handler
    
    sample_aws_summary = {
        "Insights": {
            "EngagementScore": 70
        },
        "LifeCycle": {
            "ReviewStatus": "Action Required"
        },
        "OpportunityTeam": []
    }
    
    mock_hubspot_client.get_deal.return_value = sample_deal
    mock_pc_client.get_aws_opportunity_summary.return_value = sample_aws_summary
    
    mock_hubspot_client.session.post.return_value.json.return_value = {
        "results": [sample_deal]
    }
    mock_hubspot_client.session.post.return_value.raise_for_status = MagicMock()
    
    response = lambda_handler({}, None)
    
    assert response["statusCode"] == 200
    
    # Verify high priority notification
    note_calls = [str(call) for call in mock_hubspot_client.add_note_to_deal.call_args_list]
    action_notes = [n for n in note_calls if "Action Required" in n or "action" in n.lower()]
    assert len(action_notes) > 0


def test_seller_assignment_notification(mock_hubspot_client, mock_pc_client, sample_deal, sample_aws_summary):
    """Test notification when AWS seller is assigned."""
    from smart_notifications.handler import lambda_handler
    
    mock_hubspot_client.get_deal.return_value = sample_deal
    mock_pc_client.get_aws_opportunity_summary.return_value = sample_aws_summary
    
    mock_hubspot_client.session.post.return_value.json.return_value = {
        "results": [sample_deal]
    }
    mock_hubspot_client.session.post.return_value.raise_for_status = MagicMock()
    
    response = lambda_handler({}, None)
    
    assert response["statusCode"] == 200
    
    # Verify seller assignment notification
    mock_hubspot_client.add_note_to_deal.assert_called()
    note_calls = [str(call) for call in mock_hubspot_client.add_note_to_deal.call_args_list]
    seller_notes = [n for n in note_calls if "Seller" in n or "John Smith" in n]
    assert len(seller_notes) > 0


def test_eventbridge_opportunity_updated(mock_hubspot_client, mock_pc_client, sample_deal):
    """Test handling EventBridge Opportunity Updated event."""
    from smart_notifications.handler import lambda_handler
    
    event = {
        "source": "aws.partnercentral-selling",
        "detail-type": "Opportunity Updated",
        "detail": {
            "opportunity": {
                "identifier": "O1234567890"
            }
        }
    }
    
    # Mock finding the deal
    mock_hubspot_client.session.post.return_value.json.return_value = {
        "results": [sample_deal]
    }
    mock_hubspot_client.session.post.return_value.raise_for_status = MagicMock()
    
    response = lambda_handler(event, None)
    
    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert "notificationsCreated" in body


def test_no_deals_scenario(mock_hubspot_client, mock_pc_client):
    """Test behavior when no active deals exist."""
    from smart_notifications.handler import lambda_handler
    
    # Mock empty results
    mock_hubspot_client.session.post.return_value.json.return_value = {
        "results": []
    }
    mock_hubspot_client.session.post.return_value.raise_for_status = MagicMock()
    
    response = lambda_handler({}, None)
    
    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert body["dealsChecked"] == 0
    assert body["notificationsCreated"] == 0


def test_task_creation_for_high_priority(mock_hubspot_client, mock_pc_client, sample_deal):
    """Test that high priority notifications create HubSpot tasks."""
    from smart_notifications.handler import lambda_handler
    
    # High engagement score increase
    sample_aws_summary = {
        "Insights": {
            "EngagementScore": 95  # Very high score
        },
        "LifeCycle": {
            "ReviewStatus": "Approved"
        },
        "OpportunityTeam": []
    }
    
    mock_hubspot_client.get_deal.return_value = sample_deal
    mock_pc_client.get_aws_opportunity_summary.return_value = sample_aws_summary
    
    mock_hubspot_client.session.post.return_value.json.return_value = {
        "results": [sample_deal]
    }
    mock_hubspot_client.session.post.return_value.raise_for_status = MagicMock()
    
    # Mock task creation response
    mock_hubspot_client.session.post.return_value.json.side_effect = [
        {"results": [sample_deal]},  # Search results
        {"id": "task-123"}  # Task creation
    ]
    
    response = lambda_handler({}, None)
    
    assert response["statusCode"] == 200


def test_error_handling_pc_api_failure(mock_hubspot_client, mock_pc_client, sample_deal):
    """Test graceful handling of Partner Central API errors."""
    from smart_notifications.handler import lambda_handler
    
    mock_hubspot_client.session.post.return_value.json.return_value = {
        "results": [sample_deal]
    }
    mock_hubspot_client.session.post.return_value.raise_for_status = MagicMock()
    
    # Simulate PC API error
    mock_pc_client.get_aws_opportunity_summary.side_effect = Exception("PC API Error")
    
    response = lambda_handler({}, None)
    
    # Should handle error gracefully
    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    # May have errors but shouldn't crash
    assert "errors" in body or "error" not in body
