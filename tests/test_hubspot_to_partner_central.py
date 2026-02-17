"""
Tests for the HubSpot → Partner Central Lambda handler.
Covers deal.creation, deal.propertyChange, and title-immutability protection.
"""

import json
import pytest
from datetime import date, timedelta
from unittest.mock import MagicMock, patch, call


@pytest.fixture
def future_date():
    return (date.today() + timedelta(days=60)).isoformat() + "T00:00:00Z"


@pytest.fixture
def sample_deal(future_date):
    return {
        "id": "12345",
        "properties": {
            "dealname": "BigCorp Cloud Migration #AWS",
            "amount": "50000",
            "closedate": future_date,
            "dealstage": "qualifiedtobuy",
            "description": "Migrate 200 on-prem servers to AWS for cost savings and scalability.",
            "aws_opportunity_id": None,
            "aws_opportunity_title": None,
            "aws_review_status": None,
        },
    }


@pytest.fixture
def deal_without_aws_tag(future_date):
    return {
        "id": "99999",
        "properties": {
            "dealname": "Regular Deal No Tag",
            "amount": "10000",
            "closedate": future_date,
            "dealstage": "appointmentscheduled",
            "description": "A standard deal with no AWS involvement.",
            "aws_opportunity_id": None,
        },
    }


@pytest.fixture
def synced_deal(future_date):
    """A deal that already has an AWS opportunity ID."""
    return {
        "id": "12345",
        "properties": {
            "dealname": "BigCorp Cloud Migration #AWS",
            "amount": "50000",
            "closedate": future_date,
            "dealstage": "qualifiedtobuy",
            "description": "Already synced deal.",
            "aws_opportunity_id": "O9999999",
            "aws_opportunity_title": "BigCorp Cloud Migration #AWS",
            "aws_review_status": "Approved",
        },
    }


@pytest.fixture
def creation_event():
    return {
        "body": json.dumps([
            {"subscriptionType": "deal.creation", "objectId": 12345, "eventId": 1}
        ]),
        "isBase64Encoded": False,
        "headers": {},
    }


@pytest.fixture
def property_change_event():
    return {
        "body": json.dumps([
            {
                "subscriptionType": "deal.propertyChange",
                "objectId": 12345,
                "propertyName": "dealstage",
                "propertyValue": "presentationscheduled",
                "eventId": 2,
            }
        ]),
        "isBase64Encoded": False,
        "headers": {},
    }


@pytest.fixture
def title_change_event():
    return {
        "body": json.dumps([
            {
                "subscriptionType": "deal.propertyChange",
                "objectId": 12345,
                "propertyName": "dealname",
                "propertyValue": "New Name That Changed #AWS",
                "eventId": 3,
            }
        ]),
        "isBase64Encoded": False,
        "headers": {},
    }


# ---------------------------------------------------------------------------
# deal.creation tests
# ---------------------------------------------------------------------------

