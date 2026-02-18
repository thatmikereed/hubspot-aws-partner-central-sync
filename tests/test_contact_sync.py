"""
Tests for Contact Sync handler.
"""

import json
import pytest
from unittest.mock import MagicMock, patch


@pytest.fixture
def mock_handler_clients():
    """Mock clients via BaseLambdaHandler properties."""
    with patch("common.hubspot_client.HubSpotClient") as mock_hs, patch(
        "common.aws_client.get_partner_central_client"
    ) as mock_pc:
        hs_client = MagicMock()
        pc_client = MagicMock()
        mock_hs.return_value = hs_client
        mock_pc.return_value = pc_client
        yield hs_client, pc_client


@pytest.fixture
def sample_eventbridge_event():
    """Sample EventBridge opportunity.updated event."""
    return {"detail": {"opportunity": {"identifier": "O1234567890"}}}


@pytest.fixture
def sample_manual_event():
    """Sample manual API call event."""
    return {"opportunityId": "O1234567890"}


@pytest.fixture
def sample_scheduled_event():
    """Sample scheduled sync event (empty)."""
    return {}


@pytest.fixture
def sample_pc_opportunity():
    """Sample Partner Central opportunity with contacts."""
    return {
        "Identifier": "O1234567890",
        "LifeCycle": {"Stage": "Prospect"},
        "Project": {"Title": "Test Deal #AWS"},
        "Customer": {
            "Account": {"CompanyName": "Test Company"},
            "Contacts": [
                {
                    "Email": "john@example.com",
                    "FirstName": "John",
                    "LastName": "Doe",
                    "Phone": "+15551234",
                    "BusinessTitle": "CTO",
                }
            ],
        },
        "OpportunityTeam": [
            {
                "Email": "aws-seller@amazon.com",
                "FirstName": "AWS",
                "LastName": "Seller",
                "BusinessTitle": "Account Manager",
            }
        ],
    }


@pytest.fixture
def sample_hubspot_deal():
    """Sample HubSpot deal with AWS opportunity."""
    return {
        "id": "12345",
        "properties": {
            "dealname": "Test Deal #AWS",
            "aws_opportunity_id": "O1234567890",
        },
    }


def test_eventbridge_event_syncs_contacts(
    mock_handler_clients,
    sample_eventbridge_event,
    sample_pc_opportunity,
    sample_hubspot_deal,
):
    """Test that EventBridge opportunity.updated event triggers contact sync."""
    from contact_sync.handler import lambda_handler

    hs_client, pc_client = mock_handler_clients

    # Setup mocks
    pc_client.get_opportunity.return_value = sample_pc_opportunity
    hs_client.search_deals_by_aws_opportunity_id.return_value = [sample_hubspot_deal]

    # Mock contact search (doesn't exist) and creation
    search_response = MagicMock()
    search_response.json.return_value = {"results": []}
    hs_client.session.post.return_value = search_response

    create_response = MagicMock()
    create_response.json.return_value = {"id": "contact1"}
    create_response.raise_for_status = MagicMock()
    hs_client.session.post.return_value = create_response

    # Mock association
    assoc_response = MagicMock()
    assoc_response.status_code = 200
    hs_client.session.put.return_value = assoc_response

    # Execute
    response = lambda_handler(sample_eventbridge_event, None)

    # Verify success
    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert body["opportunitiesProcessed"] == 1
    assert body["contactsSynced"] == 2  # 1 customer + 1 team


def test_manual_event_syncs_specific_opportunity(
    mock_handler_clients,
    sample_manual_event,
    sample_pc_opportunity,
    sample_hubspot_deal,
):
    """Test that manual API call with opportunityId syncs that opportunity."""
    from contact_sync.handler import lambda_handler

    hs_client, pc_client = mock_handler_clients

    # Setup mocks
    pc_client.get_opportunity.return_value = sample_pc_opportunity
    hs_client.search_deals_by_aws_opportunity_id.return_value = [sample_hubspot_deal]

    # Mock contact operations
    search_response = MagicMock()
    search_response.json.return_value = {"results": []}
    search_response.raise_for_status = MagicMock()

    create_response = MagicMock()
    create_response.json.return_value = {"id": "contact1"}
    create_response.raise_for_status = MagicMock()

    hs_client.session.post.side_effect = [
        search_response,
        create_response,
        search_response,
        create_response,
    ]

    assoc_response = MagicMock()
    assoc_response.status_code = 200
    hs_client.session.put.return_value = assoc_response

    # Execute
    response = lambda_handler(sample_manual_event, None)

    # Verify
    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert body["opportunitiesProcessed"] == 1
    assert body["contactsSynced"] == 2


