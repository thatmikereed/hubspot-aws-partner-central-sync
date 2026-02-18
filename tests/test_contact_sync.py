"""
Tests for Contact Sync handler.
"""

import json
import pytest
from unittest.mock import MagicMock, patch


@pytest.fixture
def mock_hubspot_client():
    """Mock HubSpotClient."""
    with patch('contact_sync.handler.HubSpotClient') as mock:
        client = MagicMock()
        mock.return_value = client
        yield client


@pytest.fixture
def mock_pc_client():
    """Mock Partner Central client."""
    with patch('contact_sync.handler.get_partner_central_client') as mock:
        client = MagicMock()
        mock.return_value = client
        yield client


@pytest.fixture
def sample_contact_webhook():
    """Sample HubSpot contact.propertyChange webhook event."""
    return {
        "httpMethod": "POST",
        "body": json.dumps({
            "objectId": "54321",
            "propertyName": "email",
            "propertyValue": "newemail@example.com",
            "subscriptionType": "contact.propertyChange"
        }),
        "headers": {}
    }


@pytest.fixture
def sample_contact():
    """Sample HubSpot contact object."""
    return {
        "id": "54321",
        "properties": {
            "email": "newemail@example.com",
            "firstname": "John",
            "lastname": "Doe",
            "phone": "+1-555-1234",
            "jobtitle": "CTO"
        }
    }


@pytest.fixture
def sample_deal():
    """Sample HubSpot deal with AWS opportunity."""
    return {
        "id": "12345",
        "properties": {
            "dealname": "Test Deal #AWS",
            "aws_opportunity_id": "O1234567890"
        }
    }


@pytest.fixture
def sample_opportunity():
    """Sample Partner Central opportunity."""
    return {
        "Id": "O1234567890",
        "LifeCycle": {"Stage": "Prospect"},
        "Project": {"Title": "Test Deal #AWS"},
        "Customer": {
            "Account": {
                "CompanyName": "Test Company"
            },
            "Contacts": [
                {
                    "Email": "oldemail@example.com",
                    "FirstName": "John",
                    "LastName": "Doe"
                }
            ]
        }
    }


def test_contact_email_change_syncs_to_opportunity(
    mock_hubspot_client,
    mock_pc_client,
    sample_contact_webhook,
    sample_contact,
    sample_deal,
    sample_opportunity
):
    """Test that contact email changes are synced to Partner Central opportunity."""
    from contact_sync.handler import lambda_handler
    
    # Setup mocks
    mock_hubspot_client.get_contact.return_value = sample_contact
    mock_hubspot_client.get_contact_associations.return_value = ["12345"]
    mock_hubspot_client.get_deal.return_value = sample_deal
    mock_hubspot_client.get_deal_associations.return_value = ["54321"]
    mock_pc_client.get_opportunity.return_value = sample_opportunity
    mock_hubspot_client.now_timestamp_ms.return_value = 1708257600000
    
    # Execute
    response = lambda_handler(sample_contact_webhook, None)
    
    # Verify success
    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert body["dealsSynced"] == 1
    assert body["contactId"] == "54321"
    
    # Verify Partner Central was updated
    mock_pc_client.update_opportunity.assert_called_once()
    update_call = mock_pc_client.update_opportunity.call_args[1]
    
    # Check that contacts were updated
    assert "Customer" in update_call
    assert "Contacts" in update_call["Customer"]
    contacts = update_call["Customer"]["Contacts"]
    assert len(contacts) == 1
    assert contacts[0]["Email"] == "newemail@example.com"
    assert contacts[0]["FirstName"] == "John"
    assert contacts[0]["LastName"] == "Doe"
    
    # Verify note was created
    mock_hubspot_client.create_deal_note.assert_called_once()
    note_text = mock_hubspot_client.create_deal_note.call_args[0][1]
    assert "Contact Information Synced" in note_text
    assert "email" in note_text
    
    # Verify sync timestamp was updated
    mock_hubspot_client.update_deal.assert_called_once_with(
        "12345",
        {"aws_contact_company_last_sync": 1708257600000}
    )


def test_contact_with_no_deals_skips_sync(
    mock_hubspot_client,
    sample_contact_webhook,
    sample_contact
):
    """Test that contacts with no associated deals are skipped."""
    from contact_sync.handler import lambda_handler
    
    # Setup mocks - no associated deals
    mock_hubspot_client.get_contact.return_value = sample_contact
    mock_hubspot_client.get_contact_associations.return_value = []
    
    # Execute
    response = lambda_handler(sample_contact_webhook, None)
    
    # Verify success but no sync
    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert body["message"] == "No deals to sync"
    assert body["contactId"] == "54321"


