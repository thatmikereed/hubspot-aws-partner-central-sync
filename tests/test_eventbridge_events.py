"""
Tests for EventBridge event processing, specifically the Closed Lost notification feature.
"""

import pytest
from unittest.mock import Mock


@pytest.fixture
def mock_hubspot_client():
    """Mock HubSpot client."""
    client = Mock()
    client.session = Mock()
    client.session.post = Mock()
    client.session.put = Mock()
    return client


@pytest.fixture
def mock_pc_client():
    """Mock Partner Central client."""
    return Mock()


@pytest.fixture
def sample_opportunity_closed_lost():
    """Sample opportunity marked as Closed Lost."""
    return {
        "Id": "O1234567890",
        "Arn": "arn:aws:partnercentral:us-east-1:123456789012:opportunity/O1234567890",
        "Catalog": "AWS",
        "LifeCycle": {
            "Stage": "Closed Lost",
            "ReviewStatus": "Approved",
            "TargetCloseDate": "2026-03-15",
        },
        "Project": {
            "Title": "Test Deal #AWS",
            "CustomerBusinessProblem": "Test business problem that is long enough to meet requirements",
            "DeliveryModels": ["SaaS or PaaS"],
            "ExpectedCustomerSpend": [
                {
                    "Amount": "10000.00",
                    "CurrencyCode": "USD",
                    "Frequency": "Monthly",
                    "TargetCompany": "AWS",
                }
            ],
        },
        "Customer": {
            "Account": {
                "CompanyName": "Test Company",
                "Industry": "Software and Internet",
            }
        },
    }


@pytest.fixture
def sample_opportunity_qualified():
    """Sample opportunity in Qualified stage."""
    return {
        "Id": "O1234567890",
        "Arn": "arn:aws:partnercentral:us-east-1:123456789012:opportunity/O1234567890",
        "Catalog": "AWS",
        "LifeCycle": {
            "Stage": "Qualified",
            "ReviewStatus": "Approved",
            "TargetCloseDate": "2026-03-15",
        },
        "Project": {
            "Title": "Test Deal #AWS",
            "CustomerBusinessProblem": "Test business problem that is long enough to meet requirements",
            "DeliveryModels": ["SaaS or PaaS"],
            "ExpectedCustomerSpend": [
                {
                    "Amount": "10000.00",
                    "CurrencyCode": "USD",
                    "Frequency": "Monthly",
                    "TargetCompany": "AWS",
                }
            ],
        },
        "Customer": {
            "Account": {
                "CompanyName": "Test Company",
                "Industry": "Software and Internet",
            }
        },
    }


@pytest.fixture
def sample_aws_summary():
    """Sample AWS opportunity summary."""
    return {
        "Catalog": "AWS",
        "LifeCycle": {
            "ReviewStatus": "Approved",
            "InvolvementType": "Co-Sell",
        },
        "Insights": {
            "EngagementScore": 85,
        },
        "OpportunityTeam": [
            {
                "FirstName": "Jane",
                "LastName": "Smith",
                "Email": "jsmith@amazon.com",
            }
        ],
    }


def test_closed_lost_creates_notification_instead_of_updating_stage(
    mock_hubspot_client,
    mock_pc_client,
    sample_opportunity_closed_lost,
    sample_aws_summary,
):
    """
    Test that when AWS marks an opportunity as Closed Lost, we create a
    notification task instead of automatically updating the HubSpot deal stage.
    """
    from eventbridge_events.handler import EventBridgeEventsHandler

    # Setup mock responses
    mock_hubspot_client.search_deals_by_aws_opportunity_id.return_value = [
        {"id": "12345"}
    ]
    mock_hubspot_client.get_deal.return_value = {
        "id": "12345",
        "properties": {
            "dealname": "Test Deal #AWS",
            "hubspot_owner_id": "9876",
        },
    }
    mock_pc_client.get_opportunity.return_value = sample_opportunity_closed_lost
    mock_pc_client.get_aws_opportunity_summary.return_value = sample_aws_summary

    # Mock task creation response
    mock_hubspot_client.session.post.return_value.raise_for_status = Mock()
    mock_hubspot_client.session.post.return_value.json.return_value = {"id": "task-123"}
    mock_hubspot_client.session.put.return_value.raise_for_status = Mock()
    mock_hubspot_client.add_note_to_deal = Mock()
    mock_hubspot_client.update_deal = Mock()

    # Create event
    detail = {"opportunity": {"identifier": "O1234567890"}}

    # Execute
    handler = EventBridgeEventsHandler()
    handler._hubspot_client = mock_hubspot_client
    handler._pc_client = mock_pc_client
    result = handler._handle_opportunity_updated(detail)

    # Verify task was created
    assert mock_hubspot_client.session.post.called
    task_call = mock_hubspot_client.session.post.call_args
    assert "tasks" in task_call[0][0]  # URL contains 'tasks'

    task_data = task_call[1]["json"]
    assert "Closed Lost" in task_data["properties"]["hs_task_subject"]
    assert (
        "reach out to your AWS Account Executive"
        in task_data["properties"]["hs_task_body"]
    )
    assert task_data["properties"]["hs_task_priority"] == "HIGH"

    # Verify task was associated with deal
    assert mock_hubspot_client.session.put.called
    assoc_call = mock_hubspot_client.session.put.call_args
    assert "associations" in assoc_call[0][0]

    # Verify note was added
    assert mock_hubspot_client.add_note_to_deal.called
    note_call = mock_hubspot_client.add_note_to_deal.call_args
    assert "Closed Lost" in note_call[0][1]

    # Verify deal was updated BUT stage was NOT updated to closedlost
    assert mock_hubspot_client.update_deal.called
    update_call = mock_hubspot_client.update_deal.call_args
    updates = update_call[0][1]
    assert "dealstage" not in updates or updates.get("dealstage") != "closedlost"

    # Verify result indicates success
    assert result["status"] == "synced"


