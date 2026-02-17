"""
Tests for the Partner Central Invitations → HubSpot Lambda handler.
"""

import json
import pytest
from unittest.mock import MagicMock, patch, call


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def pending_invitation():
    return {
        "Id": "inv-abc123",
        "Status": "PENDING",
        "EngagementTitle": "AWS Opportunity Share",
        "InvitationDate": "2025-01-15T10:00:00Z",
    }


@pytest.fixture
def invitation_detail():
    return {
        "Id": "inv-abc123",
        "Status": "PENDING",
        "Payload": {
            "OpportunityInvitation": {
                "OpportunitySummary": {
                    "Id": "opp-xyz789",
                }
            }
        },
    }


@pytest.fixture
def full_opportunity():
    return {
        "Id": "opp-xyz789",
        "Arn": "arn:aws:partnercentral::123456789012:opportunity/opp-xyz789",
        "Project": {
            "Title": "Enterprise Cloud Migration",
            "CustomerBusinessProblem": "Migrate 200 servers to AWS",
            "Stage": "Qualified",
            "TargetCompletionDate": "2025-09-30",
            "ExpectedCustomerSpend": [
                {"Amount": "75000", "CurrencyCode": "USD", "Frequency": "Monthly"}
            ],
        },
        "LifeCycle": {
            "Stage": "Qualified",
            "TargetCloseDate": "2025-09-30",
        },
        "Customer": {
            "Account": {
                "CompanyName": "Acme Corp",
                "CountryCode": "US",
            }
        },
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestPartnerCentralToHubSpot:
    @patch("partner_central_to_hubspot.handler.get_partner_central_client")
    @patch("partner_central_to_hubspot.handler.HubSpotClient")
    def test_accepts_invitation_and_creates_deal(
        self, MockHubSpot, mock_pc_client,
        pending_invitation, invitation_detail, full_opportunity
    ):
        """Pending invitations should be accepted and synced to HubSpot."""
        mock_hs = MockHubSpot.return_value
        mock_hs.search_deals_by_aws_opportunity_id.return_value = []
        mock_hs.create_deal.return_value = {"id": "hs-deal-001"}

        mock_pc = mock_pc_client.return_value
        mock_pc.list_engagement_invitations.return_value = {
            "EngagementInvitationSummaries": [pending_invitation],
            "NextToken": None,
        }
        mock_pc.get_engagement_invitation.return_value = invitation_detail
        mock_pc.accept_engagement_invitation.return_value = {}
        mock_pc.get_opportunity.return_value = full_opportunity

        from partner_central_to_hubspot.handler import lambda_handler

        result = lambda_handler({}, None)
        body = json.loads(result["body"])

        assert result["statusCode"] == 200
        assert body["invitationsProcessed"] == 1
        assert body["results"][0]["hubspotDealId"] == "hs-deal-001"
        assert body["results"][0]["partnerCentralOpportunityId"] == "opp-xyz789"

        mock_pc.accept_engagement_invitation.assert_called_once_with(
            Catalog="AWS",
            Identifier="inv-abc123",
        )
        mock_hs.create_deal.assert_called_once()

    @patch("partner_central_to_hubspot.handler.get_partner_central_client")
    @patch("partner_central_to_hubspot.handler.HubSpotClient")
    def test_skips_already_processed_invitation(
        self, MockHubSpot, mock_pc_client, pending_invitation
    ):
        """An invitation already synced to HubSpot should not be reprocessed."""
        mock_hs = MockHubSpot.return_value
        mock_hs.search_deals_by_aws_opportunity_id.return_value = [{"id": "existing-deal"}]

        mock_pc = mock_pc_client.return_value
        mock_pc.list_engagement_invitations.return_value = {
            "EngagementInvitationSummaries": [pending_invitation],
            "NextToken": None,
        }

        from partner_central_to_hubspot.handler import lambda_handler

        result = lambda_handler({}, None)
        body = json.loads(result["body"])

        assert body["invitationsProcessed"] == 0
        mock_pc.accept_engagement_invitation.assert_not_called()
        mock_hs.create_deal.assert_not_called()

    @patch("partner_central_to_hubspot.handler.get_partner_central_client")
    @patch("partner_central_to_hubspot.handler.HubSpotClient")
    def test_deal_name_includes_aws_tag(
        self, MockHubSpot, mock_pc_client,
        pending_invitation, invitation_detail, full_opportunity
    ):
        """Created HubSpot deal name should include #AWS tag."""
        mock_hs = MockHubSpot.return_value
        mock_hs.search_deals_by_aws_opportunity_id.return_value = []
        mock_hs.create_deal.return_value = {"id": "hs-deal-002"}

        mock_pc = mock_pc_client.return_value
        mock_pc.list_engagement_invitations.return_value = {
            "EngagementInvitationSummaries": [pending_invitation],
        }
        mock_pc.get_engagement_invitation.return_value = invitation_detail
        mock_pc.accept_engagement_invitation.return_value = {}
        mock_pc.get_opportunity.return_value = full_opportunity

        from partner_central_to_hubspot.handler import lambda_handler
        lambda_handler({}, None)

        created_properties = mock_hs.create_deal.call_args[0][0]
        assert "#AWS" in created_properties["dealname"]

    @patch("partner_central_to_hubspot.handler.get_partner_central_client")
    @patch("partner_central_to_hubspot.handler.HubSpotClient")
    def test_no_invitations_returns_zero(self, MockHubSpot, mock_pc_client):
        """When there are no pending invitations, nothing should be processed."""
        mock_pc = mock_pc_client.return_value
        mock_pc.list_engagement_invitations.return_value = {
            "EngagementInvitationSummaries": [],
        }

        from partner_central_to_hubspot.handler import lambda_handler

        result = lambda_handler({}, None)
        body = json.loads(result["body"])

        assert body["invitationsProcessed"] == 0
        MockHubSpot.return_value.create_deal.assert_not_called()

    @patch("partner_central_to_hubspot.handler.get_partner_central_client")
    @patch("partner_central_to_hubspot.handler.HubSpotClient")
    def test_handles_partial_errors_gracefully(
        self, MockHubSpot, mock_pc_client, pending_invitation
    ):
        """A failure on one invitation should not abort others."""
        inv2 = {**pending_invitation, "Id": "inv-def456"}

        mock_hs = MockHubSpot.return_value
        mock_hs.search_deals_by_aws_opportunity_id.return_value = []

        mock_pc = mock_pc_client.return_value
        mock_pc.list_engagement_invitations.return_value = {
            "EngagementInvitationSummaries": [pending_invitation, inv2],
        }
        # First invitation raises an error
        mock_pc.get_engagement_invitation.side_effect = [
            Exception("PC API error"),
            {
                "Id": "inv-def456",
                "Payload": {
                    "OpportunityInvitation": {
                        "OpportunitySummary": {"Id": "opp-2"}
                    }
                },
            },
        ]
        mock_pc.accept_engagement_invitation.return_value = {}
        mock_pc.get_opportunity.return_value = {
            "Id": "opp-2", "Arn": "arn:aws:partnercentral:::opp-2",
            "Project": {"Title": "Test #AWS", "Stage": "Prospect"},
            "LifeCycle": {"Stage": "Prospect", "TargetCloseDate": "2025-12-31"},
            "Customer": {"Account": {"CompanyName": "Test"}},
        }
        mock_hs.create_deal.return_value = {"id": "hs-999"}

        from partner_central_to_hubspot.handler import lambda_handler

        result = lambda_handler({}, None)
        body = json.loads(result["body"])

        assert body["errors"] == 1
        assert body["invitationsProcessed"] == 1