class TestDealCreation:

    @patch("hubspot_to_partner_central.handler.get_partner_central_client")
    @patch("hubspot_to_partner_central.handler.HubSpotClient")
    def test_creates_opportunity_for_aws_tagged_deal(
        self, MockHubSpot, mock_pc_factory, sample_deal, creation_event
    ):
        mock_hs = MockHubSpot.return_value
        mock_hs.get_deal_with_associations.return_value = (sample_deal, None, [])
        mock_hs.update_deal.return_value = {}

        mock_pc = mock_pc_factory.return_value
        mock_pc.create_opportunity.return_value = {"Id": "O1234567"}

        from hubspot_to_partner_central.handler import lambda_handler
        result = lambda_handler(creation_event, None)
        body = json.loads(result["body"])

        assert result["statusCode"] == 200
        assert body["processed"] == 1
        assert body["results"][0]["partnerCentralOpportunityId"] == "O1234567"
        assert body["results"][0]["action"] == "created"
        mock_pc.create_opportunity.assert_called_once()

    @patch("hubspot_to_partner_central.handler.get_partner_central_client")
    @patch("hubspot_to_partner_central.handler.HubSpotClient")
    def test_writes_opportunity_id_and_title_back(
        self, MockHubSpot, mock_pc_factory, sample_deal, creation_event
    ):
        """After creating PC opportunity, both ID and title should be stored in HubSpot."""
        mock_hs = MockHubSpot.return_value
        mock_hs.get_deal_with_associations.return_value = (sample_deal, None, [])
        mock_pc = mock_pc_factory.return_value
        mock_pc.create_opportunity.return_value = {"Id": "O1234567"}

        from hubspot_to_partner_central.handler import lambda_handler
        lambda_handler(creation_event, None)

        call_kwargs = mock_hs.update_deal.call_args[0][1]
        assert call_kwargs["aws_opportunity_id"] == "O1234567"
        assert "aws_opportunity_title" in call_kwargs
        assert call_kwargs["aws_review_status"] == "Pending Submission"

    @patch("hubspot_to_partner_central.handler.get_partner_central_client")
    @patch("hubspot_to_partner_central.handler.HubSpotClient")
    def test_skips_deal_without_aws_tag(
        self, MockHubSpot, mock_pc_factory, deal_without_aws_tag
    ):
        mock_hs = MockHubSpot.return_value
        mock_hs.get_deal_with_associations.return_value = (deal_without_aws_tag, None, [])
        event = {
            "body": json.dumps([{"subscriptionType": "deal.creation", "objectId": 99999}]),
            "headers": {},
        }

        from hubspot_to_partner_central.handler import lambda_handler
        result = lambda_handler(event, None)
        body = json.loads(result["body"])

        assert body["processed"] == 0
        mock_pc_factory.return_value.create_opportunity.assert_not_called()

    @patch("hubspot_to_partner_central.handler.get_partner_central_client")
    @patch("hubspot_to_partner_central.handler.HubSpotClient")
    def test_skips_already_synced_deal(
        self, MockHubSpot, mock_pc_factory, synced_deal, creation_event
    ):
        mock_hs = MockHubSpot.return_value
        mock_hs.get_deal_with_associations.return_value = (synced_deal, None, [])

        from hubspot_to_partner_central.handler import lambda_handler
        result = lambda_handler(creation_event, None)
        body = json.loads(result["body"])

        assert body["processed"] == 0
        mock_pc_factory.return_value.create_opportunity.assert_not_called()

    @patch("hubspot_to_partner_central.handler.get_partner_central_client")
    @patch("hubspot_to_partner_central.handler.HubSpotClient")
    def test_solution_associated_when_env_var_set(
        self, MockHubSpot, mock_pc_factory, sample_deal, creation_event, monkeypatch
    ):
        monkeypatch.setenv("PARTNER_CENTRAL_SOLUTION_ID", "S-0000001")
        mock_hs = MockHubSpot.return_value
        mock_hs.get_deal_with_associations.return_value = (sample_deal, None, [])
        mock_pc = mock_pc_factory.return_value
        mock_pc.create_opportunity.return_value = {"Id": "O1234567"}

        from hubspot_to_partner_central.handler import lambda_handler
        lambda_handler(creation_event, None)

        mock_pc.associate_opportunity.assert_called_once_with(
            Catalog="AWS",
            OpportunityIdentifier="O1234567",
            RelatedEntityIdentifier="S-0000001",
            RelatedEntityType="Solutions",
        )

    def test_returns_400_for_invalid_json(self):
        from hubspot_to_partner_central.handler import lambda_handler
        result = lambda_handler({"body": "not-json", "headers": {}}, None)
        assert result["statusCode"] == 400

    @patch("hubspot_to_partner_central.handler.get_partner_central_client")
    @patch("hubspot_to_partner_central.handler.HubSpotClient")
    def test_non_creation_events_skipped(self, MockHubSpot, mock_pc_factory):
        event = {
            "body": json.dumps([{"subscriptionType": "deal.deletion", "objectId": 1}]),
            "headers": {},
        }
        from hubspot_to_partner_central.handler import lambda_handler
        result = lambda_handler(event, None)
        body = json.loads(result["body"])
        assert body["processed"] == 0
        MockHubSpot.return_value.get_deal_with_associations.assert_not_called()


# ---------------------------------------------------------------------------
# deal.propertyChange — title immutability
# ---------------------------------------------------------------------------