def test_non_closed_lost_stage_updates_normally(
    mock_hubspot_client,
    mock_pc_client,
    sample_opportunity_qualified,
    sample_aws_summary,
):
    """
    Test that non-Closed Lost stage changes still sync normally to HubSpot.
    """
    from eventbridge_events.handler import EventBridgeEventsHandler

    # Setup mock responses
    mock_hubspot_client.search_deals_by_aws_opportunity_id.return_value = [
        {"id": "12345"}
    ]
    mock_pc_client.get_opportunity.return_value = sample_opportunity_qualified
    mock_pc_client.get_aws_opportunity_summary.return_value = sample_aws_summary
    mock_hubspot_client.update_deal = Mock()
    mock_hubspot_client.add_note_to_deal = Mock()

    # Create event
    detail = {"opportunity": {"identifier": "O1234567890"}}

    # Execute
    handler = EventBridgeEventsHandler()
    handler._hubspot_client = mock_hubspot_client
    handler._pc_client = mock_pc_client
    result = handler._handle_opportunity_updated(detail)

    # Verify deal was updated with the stage
    assert mock_hubspot_client.update_deal.called
    update_call = mock_hubspot_client.update_deal.call_args
    updates = update_call[0][1]
    assert "dealstage" in updates
    assert updates["dealstage"] == "qualifiedtobuy"  # Qualified maps to qualifiedtobuy

    # Verify task creation was NOT called (no closed lost notification)
    # The post call might be for other things, but not for tasks
    if mock_hubspot_client.session.post.called:
        # Check that it wasn't for creating a task
        for call in mock_hubspot_client.session.post.call_args_list:
            url = call[0][0] if call[0] else ""
            assert "tasks" not in url

    # Verify result
    assert result["status"] == "synced"


def test_closed_lost_notification_without_deal_owner(
    mock_hubspot_client,
    mock_pc_client,
    sample_opportunity_closed_lost,
    sample_aws_summary,
):
    """
    Test that closed lost notification works even when deal has no owner.
    """
    from eventbridge_events.handler import EventBridgeEventsHandler

    # Setup mock responses with no owner
    mock_hubspot_client.search_deals_by_aws_opportunity_id.return_value = [
        {"id": "12345"}
    ]
    mock_hubspot_client.get_deal.return_value = {
        "id": "12345",
        "properties": {
            "dealname": "Test Deal #AWS",
            # No hubspot_owner_id
        },
    }
    mock_pc_client.get_opportunity.return_value = sample_opportunity_closed_lost
    mock_pc_client.get_aws_opportunity_summary.return_value = sample_aws_summary

    # Mock task creation response
    mock_hubspot_client.session.post.return_value.raise_for_status = Mock()
    mock_hubspot_client.session.post.return_value.json.return_value = {"id": "task-123"}
    mock_hubspot_client.session.put.return_value.raise_for_status = Mock()
    mock_hubspot_client.add_note_to_deal = Mock()
    mock_hubspot_client.update_deal = Mock()

    # Create event
    detail = {"opportunity": {"identifier": "O1234567890"}}

    # Execute - should not raise exception
    handler = EventBridgeEventsHandler()
    handler._hubspot_client = mock_hubspot_client
    handler._pc_client = mock_pc_client
    result = handler._handle_opportunity_updated(detail)

    # Verify task was still created
    assert mock_hubspot_client.session.post.called
    task_call = mock_hubspot_client.session.post.call_args
    task_data = task_call[1]["json"]

    # Owner should not be in the task data
    assert "hubspot_owner_id" not in task_data["properties"]

    # Verify result
    assert result["status"] == "synced"
