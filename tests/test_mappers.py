"""
Tests for the HubSpot ↔ Partner Central field mappers.
"""

import pytest
from common.mappers import (
    hubspot_deal_to_partner_central,
    partner_central_opportunity_to_hubspot,
    HUBSPOT_STAGE_TO_PC,
    PC_STAGE_TO_HUBSPOT,
)


class TestHubSpotToPartnerCentralMapper:
    def test_basic_mapping(self):
        deal = {
            "id": "42",
            "properties": {
                "dealname": "Cloud Project #AWS",
                "amount": "120000",
                "closedate": "2025-12-31T00:00:00Z",
                "dealstage": "qualifiedtobuy",
                "description": "Lift and shift migration",
            },
        }
        result = hubspot_deal_to_partner_central(deal)

        assert result["Project"]["Title"] == "Cloud Project #AWS"
        assert result["Project"]["Stage"] == "Qualified"
        assert result["Project"]["TargetCompletionDate"] == "2025-12-31"
        assert result["ClientToken"] == "hs-42"
        assert result["Project"]["ExpectedCustomerSpend"][0]["Amount"] == "120000.0"

    def test_stage_mapping_closed_won(self):
        deal = {
            "id": "1",
            "properties": {
                "dealname": "Deal #AWS",
                "dealstage": "closedwon",
                "closedate": None,
            },
        }
        result = hubspot_deal_to_partner_central(deal)
        assert result["Project"]["Stage"] == "Launched"

    def test_unknown_stage_defaults_to_prospect(self):
        deal = {
            "id": "1",
            "properties": {
                "dealname": "Deal #AWS",
                "dealstage": "some_custom_stage",
                "closedate": None,
            },
        }
        result = hubspot_deal_to_partner_central(deal)
        assert result["Project"]["Stage"] == "Prospect"

    def test_missing_amount_uses_zero(self):
        deal = {
            "id": "1",
            "properties": {"dealname": "Deal #AWS", "closedate": None},
        }
        result = hubspot_deal_to_partner_central(deal)
        assert result["Project"]["ExpectedCustomerSpend"][0]["Amount"] == "0"

    def test_idempotency_key_includes_deal_id(self):
        deal = {"id": "999", "properties": {"dealname": "X #AWS", "closedate": None}}
        result = hubspot_deal_to_partner_central(deal)
        assert result["ClientToken"] == "hs-999"


class TestPartnerCentralToHubSpotMapper:
    def test_basic_mapping(self):
        opportunity = {
            "Id": "opp-001",
            "Arn": "arn:aws:pc:::opp-001",
            "Project": {
                "Title": "AWS Opportunity",
                "CustomerBusinessProblem": "Migrate workloads",
                "Stage": "Qualified",
                "ExpectedCustomerSpend": [
                    {"Amount": "50000", "CurrencyCode": "USD"}
                ],
            },
            "LifeCycle": {
                "Stage": "Qualified",
                "TargetCloseDate": "2025-09-30",
            },
            "Customer": {"Account": {"CompanyName": "Acme"}},
        }
        result = partner_central_opportunity_to_hubspot(opportunity)

        assert result["dealname"] == "AWS Opportunity #AWS"
        assert result["dealstage"] == "qualifiedtobuy"
        assert result["aws_opportunity_id"] == "opp-001"
        assert result["amount"] == "50000"
        assert "#AWS" in result["dealname"]

    def test_aws_tag_not_duplicated(self):
        opportunity = {
            "Id": "opp-002",
            "Arn": "arn:",
            "Project": {"Title": "Already Tagged #AWS", "Stage": "Prospect"},
            "LifeCycle": {"Stage": "Prospect", "TargetCloseDate": "2025-12-31"},
            "Customer": {"Account": {}},
        }
        result = partner_central_opportunity_to_hubspot(opportunity)
        # Should not become "Already Tagged #AWS #AWS"
        assert result["dealname"].count("#AWS") == 1

    def test_invitation_id_stored_when_provided(self):
        opportunity = {
            "Id": "opp-003",
            "Arn": "arn:",
            "Project": {"Title": "Test", "Stage": "Prospect"},
            "LifeCycle": {"Stage": "Prospect", "TargetCloseDate": "2025-12-31"},
            "Customer": {"Account": {}},
        }
        result = partner_central_opportunity_to_hubspot(
            opportunity, invitation_id="inv-abc"
        )
        assert result["aws_invitation_id"] == "inv-abc"

    def test_stage_roundtrip(self):
        """All PC stages should map back to a valid HubSpot stage."""
        for pc_stage, hs_stage in PC_STAGE_TO_HUBSPOT.items():
            assert hs_stage in HUBSPOT_STAGE_TO_PC, (
                f"PC stage '{pc_stage}' maps to unknown HubSpot stage '{hs_stage}'"
            )
