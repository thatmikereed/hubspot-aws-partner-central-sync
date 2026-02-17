"""
Tests for the Partner Central Invitations → HubSpot Lambda handler.
Validates the correct use of StartEngagementByAcceptingInvitationTask.
"""

import json
import pytest
from datetime import date, timedelta
from unittest.mock import MagicMock, patch


@pytest.fixture
def pending_invitation():
    return {
        "Id": "arn:aws:partnercentral:us-east-1:aws:catalog/AWS/engagement-invitation/engi-abc123",
        "Status": "PENDING",
        "EngagementTitle": "AWS Opportunity Share",
    }


@pytest.fixture
def inv_detail():
    return {
        "Id": "arn:aws:partnercentral:us-east-1:aws:catalog/AWS/engagement-invitation/engi-abc123",
        "Status": "PENDING",
        "PayloadType": "OpportunityInvitation",
        "Payload": {
            "OpportunityInvitation": {
                "OpportunitySummary": {"Id": "O7654321"}
            }
        },
    }


@pytest.fixture
def task_response_complete():
    return {
        "TaskId": "task-xyz",
        "TaskStatus": "COMPLETE",
        "OpportunityId": "O7654321",
    }


@pytest.fixture
def full_opportunity():
    future = (date.today() + timedelta(days=60)).isoformat()
    return {
        "Id": "O7654321",
        "Arn": "arn:aws:partnercentral:::O7654321",
        "Project": {
            "Title": "Enterprise Cloud Migration",
            "CustomerBusinessProblem": "Migrate 200 servers to AWS for cost and scale benefits.",
            "ExpectedCustomerSpend": [
                {"Amount": "75000", "CurrencyCode": "USD", "Frequency": "Monthly"}
            ],
        },
        "LifeCycle": {
            "Stage": "Qualified",
            "ReviewStatus": "Approved",
            "TargetCloseDate": future,
        },
        "Customer": {
            "Account": {"CompanyName": "Acme Corp", "CountryCode": "US"}
        },
    }


# ---------------------------------------------------------------------------
# Core workflow tests
# ---------------------------------------------------------------------------

