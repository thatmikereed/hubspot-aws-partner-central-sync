"""
Tests for the HubSpot ↔ Partner Central field mappers.
Validates all business-validation constraints and the title-immutability rule.
"""

import pytest
from datetime import date, timedelta
from unittest.mock import patch

from common.mappers import (
    hubspot_deal_to_partner_central,
    hubspot_deal_to_partner_central_update,
    partner_central_opportunity_to_hubspot,
    HUBSPOT_STAGE_TO_PC,
    PC_STAGE_TO_HUBSPOT,
    PC_VALID_INDUSTRIES,
    PC_VALID_DELIVERY_MODELS,
    _sanitize_business_problem,
    _sanitize_website,
    _map_industry,
    _safe_close_date,
    _build_spend,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def minimal_deal():
    """A HubSpot deal with only the bare minimum fields."""
    return {
        "id": "100",
        "properties": {
            "dealname": "Minimal Deal #AWS",
            "dealstage": "appointmentscheduled",
            "closedate": None,
            "description": None,
            "amount": None,
        },
    }


@pytest.fixture
def full_deal():
    """A HubSpot deal with all relevant fields populated."""
    future = (date.today() + timedelta(days=60)).isoformat() + "T00:00:00Z"
    return {
        "id": "42",
        "properties": {
            "dealname": "BigCorp Cloud Migration #AWS",
            "amount": "120000",
            "closedate": future,
            "dealstage": "qualifiedtobuy",
            "description": "Customer needs to migrate 200 on-prem servers to AWS for cost savings.",
            "deal_currency_code": "USD",
            "dealtype": "newbusiness",
            "aws_delivery_models": "SaaS or PaaS,Managed Services",
            "aws_primary_needs": "Co-Sell - Deal Support",
            "aws_use_case": "Migration/Database Migration",
        },
    }


@pytest.fixture
def full_company():
    return {
        "id": "999",
        "properties": {
            "name": "BigCorp Inc",
            "website": "https://bigcorp.example.com",
            "industry": "TECHNOLOGY",
            "country": "US",
            "city": "Seattle",
            "state": "Washington",
            "zip": "98101",
            "address": "123 Main St",
        },
    }


@pytest.fixture
def full_contacts():
    return [
        {
            "id": "c1",
            "properties": {
                "firstname": "Jane",
                "lastname": "Smith",
                "email": "jane.smith@bigcorp.example.com",
                "phone": "+12065550100",
                "jobtitle": "CTO",
            },
        }
    ]


@pytest.fixture
def current_pc_opportunity():
    """Simulates a PC opportunity that has been submitted and approved."""
    return {
        "Id": "O1234567",
        "LifeCycle": {
            "Stage": "Qualified",
            "ReviewStatus": "Approved",
            "TargetCloseDate": (date.today() + timedelta(days=60)).isoformat(),
        },
        "Project": {
            "Title": "BigCorp Cloud Migration #AWS",
            "CustomerBusinessProblem": "Customer needs to migrate servers to AWS.",
        },
    }


# ---------------------------------------------------------------------------
# hubspot_deal_to_partner_central
# ---------------------------------------------------------------------------

class TestHubSpotToPartnerCentral:

    def test_required_fields_present(self, minimal_deal):
        result = hubspot_deal_to_partner_central(minimal_deal)
        assert result["Catalog"] == "AWS"
        assert result["Origin"] == "Partner Referral"
        assert result["ClientToken"].startswith("hs-deal-")
        assert result["Customer"]["Account"]["CompanyName"]
        assert result["Customer"]["Account"]["Industry"] in PC_VALID_INDUSTRIES
        # WebsiteUrl is now optional, not required with a placeholder
        assert result["LifeCycle"]["Stage"] in HUBSPOT_STAGE_TO_PC.values()
        assert result["Project"]["CustomerBusinessProblem"]
        assert len(result["Project"]["CustomerBusinessProblem"]) >= 20
        assert result["Project"]["DeliveryModels"]
        assert all(m in PC_VALID_DELIVERY_MODELS for m in result["Project"]["DeliveryModels"])

    def test_business_problem_minimum_20_chars(self, minimal_deal):
        """CustomerBusinessProblem must be at least 20 chars."""
        result = hubspot_deal_to_partner_central(minimal_deal)
        assert len(result["Project"]["CustomerBusinessProblem"]) >= 20

    def test_business_problem_max_2000_chars(self):
        deal = {
            "id": "1",
            "properties": {
                "dealname": "Test #AWS",
                "description": "x" * 3000,
            },
        }
        result = hubspot_deal_to_partner_central(deal)
        assert len(result["Project"]["CustomerBusinessProblem"]) <= 2000

    def test_spend_frequency_is_monthly(self, minimal_deal):
        """ExpectedCustomerSpend.Frequency must always be 'Monthly'."""
        result = hubspot_deal_to_partner_central(minimal_deal)
        for spend in result["Project"]["ExpectedCustomerSpend"]:
            assert spend["Frequency"] == "Monthly"

    def test_spend_target_company_is_aws(self, minimal_deal):
        """ExpectedCustomerSpend.TargetCompany must always be 'AWS'."""
        result = hubspot_deal_to_partner_central(minimal_deal)
        for spend in result["Project"]["ExpectedCustomerSpend"]:
            assert spend["TargetCompany"] == "AWS"

    def test_spend_currency_code_present(self, minimal_deal):
        result = hubspot_deal_to_partner_central(minimal_deal)
        for spend in result["Project"]["ExpectedCustomerSpend"]:
            assert "CurrencyCode" in spend

    def test_close_date_not_in_past(self):
        """If the close date is in the past, it should be pushed forward."""
        deal = {
            "id": "1",
            "properties": {
                "dealname": "Old Deal #AWS",
                "closedate": "2020-01-01T00:00:00Z",
            },
        }
        result = hubspot_deal_to_partner_central(deal)
        close_date = date.fromisoformat(result["LifeCycle"]["TargetCloseDate"])
        assert close_date > date.today()

    def test_origin_is_partner_referral(self, minimal_deal):
        """Origin must always be 'Partner Referral' for Catalog=AWS."""
        result = hubspot_deal_to_partner_central(minimal_deal)
        assert result["Origin"] == "Partner Referral"

    def test_national_security_no_by_default(self, minimal_deal):
        result = hubspot_deal_to_partner_central(minimal_deal)
        assert result["NationalSecurity"] == "No"

    def test_national_security_yes_for_government(self, minimal_deal):
        full_company = {
            "properties": {"name": "US Gov", "industry": "GOVERNMENT"}
        }
        result = hubspot_deal_to_partner_central(minimal_deal, full_company)
        assert result["NationalSecurity"] == "Yes"

    def test_company_name_from_associated_company(self, full_deal, full_company, full_contacts):
        result = hubspot_deal_to_partner_central(full_deal, full_company, full_contacts)
        assert result["Customer"]["Account"]["CompanyName"] == "BigCorp Inc"

    def test_industry_mapped_from_company(self, minimal_deal, full_company):
        result = hubspot_deal_to_partner_central(minimal_deal, full_company)
        assert result["Customer"]["Account"]["Industry"] == "Software and Internet"

    def test_contacts_mapped(self, full_deal, full_company, full_contacts):
        result = hubspot_deal_to_partner_central(full_deal, full_company, full_contacts)
        contacts = result["Customer"]["Contacts"]
        assert len(contacts) == 1
        assert contacts[0]["Email"] == "jane.smith@bigcorp.example.com"
        assert contacts[0]["FirstName"] == "Jane"

    def test_address_populated_from_company(self, minimal_deal, full_company):
        result = hubspot_deal_to_partner_central(minimal_deal, full_company)
        addr = result["Customer"]["Account"]["Address"]
        assert addr["City"] == "Seattle"
        assert addr["CountryCode"] == "US"

    def test_delivery_models_parsed(self, full_deal):
        result = hubspot_deal_to_partner_central(full_deal)
        assert "SaaS or PaaS" in result["Project"]["DeliveryModels"]
        assert "Managed Services" in result["Project"]["DeliveryModels"]

    def test_invalid_delivery_models_fallback(self):
        deal = {
            "id": "1",
            "properties": {
                "dealname": "X #AWS",
                "aws_delivery_models": "InvalidModel,AlsoInvalid",
            },
        }
        result = hubspot_deal_to_partner_central(deal)
        assert result["Project"]["DeliveryModels"] == ["SaaS or PaaS"]

    def test_client_token_is_deterministic(self, full_deal):
        r1 = hubspot_deal_to_partner_central(full_deal)
        r2 = hubspot_deal_to_partner_central(full_deal)
        assert r1["ClientToken"] == r2["ClientToken"]
        assert r1["ClientToken"] == "hs-deal-42"

    def test_title_present_and_truncated(self):
        long_name = "A" * 300 + " #AWS"
        deal = {"id": "1", "properties": {"dealname": long_name}}
        result = hubspot_deal_to_partner_central(deal)
        assert len(result["Project"]["Title"]) <= 255

    def test_opportunity_type_renewal(self):
        deal = {"id": "1", "properties": {"dealname": "X #AWS", "dealtype": "renewal"}}
        result = hubspot_deal_to_partner_central(deal)
        assert result["OpportunityType"] == "Flat Renewal"

    def test_opportunity_type_expansion(self):
        deal = {"id": "1", "properties": {"dealname": "X #AWS", "dealtype": "expansion"}}
        result = hubspot_deal_to_partner_central(deal)
        assert result["OpportunityType"] == "Expansion"

    def test_website_url_included_when_present(self):
        company = {"properties": {"website": "example.com"}}
        deal = {"id": "1", "properties": {"dealname": "Test #AWS"}}
        result = hubspot_deal_to_partner_central(deal, associated_company=company)
        assert "WebsiteUrl" in result["Customer"]["Account"]
        assert result["Customer"]["Account"]["WebsiteUrl"] == "https://example.com"

    def test_website_url_omitted_when_absent(self):
        deal = {"id": "1", "properties": {"dealname": "Test #AWS"}}
        result = hubspot_deal_to_partner_central(deal)
        assert "WebsiteUrl" not in result["Customer"]["Account"]

    def test_sales_activities_match_stage(self, full_deal):
        result = hubspot_deal_to_partner_central(full_deal)
        # qualifiedtobuy → Qualified → "Customer has shown interest in solution"
        assert "Customer has shown interest in solution" in result["Project"]["SalesActivities"]


# ---------------------------------------------------------------------------
# hubspot_deal_to_partner_central_update — title immutability
# ---------------------------------------------------------------------------

class TestUpdateMapper:

    def test_title_excluded_from_update_payload(self, full_deal, current_pc_opportunity):
        """Project.Title must NEVER appear in an UpdateOpportunity payload."""
        payload, warnings = hubspot_deal_to_partner_central_update(
            full_deal, current_pc_opportunity
        )
        assert payload is not None
        assert "Title" not in payload.get("Project", {})

    def test_title_change_generates_warning(self, current_pc_opportunity):
        deal = {
            "id": "42",
            "properties": {
                "dealname": "New Name That Changed #AWS",
                "dealstage": "qualifiedtobuy",
                "closedate": (date.today() + timedelta(days=60)).isoformat() + "T00:00:00Z",
            },
        }
        payload, warnings = hubspot_deal_to_partner_central_update(
            deal, current_pc_opportunity, changed_properties={"dealname"}
        )
        assert any("title" in w.lower() or "immutable" in w.lower() for w in warnings)

    def test_update_blocked_when_submitted(self, full_deal):
        pc_opp_submitted = {
            "Id": "O999",
            "LifeCycle": {"ReviewStatus": "Submitted"},
            "Project": {"Title": "Test"},
        }
        payload, warnings = hubspot_deal_to_partner_central_update(full_deal, pc_opp_submitted)
        assert payload is None
        assert any("submitted" in w.lower() for w in warnings)

    def test_update_blocked_when_in_review(self, full_deal):
        pc_opp_review = {
            "Id": "O999",
            "LifeCycle": {"ReviewStatus": "In Review"},
            "Project": {"Title": "Test"},
        }
        payload, warnings = hubspot_deal_to_partner_central_update(full_deal, pc_opp_review)
        assert payload is None
        assert any("review" in w.lower() for w in warnings)

    def test_update_allowed_when_approved(self, full_deal, current_pc_opportunity):
        """Updates to non-title fields should succeed when status is Approved."""
        payload, warnings = hubspot_deal_to_partner_central_update(full_deal, current_pc_opportunity)
        assert payload is not None
        assert payload["Identifier"] == "O1234567"

    def test_no_title_warning_for_non_title_change(self, full_deal, current_pc_opportunity):
        """Changing amount (not dealname) should produce no title warning."""
        payload, warnings = hubspot_deal_to_partner_central_update(
            full_deal, current_pc_opportunity, changed_properties={"amount"}
        )
        title_warnings = [w for w in warnings if "title" in w.lower()]
        assert not title_warnings


# ---------------------------------------------------------------------------
# partner_central_opportunity_to_hubspot
# ---------------------------------------------------------------------------

class TestPartnerCentralToHubSpot:

    def test_basic_mapping(self):
        pc_opp = {
            "Id": "O001",
            "Arn": "arn:aws:pc:::O001",
            "Project": {
                "Title": "AWS Cloud Project",
                "CustomerBusinessProblem": "Customer needs cloud infrastructure",
                "ExpectedCustomerSpend": [{"Amount": "5000", "CurrencyCode": "USD"}],
            },
            "LifeCycle": {
                "Stage": "Qualified",
                "ReviewStatus": "Approved",
                "TargetCloseDate": (date.today() + timedelta(days=60)).isoformat(),
            },
            "Customer": {"Account": {"CompanyName": "Acme Corp"}},
        }
        result = partner_central_opportunity_to_hubspot(pc_opp)
        assert "#AWS" in result["dealname"]
        assert result["dealstage"] == "qualifiedtobuy"
        assert result["aws_opportunity_id"] == "O001"
        assert result["amount"] == "5000"
        assert result["aws_review_status"] == "Approved"
        # Canonical title stored separately
        assert result["aws_opportunity_title"] == "AWS Cloud Project"

    def test_aws_tag_not_duplicated(self):
        pc_opp = {
            "Id": "O002",
            "Arn": "arn:",
            "Project": {"Title": "Already Tagged #AWS"},
            "LifeCycle": {
                "Stage": "Prospect",
                "TargetCloseDate": (date.today() + timedelta(days=90)).isoformat(),
            },
            "Customer": {"Account": {}},
        }
        result = partner_central_opportunity_to_hubspot(pc_opp)
        assert result["dealname"].count("#AWS") == 1

    def test_invitation_id_stored(self):
        pc_opp = {
            "Id": "O003", "Arn": "arn:",
            "Project": {"Title": "Test"},
            "LifeCycle": {"Stage": "Prospect",
                          "TargetCloseDate": (date.today() + timedelta(days=90)).isoformat()},
            "Customer": {"Account": {}},
        }
        result = partner_central_opportunity_to_hubspot(pc_opp, invitation_id="inv-abc")
        assert result["aws_invitation_id"] == "inv-abc"

    def test_stage_roundtrip(self):
        for pc_stage, hs_stage in PC_STAGE_TO_HUBSPOT.items():
            assert hs_stage in HUBSPOT_STAGE_TO_PC


# ---------------------------------------------------------------------------
# Individual helper tests
# ---------------------------------------------------------------------------

class TestHelpers:

    def test_sanitize_business_problem_too_short(self):
        result = _sanitize_business_problem("Short")
        assert len(result) >= 20

    def test_sanitize_business_problem_empty(self):
        result = _sanitize_business_problem(None, deal_name="Test Deal #AWS")
        assert len(result) >= 20
        assert "Test Deal #AWS" in result

    def test_sanitize_business_problem_truncates_at_2000(self):
        result = _sanitize_business_problem("x" * 3000)
        assert len(result) == 2000

    def test_sanitize_website_adds_https(self):
        assert _sanitize_website("example.com").startswith("https://")

    def test_sanitize_website_fallback_on_none(self):
        result = _sanitize_website(None)
        # Now returns None instead of placeholder
        assert result is None

    def test_sanitize_website_truncates(self):
        result = _sanitize_website("https://" + "a" * 260 + ".com")
        assert len(result) <= 255

    def test_map_industry_hubspot_enum(self):
        assert _map_industry("COMPUTER_SOFTWARE") == "Software and Internet"
        assert _map_industry("HEALTHCARE") == "Healthcare"
        assert _map_industry("GOVERNMENT") == "Government"
        assert _map_industry("GAMING") == "Gaming"

    def test_map_industry_direct_pc_value(self):
        assert _map_industry("Software and Internet") == "Software and Internet"

    def test_map_industry_unknown_falls_back_to_other(self):
        assert _map_industry("TOTALLY_UNKNOWN_INDUSTRY") == "Other"

    def test_map_industry_none_falls_back_to_other(self):
        assert _map_industry(None) == "Other"

    def test_safe_close_date_past_returns_future(self):
        result = _safe_close_date("2020-01-01T00:00:00Z")
        assert date.fromisoformat(result) > date.today()

    def test_safe_close_date_future_preserved(self):
        future = (date.today() + timedelta(days=30)).isoformat()
        result = _safe_close_date(future + "T00:00:00Z")
        assert result == future

    def test_safe_close_date_none_returns_default(self):
        result = _safe_close_date(None)
        assert date.fromisoformat(result) > date.today()

    def test_build_spend_always_monthly(self):
        spend = _build_spend({"amount": "5000"})
        assert spend[0]["Frequency"] == "Monthly"

    def test_build_spend_target_company_aws(self):
        spend = _build_spend({"amount": "5000"})
        assert spend[0]["TargetCompany"] == "AWS"

    def test_build_spend_zero_when_no_amount(self):
        spend = _build_spend({})
        assert spend[0]["Amount"] == "0.00"
