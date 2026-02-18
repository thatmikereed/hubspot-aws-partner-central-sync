"""
Tests for Company Sync handler.
"""

import json
import pytest
from unittest.mock import MagicMock, patch


@pytest.fixture
def mock_hubspot_client():
    """Mock HubSpotClient."""
    with patch('company_sync.handler.HubSpotClient') as mock:
        client = MagicMock()
        mock.return_value = client
        yield client


@pytest.fixture
def mock_pc_client():
    """Mock Partner Central client."""
    with patch('company_sync.handler.get_partner_central_client') as mock:
        client = MagicMock()
        mock.return_value = client
        yield client


@pytest.fixture
def sample_company_webhook():
    """Sample HubSpot company.propertyChange webhook event."""
    return {
        "httpMethod": "POST",
        "body": json.dumps({
            "objectId": "67890",
            "propertyName": "city",
            "propertyValue": "Seattle",
            "subscriptionType": "company.propertyChange"
        }),
        "headers": {}
    }


@pytest.fixture
def sample_company():
    """Sample HubSpot company object."""
    return {
        "id": "67890",
        "properties": {
            "name": "Acme Corporation",
            "industry": "COMPUTER_SOFTWARE",
            "website": "https://acme.com",
            "address": "123 Main St",
            "city": "Seattle",
            "state": "WA",
            "zip": "98101",
            "country": "US"
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
                "CompanyName": "Old Company Name",
                "Industry": "Other",
                "Address": {
                    "CountryCode": "US"
                }
            },
            "Contacts": [
                {
                    "Email": "john@example.com",
                    "FirstName": "John",
                    "LastName": "Doe"
                }
            ]
        }
    }


def test_company_property_change_syncs_to_opportunity(
    mock_hubspot_client,
    mock_pc_client,
    sample_company_webhook,
    sample_company,
    sample_deal,
    sample_opportunity
):
    """Test that company property changes are synced to Partner Central opportunity."""
    from company_sync.handler import lambda_handler
    
    # Setup mocks
    mock_hubspot_client.get_company.return_value = sample_company
    mock_hubspot_client.get_company_associations.return_value = ["12345"]
    mock_hubspot_client.get_deal.return_value = sample_deal
    mock_pc_client.get_opportunity.return_value = sample_opportunity
    mock_hubspot_client.now_timestamp_ms.return_value = 1708257600000
    
    # Execute
    response = lambda_handler(sample_company_webhook, None)
    
    # Verify success
    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert body["dealsSynced"] == 1
    assert body["companyId"] == "67890"
    assert body["propertyChanged"] == "city"
    
    # Verify Partner Central was updated
    mock_pc_client.update_opportunity.assert_called_once()
    update_call = mock_pc_client.update_opportunity.call_args[1]
    
    # Check that company account was updated
    assert "Customer" in update_call
    assert "Account" in update_call["Customer"]
    account = update_call["Customer"]["Account"]
    
    assert account["CompanyName"] == "Acme Corporation"
    assert account["Industry"] == "Software and Internet"
    assert account["WebsiteUrl"] == "https://acme.com"
    assert account["Address"]["City"] == "Seattle"
    assert account["Address"]["StateOrRegion"] == "WA"
    assert account["Address"]["PostalCode"] == "98101"
    assert account["Address"]["CountryCode"] == "US"
    assert account["Address"]["StreetAddress"] == "123 Main St"
    
    # Verify contacts were preserved
    assert "Contacts" in update_call["Customer"]
    assert len(update_call["Customer"]["Contacts"]) == 1
    assert update_call["Customer"]["Contacts"][0]["Email"] == "john@example.com"
    
    # Verify note was created
    mock_hubspot_client.create_deal_note.assert_called_once()
    note_text = mock_hubspot_client.create_deal_note.call_args[0][1]
    assert "Company Information Synced" in note_text
    assert "Acme Corporation" in note_text
    assert "city" in note_text
    
    # Verify sync timestamp was updated
    mock_hubspot_client.update_deal.assert_called_once_with(
        "12345",
        {"aws_contact_company_last_sync": 1708257600000}
    )


def test_company_with_no_deals_skips_sync(
    mock_hubspot_client,
    mock_pc_client,
    sample_company_webhook,
    sample_company
):
    """Test that companies with no associated deals are skipped."""
    from company_sync.handler import lambda_handler
    
    # Setup mocks - no associated deals
    mock_hubspot_client.get_company.return_value = sample_company
    mock_hubspot_client.get_company_associations.return_value = []
    
    # Execute
    response = lambda_handler(sample_company_webhook, None)
    
    # Verify success but no sync
    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert body["message"] == "No deals to sync"
    assert body["companyId"] == "67890"


def test_company_deal_without_opportunity_skips_sync(
    mock_hubspot_client,
    mock_pc_client,
    sample_company_webhook,
    sample_company,
    sample_deal
):
    """Test that deals without AWS opportunities are skipped."""
    from company_sync.handler import lambda_handler
    
    # Setup mocks
    mock_hubspot_client.get_company.return_value = sample_company
    mock_hubspot_client.get_company_associations.return_value = ["12345"]
    
    # Deal without aws_opportunity_id
    deal_no_opp = sample_deal.copy()
    deal_no_opp["properties"] = {"dealname": "Test Deal"}
    mock_hubspot_client.get_deal.return_value = deal_no_opp
    
    # Execute
    response = lambda_handler(sample_company_webhook, None)
    
    # Verify success but deals skipped
    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert body["dealsSynced"] == 0
    assert body["dealsSkipped"] == 1