class TestPartnerCentralToHubSpot:

    @patch("partner_central_to_hubspot.handler.get_partner_central_client")
    @patch("partner_central_to_hubspot.handler.HubSpotClient")
    def test_accepts_via_correct_api_method(
        self, MockHubSpot, mock_pc_factory,
        pending_invitation, inv_detail, task_response_complete, full_opportunity
    ):
        """
        CRITICAL: Must call start_engagement_by_accepting_invitation_task,
        NOT accept_engagement_invitation (which does not exist in the API).
        """
        mock_hs = MockHubSpot.return_value
        mock_hs.search_deals_by_aws_invitation_id.return_value = []
        mock_hs.create_deal.return_value = {"id": "hs-001"}

        mock_pc = mock_pc_factory.return_value
        mock_pc.list_engagement_invitations.return_value = {
            "EngagementInvitationSummaries": [pending_invitation]
        }
        mock_pc.get_engagement_invitation.return_value = inv_detail
        mock_pc.start_engagement_by_accepting_invitation_task.return_value = task_response_complete
        mock_pc.get_opportunity.return_value = full_opportunity

        from partner_central_to_hubspot.handler import lambda_handler
        lambda_handler({}, None)

        mock_pc.start_engagement_by_accepting_invitation_task.assert_called_once()
        # The old wrong method should never be called
        assert not hasattr(mock_pc, "accept_engagement_invitation") or \
               not mock_pc.accept_engagement_invitation.called

    @patch("partner_central_to_hubspot.handler.get_partner_central_client")
    @patch("partner_central_to_hubspot.handler.HubSpotClient")
    def test_creates_hubspot_deal_after_acceptance(
        self, MockHubSpot, mock_pc_factory,
        pending_invitation, inv_detail, task_response_complete, full_opportunity
    ):
        mock_hs = MockHubSpot.return_value
        mock_hs.search_deals_by_aws_invitation_id.return_value = []
        mock_hs.create_deal.return_value = {"id": "hs-deal-001"}

        mock_pc = mock_pc_factory.return_value
        mock_pc.list_engagement_invitations.return_value = {
            "EngagementInvitationSummaries": [pending_invitation]
        }
        mock_pc.get_engagement_invitation.return_value = inv_detail
        mock_pc.start_engagement_by_accepting_invitation_task.return_value = task_response_complete
        mock_pc.get_opportunity.return_value = full_opportunity

        from partner_central_to_hubspot.handler import lambda_handler
        result = lambda_handler({}, None)
        body = json.loads(result["body"])

        assert result["statusCode"] == 200
        assert body["invitationsProcessed"] == 1
        assert body["results"][0]["hubspotDealId"] == "hs-deal-001"
        assert body["results"][0]["partnerCentralOpportunityId"] == "O7654321"
        mock_hs.create_deal.assert_called_once()

    @patch("partner_central_to_hubspot.handler.get_partner_central_client")
    @patch("partner_central_to_hubspot.handler.HubSpotClient")
    def test_deal_has_aws_tag_in_name(
        self, MockHubSpot, mock_pc_factory,
        pending_invitation, inv_detail, task_response_complete, full_opportunity
    ):
        mock_hs = MockHubSpot.return_value
        mock_hs.search_deals_by_aws_invitation_id.return_value = []
        mock_hs.create_deal.return_value = {"id": "hs-002"}

        mock_pc = mock_pc_factory.return_value
        mock_pc.list_engagement_invitations.return_value = {
            "EngagementInvitationSummaries": [pending_invitation]
        }
        mock_pc.get_engagement_invitation.return_value = inv_detail
        mock_pc.start_engagement_by_accepting_invitation_task.return_value = task_response_complete
        mock_pc.get_opportunity.return_value = full_opportunity

        from partner_central_to_hubspot.handler import lambda_handler
        lambda_handler({}, None)

        created_props = mock_hs.create_deal.call_args[0][0]
        assert "#AWS" in created_props["dealname"]

    @patch("partner_central_to_hubspot.handler.get_partner_central_client")
    @patch("partner_central_to_hubspot.handler.HubSpotClient")
    def test_canonical_title_stored_separately(
        self, MockHubSpot, mock_pc_factory,
        pending_invitation, inv_detail, task_response_complete, full_opportunity
    ):
        """aws_opportunity_title must store the raw PC title (without #AWS)."""
        mock_hs = MockHubSpot.return_value
        mock_hs.search_deals_by_aws_invitation_id.return_value = []
        mock_hs.create_deal.return_value = {"id": "hs-003"}

        mock_pc = mock_pc_factory.return_value
        mock_pc.list_engagement_invitations.return_value = {
            "EngagementInvitationSummaries": [pending_invitation]
        }
        mock_pc.get_engagement_invitation.return_value = inv_detail
        mock_pc.start_engagement_by_accepting_invitation_task.return_value = task_response_complete
        mock_pc.get_opportunity.return_value = full_opportunity

        from partner_central_to_hubspot.handler import lambda_handler
        lambda_handler({}, None)

        created_props = mock_hs.create_deal.call_args[0][0]
        # dealname has #AWS; aws_opportunity_title has the raw title
        assert created_props["aws_opportunity_title"] == "Enterprise Cloud Migration"

    @patch("partner_central_to_hubspot.handler.get_partner_central_client")
    @patch("partner_central_to_hubspot.handler.HubSpotClient")
    def test_skips_already_processed_invitation(
        self, MockHubSpot, mock_pc_factory, pending_invitation
    ):
        mock_hs = MockHubSpot.return_value
        mock_hs.search_deals_by_aws_invitation_id.return_value = [{"id": "existing"}]

        mock_pc = mock_pc_factory.return_value
        mock_pc.list_engagement_invitations.return_value = {
            "EngagementInvitationSummaries": [pending_invitation]
        }

        from partner_central_to_hubspot.handler import lambda_handler
        result = lambda_handler({}, None)
        body = json.loads(result["body"])

        assert body["invitationsProcessed"] == 0
        mock_pc.start_engagement_by_accepting_invitation_task.assert_not_called()
        mock_hs.create_deal.assert_not_called()

    @patch("partner_central_to_hubspot.handler.get_partner_central_client")
    @patch("partner_central_to_hubspot.handler.HubSpotClient")
    def test_polls_task_when_pending(
        self, MockHubSpot, mock_pc_factory,
        pending_invitation, inv_detail, full_opportunity
    ):
        """When task returns IN_PROGRESS, it should poll until COMPLETE."""
        mock_hs = MockHubSpot.return_value
        mock_hs.search_deals_by_aws_invitation_id.return_value = []
        mock_hs.create_deal.return_value = {"id": "hs-004"}

        mock_pc = mock_pc_factory.return_value
        mock_pc.list_engagement_invitations.return_value = {
            "EngagementInvitationSummaries": [pending_invitation]
        }
        mock_pc.get_engagement_invitation.return_value = inv_detail
        # First call: IN_PROGRESS; second call: COMPLETE
        mock_pc.start_engagement_by_accepting_invitation_task.return_value = {
            "TaskId": "task-pending",
            "TaskStatus": "IN_PROGRESS",
            "OpportunityId": None,
        }
        mock_pc.get_engagement_by_accepting_invitation_task.side_effect = [
            {"TaskStatus": "IN_PROGRESS", "OpportunityId": None},
            {"TaskStatus": "COMPLETE", "OpportunityId": "O7654321"},
        ]
        mock_pc.get_opportunity.return_value = full_opportunity

        from partner_central_to_hubspot.handler import lambda_handler
        with patch("partner_central_to_hubspot.handler.time.sleep"):
            result = lambda_handler({}, None)

        body = json.loads(result["body"])
        assert body["invitationsProcessed"] == 1
        assert mock_pc.get_engagement_by_accepting_invitation_task.call_count == 2

    @patch("partner_central_to_hubspot.handler.get_partner_central_client")
    @patch("partner_central_to_hubspot.handler.HubSpotClient")
    def test_no_invitations_returns_zero(self, MockHubSpot, mock_pc_factory):
        mock_pc = mock_pc_factory.return_value
        mock_pc.list_engagement_invitations.return_value = {
            "EngagementInvitationSummaries": []
        }

        from partner_central_to_hubspot.handler import lambda_handler
        result = lambda_handler({}, None)
        body = json.loads(result["body"])

        assert body["invitationsProcessed"] == 0
        MockHubSpot.return_value.create_deal.assert_not_called()

    @patch("partner_central_to_hubspot.handler.get_partner_central_client")
    @patch("partner_central_to_hubspot.handler.HubSpotClient")
    def test_partial_errors_do_not_abort_batch(
        self, MockHubSpot, mock_pc_factory, full_opportunity
    ):
        """One failing invitation must not prevent others from being processed."""
        inv1 = {
            "Id": "arn:.../engi-fail001",
            "Status": "PENDING",
        }
        inv2 = {
            "Id": "arn:.../engi-ok002",
            "Status": "PENDING",
        }

        mock_hs = MockHubSpot.return_value
        mock_hs.search_deals_by_aws_invitation_id.return_value = []
        mock_hs.create_deal.return_value = {"id": "hs-005"}

        mock_pc = mock_pc_factory.return_value
        mock_pc.list_engagement_invitations.return_value = {
            "EngagementInvitationSummaries": [inv1, inv2]
        }
        # First invitation's get_engagement_invitation raises an error
        mock_pc.get_engagement_invitation.side_effect = [
            Exception("Simulated API error"),
            {
                "Id": inv2["Id"],
                "PayloadType": "OpportunityInvitation",
                "Payload": {
                    "OpportunityInvitation": {
                        "OpportunitySummary": {"Id": "O7654321"}
                    }
                },
            },
        ]
        mock_pc.start_engagement_by_accepting_invitation_task.return_value = {
            "TaskId": "t1", "TaskStatus": "COMPLETE", "OpportunityId": "O7654321"
        }
        mock_pc.get_opportunity.return_value = full_opportunity

        from partner_central_to_hubspot.handler import lambda_handler
        result = lambda_handler({}, None)
        body = json.loads(result["body"])

        assert body["errors"] == 1
        assert body["invitationsProcessed"] == 1
