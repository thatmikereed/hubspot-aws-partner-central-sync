"""
Tests for the HubSpot → Partner Central Lambda handler.
"""

import json
import pytest
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_deal():
    return {
        "id": "12345",
        "properties": {
            "dealname": "BigCorp Cloud Migration #AWS",
            "amount": "50000",
            "closedate": "2025-06-30T00:00:00Z",
            "dealstage": "qualifiedtobuy",
            "description": "Migrate on-prem workloads to AWS",
            "aws_opportunity_id": None,
        },
    }


@pytest.fixture
def deal_without_aws_tag():
    return {
        "id": "99999",
        "properties": {
            "dealname": "Regular Deal No Tag",
            "amount": "10000",
            "closedate": "2025-06-30T00:00:00Z",
            "dealstage": "appointmentscheduled",
            "description": "Standard deal",
            "aws_opportunity_id": None,
        },
    }


@pytest.fixture
def hubspot_webhook_event(sample_deal):
    payload = [
        {
            "subscriptionType": "deal.creation",
            "objectId": int(sample_deal["id"]),
            "eventId": 1001,
        }
    ]
    return {
        "body": json.dumps(payload),
        "isBase64Encoded": False,
        "headers": {},
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestHubSpotToPartnerCentral:
    @patch("hubspot_to_partner_central.handler.get_partner_central_client")
    @patch("hubspot_to_partner_central.handler.HubSpotClient")
    def test_creates_opportunity_for_aws_tagged_deal(
        self, MockHubSpot, mock_pc_client, sample_deal, hubspot_webhook_event
    ):
        """A deal containing #AWS should create a Partner Central opportunity."""
        mock_hs = MockHubSpot.return_value
        mock_hs.get_deal.return_value = sample_deal
        mock_hs.update_deal.return_value = {}

        mock_pc = mock_pc_client.return_value
        mock_pc.create_opportunity.return_value = {
            "Id": "pc-opp-001",
            "OpportunityArn": "arn:aws:partnercentral::123456789012:opportunity/pc-opp-001",
        }

        from hubspot_to_partner_central.handler import lambda_handler

        result = lambda_handler(hubspot_webhook_event, None)
        body = json.loads(result["body"])

        assert result["statusCode"] == 200
        assert body["processed"] == 1
        assert body["results"][0]["partnerCentralOpportunityId"] == "pc-opp-001"
        mock_pc.create_opportunity.assert_called_once()
        mock_hs.update_deal.assert_called_once_with(
            "12345",
            {
                "aws_opportunity_id": "pc-opp-001",
                "aws_opportunity_arn": "arn:aws:partnercentral::123456789012:opportunity/pc-opp-001",
                "aws_sync_status": "synced",
            },
        )

    @patch("hubspot_to_partner_central.handler.get_partner_central_client")
    @patch("hubspot_to_partner_central.handler.HubSpotClient")
    def test_skips_deal_without_aws_tag(
        self, MockHubSpot, mock_pc_client, deal_without_aws_tag
    ):
        """A deal without #AWS in the title should be ignored."""
        mock_hs = MockHubSpot.return_value
        mock_hs.get_deal.return_value = deal_without_aws_tag

        payload = [{"subscriptionType": "deal.creation", "objectId": 99999}]
        event = {"body": json.dumps(payload), "isBase64Encoded": False, "headers": {}}

        from hubspot_to_partner_central.handler import lambda_handler

        result = lambda_handler(event, None)
        body = json.loads(result["body"])

        assert result["statusCode"] == 200
        assert body["processed"] == 0
        mock_pc_client.return_value.create_opportunity.assert_not_called()

    @patch("hubspot_to_partner_central.handler.get_partner_central_client")
    @patch("hubspot_to_partner_central.handler.HubSpotClient")
    def test_skips_already_synced_deal(
        self, MockHubSpot, mock_pc_client, sample_deal, hubspot_webhook_event
    ):
        """A deal with an existing PC opportunity ID should not create a duplicate."""
        already_synced = dict(sample_deal)
        already_synced["properties"] = dict(sample_deal["properties"])
        already_synced["properties"]["aws_opportunity_id"] = "existing-opp-001"

        mock_hs = MockHubSpot.return_value
        mock_hs.get_deal.return_value = already_synced

        from hubspot_to_partner_central.handler import lambda_handler

        result = lambda_handler(hubspot_webhook_event, None)
        body = json.loads(result["body"])

        assert body["processed"] == 0
        mock_pc_client.return_value.create_opportunity.assert_not_called()

    def test_returns_400_for_invalid_json(self):
        """Malformed webhook body should return 400."""
        from hubspot_to_partner_central.handler import lambda_handler

        result = lambda_handler({"body": "not-json", "headers": {}}, None)
        assert result["statusCode"] == 400

    @patch("hubspot_to_partner_central.handler.get_partner_central_client")
    @patch("hubspot_to_partner_central.handler.HubSpotClient")
    def test_non_creation_events_are_skipped(self, MockHubSpot, mock_pc_client):
        """Only deal.creation events should be processed."""
        payload = [{"subscriptionType": "deal.propertyChange", "objectId": 12345}]
        event = {"body": json.dumps(payload), "headers": {}}

        from hubspot_to_partner_central.handler import lambda_handler

        result = lambda_handler(event, None)
        body = json.loads(result["body"])

        assert body["processed"] == 0
        MockHubSpot.return_value.get_deal.assert_not_called()