class TestTitleImmutability:

    @patch("hubspot_to_partner_central.handler.get_partner_central_client")
    @patch("hubspot_to_partner_central.handler.HubSpotClient")
    def test_title_change_does_not_update_pc(
        self, MockHubSpot, mock_pc_factory, synced_deal, title_change_event
    ):
        """When dealname changes, UpdateOpportunity should be called but without Title."""
        mock_hs = MockHubSpot.return_value
        mock_hs.get_deal_with_associations.return_value = (synced_deal, None, [])
        mock_pc = mock_pc_factory.return_value
        mock_pc.get_opportunity.return_value = {
            "Id": "O9999999",
            "LifeCycle": {"ReviewStatus": "Approved",
                          "TargetCloseDate": (date.today() + timedelta(days=60)).isoformat()},
            "Project": {"Title": "BigCorp Cloud Migration #AWS"},
        }
        mock_pc.update_opportunity.return_value = {}

        from hubspot_to_partner_central.handler import lambda_handler
        result = lambda_handler(title_change_event, None)
        body = json.loads(result["body"])

        # Processed (update went through, minus the title)
        assert body["processed"] == 1
        assert body["results"][0]["action"] == "updated"

        # Verify Title was NOT in the update payload
        update_call = mock_pc.update_opportunity.call_args
        project_arg = update_call.kwargs.get("Project") or update_call[1].get("Project", {})
        assert "Title" not in project_arg

    @patch("hubspot_to_partner_central.handler.get_partner_central_client")
    @patch("hubspot_to_partner_central.handler.HubSpotClient")
    def test_title_change_adds_note_to_deal(
        self, MockHubSpot, mock_pc_factory, synced_deal, title_change_event
    ):
        """A note should be added to the deal when title change is blocked."""
        mock_hs = MockHubSpot.return_value
        mock_hs.get_deal_with_associations.return_value = (synced_deal, None, [])
        mock_pc = mock_pc_factory.return_value
        mock_pc.get_opportunity.return_value = {
            "Id": "O9999999",
            "LifeCycle": {"ReviewStatus": "Approved",
                          "TargetCloseDate": (date.today() + timedelta(days=60)).isoformat()},
            "Project": {"Title": "BigCorp Cloud Migration #AWS"},
        }

        from hubspot_to_partner_central.handler import lambda_handler
        lambda_handler(title_change_event, None)

        mock_hs.add_note_to_deal.assert_called_once()
        note_body = mock_hs.add_note_to_deal.call_args[0][1]
        assert "title" in note_body.lower() or "immutable" in note_body.lower()

    @patch("hubspot_to_partner_central.handler.get_partner_central_client")
    @patch("hubspot_to_partner_central.handler.HubSpotClient")
    def test_update_blocked_when_submitted(
        self, MockHubSpot, mock_pc_factory, synced_deal, property_change_event
    ):
        """No update should be sent when PC opportunity is Submitted."""
        mock_hs = MockHubSpot.return_value
        mock_hs.get_deal_with_associations.return_value = (synced_deal, None, [])
        mock_pc = mock_pc_factory.return_value
        mock_pc.get_opportunity.return_value = {
            "Id": "O9999999",
            "LifeCycle": {"ReviewStatus": "Submitted"},
            "Project": {"Title": "Test"},
        }

        from hubspot_to_partner_central.handler import lambda_handler
        result = lambda_handler(property_change_event, None)
        body = json.loads(result["body"])

        assert body["processed"] == 1
        assert body["results"][0]["action"] == "blocked"
        mock_pc.update_opportunity.assert_not_called()

    @patch("hubspot_to_partner_central.handler.get_partner_central_client")
    @patch("hubspot_to_partner_central.handler.HubSpotClient")
    def test_property_change_on_unsynced_deal_skipped(
        self, MockHubSpot, mock_pc_factory, sample_deal, property_change_event
    ):
        """Deal with no aws_opportunity_id should not trigger any PC call."""
        mock_hs = MockHubSpot.return_value
        mock_hs.get_deal_with_associations.return_value = (sample_deal, None, [])

        from hubspot_to_partner_central.handler import lambda_handler
        result = lambda_handler(property_change_event, None)
        body = json.loads(result["body"])

        assert body["processed"] == 0
        mock_pc_factory.return_value.update_opportunity.assert_not_called()