def test_map_company_to_partner_central_account():
    """Test company to account mapping function."""
    from company_sync.handler import _map_company_to_partner_central_account
    
    company_props = {
        "name": "Test Corp",
        "industry": "FINANCIAL_SERVICES",
        "website": "testcorp.com",  # No https://
        "address": "456 Oak Ave",
        "city": "New York",
        "state": "NY",
        "zip": "10001",
        "country": "US"
    }
    
    result = _map_company_to_partner_central_account(company_props)
    
    assert result["CompanyName"] == "Test Corp"
    assert result["Industry"] == "Financial Services"
    assert result["WebsiteUrl"] == "https://testcorp.com"  # https:// added
    assert result["Address"]["StreetAddress"] == "456 Oak Ave"
    assert result["Address"]["City"] == "New York"
    assert result["Address"]["StateOrRegion"] == "NY"
    assert result["Address"]["PostalCode"] == "10001"
    assert result["Address"]["CountryCode"] == "US"


def test_map_company_with_minimal_data():
    """Test company mapping with only required fields."""
    from company_sync.handler import _map_company_to_partner_central_account
    
    company_props = {
        "name": "Minimal Corp"
    }
    
    result = _map_company_to_partner_central_account(company_props)
    
    assert result["CompanyName"] == "Minimal Corp"
    assert result["Industry"] == "Other"  # Default
    assert result["Address"]["CountryCode"] == "US"  # Default
    assert "WebsiteUrl" not in result
    assert "City" not in result["Address"]
    assert "StateOrRegion" not in result["Address"]


def test_industry_mapping():
    """Test industry mapping from HubSpot to Partner Central."""
    from company_sync.handler import _map_company_to_partner_central_account, HUBSPOT_INDUSTRY_TO_PC
    
    # Test specific mappings
    test_cases = [
        ("AEROSPACE", "Aerospace"),
        ("HEALTHCARE", "Healthcare"),
        ("SOFTWARE", "Software and Internet"),
        ("MANUFACTURING", "Manufacturing"),
        ("UNKNOWN", "Other")  # Unmapped defaults to Other
    ]
    
    for hs_industry, expected_pc in test_cases:
        company_props = {"name": "Test", "industry": hs_industry}
        result = _map_company_to_partner_central_account(company_props)
        assert result["Industry"] == expected_pc


def test_company_not_found_returns_404(
    mock_hubspot_client,
    mock_pc_client,
    sample_company_webhook
):
    """Test that missing company returns 404."""
    from company_sync.handler import lambda_handler
    
    # Setup mocks - company not found
    mock_hubspot_client.get_company.return_value = None
    
    # Execute
    response = lambda_handler(sample_company_webhook, None)
    
    # Verify 404
    assert response["statusCode"] == 404
    body = json.loads(response["body"])
    assert "not found" in body["error"].lower()


def test_website_url_normalization():
    """Test that website URLs are normalized to include https://."""
    from company_sync.handler import _map_company_to_partner_central_account
    
    # URL without protocol
    result1 = _map_company_to_partner_central_account({
        "name": "Test",
        "website": "example.com"
    })
    assert result1["WebsiteUrl"] == "https://example.com"
    
    # URL with https://
    result2 = _map_company_to_partner_central_account({
        "name": "Test",
        "website": "https://example.com"
    })
    assert result2["WebsiteUrl"] == "https://example.com"
    
    # URL with http://
    result3 = _map_company_to_partner_central_account({
        "name": "Test",
        "website": "http://example.com"
    })
    assert result3["WebsiteUrl"] == "http://example.com"


def test_long_company_name_truncated():
    """Test that company names longer than 120 chars are truncated."""
    from company_sync.handler import _map_company_to_partner_central_account
    
    long_name = "A" * 150  # 150 characters
    result = _map_company_to_partner_central_account({"name": long_name})
    
    assert len(result["CompanyName"]) == 120
    assert result["CompanyName"] == "A" * 120


def test_multiple_deals_all_synced(
    mock_hubspot_client,
    mock_pc_client,
    sample_company_webhook,
    sample_company,
    sample_deal,
    sample_opportunity
):
    """Test that all deals associated with a company are synced."""
    from company_sync.handler import lambda_handler
    
    # Setup mocks - company has 2 deals with opportunities
    mock_hubspot_client.get_company.return_value = sample_company
    mock_hubspot_client.get_company_associations.return_value = ["12345", "67890"]
    
    deal1 = sample_deal.copy()
    deal2 = {
        "id": "67890",
        "properties": {
            "dealname": "Second Deal #AWS",
            "aws_opportunity_id": "O0987654321"
        }
    }
    
    mock_hubspot_client.get_deal.side_effect = [deal1, deal2]
    
    opp1 = sample_opportunity.copy()
    opp2 = sample_opportunity.copy()
    opp2["Id"] = "O0987654321"
    
    mock_pc_client.get_opportunity.side_effect = [opp1, opp2]
    mock_hubspot_client.now_timestamp_ms.return_value = 1708257600000
    
    # Execute
    response = lambda_handler(sample_company_webhook, None)
    
    # Verify both deals were synced
    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert body["dealsSynced"] == 2
    assert body["dealsFound"] == 2
    
    # Verify Partner Central was updated twice
    assert mock_pc_client.update_opportunity.call_count == 2