def test_contact_deal_without_opportunity_skips_sync(
    mock_hubspot_client,
    sample_contact_webhook,
    sample_contact,
    sample_deal
):
    """Test that deals without AWS opportunities are skipped."""
    from contact_sync.handler import lambda_handler
    
    # Setup mocks
    mock_hubspot_client.get_contact.return_value = sample_contact
    mock_hubspot_client.get_contact_associations.return_value = ["12345"]
    
    # Deal without aws_opportunity_id
    deal_no_opp = sample_deal.copy()
    deal_no_opp["properties"] = {"dealname": "Test Deal"}
    mock_hubspot_client.get_deal.return_value = deal_no_opp
    
    # Execute
    response = lambda_handler(sample_contact_webhook, None)
    
    # Verify success but deals skipped
    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert body["dealsSynced"] == 0
    assert body["dealsSkipped"] == 1


def test_map_contacts_to_partner_central():
    """Test contact mapping function."""
    from contact_sync.handler import _map_contacts_to_partner_central
    
    contacts = [
        {
            "id": "1",
            "properties": {
                "email": "john@example.com",
                "firstname": "John",
                "lastname": "Doe",
                "phone": "555-1234",
                "jobtitle": "CTO"
            }
        },
        {
            "id": "2",
            "properties": {
                "email": "jane@example.com",
                "firstname": "Jane",
                "lastname": "Smith"
            }
        }
    ]
    
    result = _map_contacts_to_partner_central(contacts)
    
    assert len(result) == 2
    
    # First contact with all fields
    assert result[0]["Email"] == "john@example.com"
    assert result[0]["FirstName"] == "John"
    assert result[0]["LastName"] == "Doe"
    assert result[0]["Phone"] == "+1555-1234"  # Phone should be sanitized
    assert result[0]["BusinessTitle"] == "CTO"
    
    # Second contact with minimal fields
    assert result[1]["Email"] == "jane@example.com"
    assert result[1]["FirstName"] == "Jane"
    assert result[1]["LastName"] == "Smith"
    assert "Phone" not in result[1]
    assert "BusinessTitle" not in result[1]


def test_sanitize_phone():
    """Test phone number sanitization."""
    from contact_sync.handler import _sanitize_phone
    
    # Valid phone with country code
    assert _sanitize_phone("+1-555-1234") == "+1-555-1234"
    
    # Phone without country code (assumes US)
    assert _sanitize_phone("555-1234") == "+1555-1234"
    
    # International phone
    assert _sanitize_phone("+44 20 1234 5678") == "+442012345678"
    
    # None/empty
    assert _sanitize_phone(None) is None
    assert _sanitize_phone("") is None
    
    # Too short
    assert _sanitize_phone("123") is None
    
    # Too long
    assert _sanitize_phone("12345678901234567890") is None


def test_contact_not_found_returns_404(
    mock_hubspot_client,
    sample_contact_webhook
):
    """Test that missing contact returns 404."""
    from contact_sync.handler import lambda_handler
    
    # Setup mocks - contact not found
    mock_hubspot_client.get_contact.return_value = None
    
    # Execute
    response = lambda_handler(sample_contact_webhook, None)
    
    # Verify 404
    assert response["statusCode"] == 404
    body = json.loads(response["body"])
    assert "not found" in body["error"].lower()


def test_multiple_contacts_per_deal_are_synced(
    mock_hubspot_client,
    mock_pc_client,
    sample_contact_webhook,
    sample_contact,
    sample_deal,
    sample_opportunity
):
    """Test that all contacts associated with a deal are synced."""
    from contact_sync.handler import lambda_handler
    
    # Setup mocks
    mock_hubspot_client.get_contact.side_effect = [
        sample_contact,  # First call for webhook contact
        sample_contact,  # Second call for deal's contacts
        {
            "id": "99999",
            "properties": {
                "email": "another@example.com",
                "firstname": "Another",
                "lastname": "Person"
            }
        }
    ]
    mock_hubspot_client.get_contact_associations.return_value = ["12345"]
    mock_hubspot_client.get_deal.return_value = sample_deal
    mock_hubspot_client.get_deal_associations.return_value = ["54321", "99999"]
    mock_pc_client.get_opportunity.return_value = sample_opportunity
    mock_hubspot_client.now_timestamp_ms.return_value = 1708257600000
    
    # Execute
    response = lambda_handler(sample_contact_webhook, None)
    
    # Verify success
    assert response["statusCode"] == 200
    
    # Verify all contacts were included in update
    update_call = mock_pc_client.update_opportunity.call_args[1]
    contacts = update_call["Customer"]["Contacts"]
    assert len(contacts) == 2
    emails = [c["Email"] for c in contacts]
    assert "newemail@example.com" in emails
    assert "another@example.com" in emails