def test_scheduled_event_syncs_all_opportunities(
    mock_handler_clients,
    sample_scheduled_event,
    sample_pc_opportunity,
    sample_hubspot_deal,
):
    """Test that scheduled event syncs all deals with aws_opportunity_id."""
    from contact_sync.handler import lambda_handler

    hs_client, pc_client = mock_handler_clients

    # Mock deal search returns deals with opportunity IDs
    deal_search_response = MagicMock()
    deal_search_response.json.return_value = {
        "results": [
            {"properties": {"aws_opportunity_id": "O111"}},
            {"properties": {"aws_opportunity_id": "O222"}},
        ]
    }
    deal_search_response.raise_for_status = MagicMock()

    # Setup other mocks
    pc_client.get_opportunity.return_value = sample_pc_opportunity
    hs_client.search_deals_by_aws_opportunity_id.return_value = [sample_hubspot_deal]

    # Mock contact operations
    contact_response = MagicMock()
    contact_response.json.return_value = {"results": [], "id": "contact1"}
    contact_response.status_code = 200
    contact_response.raise_for_status = MagicMock()

    hs_client.session.post.return_value = contact_response
    hs_client.session.patch.return_value = contact_response
    hs_client.session.put.return_value = contact_response

    # Need to handle both deal search and contact search
    def post_side_effect(*args, **kwargs):
        url = args[0] if args else kwargs.get("url", "")
        if "deals/search" in url:
            return deal_search_response
        else:
            return contact_response

    hs_client.session.post.side_effect = post_side_effect

    # Execute
    response = lambda_handler(sample_scheduled_event, None)

    # Verify
    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert body["opportunitiesProcessed"] == 2


def test_opportunity_without_contacts_returns_none(
    mock_handler_clients, sample_manual_event, sample_hubspot_deal
):
    """Test that opportunities without contacts are skipped."""
    from contact_sync.handler import lambda_handler

    hs_client, pc_client = mock_handler_clients

    # Setup mocks - opportunity with no contacts
    empty_opp = {
        "Identifier": "O1234567890",
        "Customer": {"Account": {"CompanyName": "Test"}},
        "OpportunityTeam": [],
    }
    pc_client.get_opportunity.return_value = empty_opp

    # Execute
    response = lambda_handler(sample_manual_event, None)

    # Verify success but no contacts synced
    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert body["contactsSynced"] == 0


def test_missing_deal_for_opportunity_skips_sync(
    mock_handler_clients, sample_manual_event, sample_pc_opportunity
):
    """Test that opportunities without corresponding HubSpot deals are skipped."""
    from contact_sync.handler import lambda_handler

    hs_client, pc_client = mock_handler_clients

    # Setup mocks
    pc_client.get_opportunity.return_value = sample_pc_opportunity
    hs_client.search_deals_by_aws_opportunity_id.return_value = []  # No deal found

    # Execute
    response = lambda_handler(sample_manual_event, None)

    # Verify success but no contacts synced
    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert body["contactsSynced"] == 0


def test_contact_without_email_is_skipped(
    mock_handler_clients, sample_manual_event, sample_hubspot_deal
):
    """Test that contacts without email addresses are skipped."""
    from contact_sync.handler import lambda_handler

    hs_client, pc_client = mock_handler_clients

    # Setup mocks - opportunity with contact missing email
    opp_no_email = {
        "Identifier": "O1234567890",
        "Customer": {
            "Contacts": [{"FirstName": "John", "LastName": "Doe"}]  # No email
        },
        "OpportunityTeam": [],
    }
    pc_client.get_opportunity.return_value = opp_no_email
    hs_client.search_deals_by_aws_opportunity_id.return_value = [sample_hubspot_deal]

    # Execute
    response = lambda_handler(sample_manual_event, None)

    # Verify success but no contacts synced
    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert body["contactsSynced"] == 0
