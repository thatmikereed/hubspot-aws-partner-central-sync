"""
Tests for HubSpot Deal Update Sync handler.
"""

import json
import pytest
from unittest.mock import MagicMock, patch, call
from datetime import datetime


@pytest.fixture
def mock_hubspot_client():
    """Mock HubSpotClient."""
    with patch('hubspot_deal_update_sync.handler.HubSpotClient') as mock:
        client = MagicMock()
        mock.return_value = client
        yield client


@pytest.fixture
def mock_pc_client():
    """Mock Partner Central client."""
    with patch('hubspot_deal_update_sync.handler.get_partner_central_client') as mock:
        client = MagicMock()
        mock.return_value = client
        yield client


@pytest.fixture
def sample_webhook_event():
    """Sample HubSpot deal.propertyChange webhook event."""
    return {
        "httpMethod": "POST",
        "body": json.dumps({
            "objectId": 12345,
            "propertyName": "dealstage",
            "propertyValue": "presentationscheduled",
            "subscriptionType": "deal.propertyChange",
            "occurredAt": 1708257600000
        }),
        "headers": {}
    }


@pytest.fixture
def sample_deal():
    """Sample HubSpot deal object."""
    return {
        "id": "12345",
        "properties": {
            "dealname": "Test Deal #AWS",
            "dealstage": "presentationscheduled",
            "amount": "100000",
            "closedate": "2025-06-30",
            "description": "Test deal for AWS opportunity",
            "aws_opportunity_id": "O1234567890"
        }
    }


def test_deal_stage_update(mock_hubspot_client, mock_pc_client, sample_webhook_event, sample_deal):
    """Test that deal stage changes are synced to Partner Central."""
    from hubspot_deal_update_sync.handler import lambda_handler
    
    # Setup mocks
    mock_hubspot_client.get_deal.return_value = sample_deal
    mock_hubspot_client.get_deal_with_associations.return_value = (sample_deal, None, [])
    
    # Execute
    response = lambda_handler(sample_webhook_event, None)
    
    # Verify
    assert response["statusCode"] == 200
    mock_pc_client.update_opportunity.assert_called_once()
    
    # Check update payload contains lifecycle stage
    call_kwargs = mock_pc_client.update_opportunity.call_args[1]
    assert "LifeCycle" in call_kwargs
    assert call_kwargs["LifeCycle"]["Stage"] == "Technical Validation"
    
    # Verify note was added
    mock_hubspot_client.add_note_to_deal.assert_called_once()
    note_call = mock_hubspot_client.add_note_to_deal.call_args[0]
    assert "12345" in str(note_call[0])
    assert "dealstage" in note_call[1]
    
    # Verify sync status updated
    mock_hubspot_client.update_deal.assert_called_once()
    update_call = mock_hubspot_client.update_deal.call_args[0]
    assert update_call[0] == "12345"
    assert "aws_sync_status" in update_call[1]
    assert update_call[1]["aws_sync_status"] == "synced"


def test_amount_update(mock_hubspot_client, mock_pc_client, sample_deal):
    """Test that amount changes are synced to Partner Central."""
    from hubspot_deal_update_sync.handler import lambda_handler
    
    event = {
        "body": json.dumps({
            "objectId": 12345,
            "propertyName": "amount",
            "propertyValue": "150000"
        })
    }
    
    mock_hubspot_client.get_deal.return_value = sample_deal
    mock_hubspot_client.get_deal_with_associations.return_value = (sample_deal, None, [])
    
    response = lambda_handler(event, None)
    
    assert response["statusCode"] == 200
    mock_pc_client.update_opportunity.assert_called_once()
    
    call_kwargs = mock_pc_client.update_opportunity.call_args[1]
    assert "Project" in call_kwargs
    assert "ExpectedCustomerSpend" in call_kwargs["Project"]


def test_closedate_update(mock_hubspot_client, mock_pc_client, sample_deal):
    """Test that close date changes are synced to Partner Central."""
    from hubspot_deal_update_sync.handler import lambda_handler
    
    event = {
        "body": json.dumps({
            "objectId": 12345,
            "propertyName": "closedate",
            "propertyValue": "2025-12-31"
        })
    }
    
    mock_hubspot_client.get_deal.return_value = sample_deal
    mock_hubspot_client.get_deal_with_associations.return_value = (sample_deal, None, [])
    
    response = lambda_handler(event, None)
    
    assert response["statusCode"] == 200
    mock_pc_client.update_opportunity.assert_called_once()
    
    call_kwargs = mock_pc_client.update_opportunity.call_args[1]
    assert "LifeCycle" in call_kwargs
    assert "TargetCloseDate" in call_kwargs["LifeCycle"]


def test_skip_unsynced_property(mock_hubspot_client, mock_pc_client, sample_deal):
    """Test that non-synced properties are skipped."""
    from hubspot_deal_update_sync.handler import lambda_handler
    
    event = {
        "body": json.dumps({
            "objectId": 12345,
            "propertyName": "hubspot_owner_id",  # Not in SYNCED_PROPERTIES
            "propertyValue": "99999"
        })
    }
    
    mock_hubspot_client.get_deal.return_value = sample_deal
    
    response = lambda_handler(event, None)
    
    assert response["statusCode"] == 200
    mock_pc_client.update_opportunity.assert_not_called()
    body = json.loads(response["body"])
    assert "not in sync list" in body["message"]


def test_skip_deal_without_opportunity(mock_hubspot_client, mock_pc_client):
    """Test that deals without Partner Central opportunities are skipped."""
    from hubspot_deal_update_sync.handler import lambda_handler
    
    event = {
        "body": json.dumps({
            "objectId": 12345,
            "propertyName": "dealstage",
            "propertyValue": "presentationscheduled"
        })
    }
    
    deal_without_opp = {
        "id": "12345",
        "properties": {
            "dealname": "Test Deal",
            "dealstage": "presentationscheduled"
            # No aws_opportunity_id
        }
    }
    
    mock_hubspot_client.get_deal.return_value = deal_without_opp
    
    response = lambda_handler(event, None)
    
    assert response["statusCode"] == 200
    mock_pc_client.update_opportunity.assert_not_called()
    body = json.loads(response["body"])
    assert "No Partner Central opportunity linked" in body["message"]


def test_dealname_update_skipped(mock_hubspot_client, mock_pc_client, sample_deal):
    """Test that deal name updates are skipped (title is immutable in PC)."""
    from hubspot_deal_update_sync.handler import lambda_handler
    
    event = {
        "body": json.dumps({
            "objectId": 12345,
            "propertyName": "dealname",
            "propertyValue": "New Deal Name #AWS"
        })
    }
    
    mock_hubspot_client.get_deal.return_value = sample_deal
    mock_hubspot_client.get_deal_with_associations.return_value = (sample_deal, None, [])
    
    response = lambda_handler(event, None)
    
    assert response["statusCode"] == 200
    # Update should be called but without title field
    mock_pc_client.update_opportunity.assert_not_called()


def test_error_handling(mock_hubspot_client, mock_pc_client, sample_webhook_event, sample_deal):
    """Test that errors are handled gracefully."""
    from hubspot_deal_update_sync.handler import lambda_handler
    
    mock_hubspot_client.get_deal.return_value = sample_deal
    mock_hubspot_client.get_deal_with_associations.return_value = (sample_deal, None, [])
    mock_pc_client.update_opportunity.side_effect = Exception("PC API Error")
    
    response = lambda_handler(sample_webhook_event, None)
    
    assert response["statusCode"] == 500
    body = json.loads(response["body"])
    assert "error" in body
    assert "PC API Error" in body["error"]
