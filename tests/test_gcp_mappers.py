"""
Tests for the HubSpot ↔ GCP Partners field mappers.
Validates all field mappings, stage conversions, and business constraints.
"""

import pytest
from datetime import date, timedelta
from unittest.mock import patch

from common.gcp_mappers import (
    hubspot_deal_to_gcp_lead,
    hubspot_deal_to_gcp_opportunity,
    gcp_opportunity_to_hubspot_deal,
    hubspot_deal_to_gcp_opportunity_update,
    HUBSPOT_STAGE_TO_GCP,
    GCP_STAGE_TO_HUBSPOT,
    GCP_PRODUCT_FAMILIES,
    DEFAULT_PRODUCT_FAMILY,
    _sanitize_website,
    _sanitize_phone,
    _parse_close_date,
    _gcp_date_to_hubspot_iso,
    _map_product_family,
    _map_qualification_state,
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
            "dealname": "Minimal Deal #GCP",
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
            "dealname": "BigCorp Cloud Migration #GCP",
            "amount": "150000",
            "closedate": future,
            "dealstage": "qualifiedtobuy",
            "description": "Customer needs to migrate infrastructure to Google Cloud Platform for scalability and cost optimization.",
            "dealtype": "newbusiness",
            "gcp_product_family": "GOOGLE_CLOUD_PLATFORM",
            "gcp_term_months": "12",
            "hs_next_step": "Schedule technical discovery call",
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
            "city": "San Francisco",
            "state": "California",
            "zip": "94102",
            "address": "123 Market St",
        },
    }


@pytest.fixture
def full_contacts():
    return [
        {
            "id": "c1",
            "properties": {
                "firstname": "John",
                "lastname": "Doe",
                "email": "john.doe@bigcorp.example.com",
                "phone": "+14155551234",
                "jobtitle": "VP of Engineering",
            },
        }
    ]


@pytest.fixture
def gcp_opportunity():
    """Sample GCP opportunity response."""
    return {
        "name": "partners/12345/opportunities/67890",
        "lead": "partners/12345/leads/11111",
        "salesStage": "QUALIFIED",
        "qualificationState": "QUALIFIED",
        "dealSize": 150000.0,
        "closeDate": {
            "year": 2026,
            "month": 4,
            "day": 15,
        },
        "productFamily": "GOOGLE_CLOUD_PLATFORM",
        "notes": "Cloud migration project",
        "nextSteps": "Technical validation",
        "externalSystemId": "hubspot-deal-42",
    }


@pytest.fixture
def gcp_lead():
    """Sample GCP lead response."""
    return {
        "name": "partners/12345/leads/11111",
        "companyName": "BigCorp Inc",
        "companyWebsite": "https://bigcorp.example.com",
        "contact": {
            "givenName": "John",
            "familyName": "Doe",
            "email": "john.doe@bigcorp.example.com",
            "phone": "+14155551234",
        },
        "notes": "Enterprise customer interested in GCP",
    }


# ---------------------------------------------------------------------------
# Tests: HubSpot Deal → GCP Lead
# ---------------------------------------------------------------------------

def test_hubspot_deal_to_gcp_lead_minimal(minimal_deal):
    """Test lead creation with minimal deal data."""
    lead = hubspot_deal_to_gcp_lead(minimal_deal)
    
    assert lead["companyName"] == "Unknown Customer"
    assert lead["externalSystemId"] == "hubspot-deal-100"
    assert lead["notes"] == "HubSpot deal: Minimal Deal #GCP"
    assert "companyWebsite" not in lead
    assert "contact" not in lead


def test_hubspot_deal_to_gcp_lead_full(full_deal, full_company, full_contacts):
    """Test lead creation with full deal data."""
    lead = hubspot_deal_to_gcp_lead(full_deal, full_company, full_contacts)
    
    assert lead["companyName"] == "BigCorp Inc"
    assert lead["companyWebsite"] == "https://bigcorp.example.com"
    assert lead["externalSystemId"] == "hubspot-deal-42"
    assert "Customer needs to migrate" in lead["notes"]
    
    # Check contact mapping
    assert "contact" in lead
    contact = lead["contact"]
    assert contact["givenName"] == "John"
    assert contact["familyName"] == "Doe"
    assert contact["email"] == "john.doe@bigcorp.example.com"
    assert contact["phone"] == "+14155551234"


def test_hubspot_deal_to_gcp_lead_website_sanitization(minimal_deal, full_company):
    """Test website URL sanitization."""
    # Test with URL missing protocol
    company = {
        "id": "999",
        "properties": {
            "name": "Test Corp",
            "website": "testcorp.com",
        },
    }
    lead = hubspot_deal_to_gcp_lead(minimal_deal, company)
    assert lead["companyWebsite"] == "https://testcorp.com"


# ---------------------------------------------------------------------------
# Tests: HubSpot Deal → GCP Opportunity
# ---------------------------------------------------------------------------

def test_hubspot_deal_to_gcp_opportunity_minimal(minimal_deal):
    """Test opportunity creation with minimal data."""
    lead_name = "partners/12345/leads/99999"
    opp = hubspot_deal_to_gcp_opportunity(minimal_deal, lead_name)
    
    assert opp["lead"] == lead_name
    assert opp["salesStage"] == "QUALIFYING"
    assert opp["qualificationState"] == "UNQUALIFIED"
    assert opp["productFamily"] == DEFAULT_PRODUCT_FAMILY
    assert opp["externalSystemId"] == "hubspot-deal-100"
    
    # Close date should be auto-generated (future date)
    assert "closeDate" in opp
    assert opp["closeDate"]["year"] >= date.today().year


def test_hubspot_deal_to_gcp_opportunity_full(full_deal, full_company, full_contacts):
    """Test opportunity creation with full data."""
    lead_name = "partners/12345/leads/99999"
    opp = hubspot_deal_to_gcp_opportunity(full_deal, lead_name, full_company, full_contacts)
    
    assert opp["lead"] == lead_name
    assert opp["salesStage"] == "QUALIFIED"
    assert opp["qualificationState"] == "QUALIFIED"
    assert opp["dealSize"] == 150000.0
    assert opp["productFamily"] == "GOOGLE_CLOUD_PLATFORM"
    assert opp["termMonths"] == "12"
    assert "Customer needs to migrate" in opp["notes"]
    assert opp["nextSteps"] == "Schedule technical discovery call"


def test_hubspot_deal_to_gcp_opportunity_stage_mapping(minimal_deal):
    """Test all stage mappings."""
    lead_name = "partners/12345/leads/99999"
    
    test_cases = [
        ("appointmentscheduled", "QUALIFYING"),
        ("qualifiedtobuy", "QUALIFIED"),
        ("presentationscheduled", "QUALIFIED"),
        ("decisionmakerboughtin", "PROPOSAL"),
        ("contractsent", "NEGOTIATING"),
        ("closedwon", "CLOSED_WON"),
        ("closedlost", "CLOSED_LOST"),
    ]
    
    for hs_stage, gcp_stage in test_cases:
        deal = minimal_deal.copy()
        deal["properties"]["dealstage"] = hs_stage
        opp = hubspot_deal_to_gcp_opportunity(deal, lead_name)
        assert opp["salesStage"] == gcp_stage, f"Failed for {hs_stage}"


def test_hubspot_deal_to_gcp_opportunity_product_family_mapping():
    """Test product family mapping."""
    deal = {
        "id": "100",
        "properties": {
            "dealname": "Test Deal #GCP",
            "dealstage": "qualifiedtobuy",
        },
    }
    lead_name = "partners/12345/leads/99999"
    
    # Test Workspace mapping
    deal["properties"]["gcp_product_family"] = "workspace"
    opp = hubspot_deal_to_gcp_opportunity(deal, lead_name)
    assert opp["productFamily"] == "GOOGLE_WORKSPACE"
    
    # Test Chrome mapping
    deal["properties"]["gcp_product_family"] = "chrome enterprise"
    opp = hubspot_deal_to_gcp_opportunity(deal, lead_name)
    assert opp["productFamily"] == "CHROME_ENTERPRISE"
    
    # Test Maps mapping
    deal["properties"]["gcp_product_family"] = "google maps"
    opp = hubspot_deal_to_gcp_opportunity(deal, lead_name)
    assert opp["productFamily"] == "GOOGLE_MAPS_PLATFORM"
    
    # Test default
    deal["properties"]["gcp_product_family"] = None
    opp = hubspot_deal_to_gcp_opportunity(deal, lead_name)
    assert opp["productFamily"] == "GOOGLE_CLOUD_PLATFORM"


# ---------------------------------------------------------------------------
# Tests: GCP Opportunity → HubSpot Deal
# ---------------------------------------------------------------------------

def test_gcp_opportunity_to_hubspot_deal(gcp_opportunity, gcp_lead):
    """Test mapping GCP opportunity back to HubSpot deal."""
    deal_props = gcp_opportunity_to_hubspot_deal(gcp_opportunity, gcp_lead)
    
    assert "#GCP" in deal_props["dealname"]
    assert deal_props["dealstage"] == "qualifiedtobuy"
    assert deal_props["gcp_opportunity_id"] == "67890"
    assert deal_props["gcp_opportunity_name"] == "partners/12345/opportunities/67890"
    assert deal_props["amount"] == "150000.0"
    assert deal_props["description"] == "Cloud migration project"
    assert deal_props["company"] == "BigCorp Inc"
    assert deal_props["gcp_product_family"] == "GOOGLE_CLOUD_PLATFORM"
    assert deal_props["gcp_sync_status"] == "synced"


def test_gcp_opportunity_to_hubspot_deal_stage_mapping(gcp_opportunity):
    """Test all GCP stage to HubSpot stage mappings."""
    test_cases = [
        ("QUALIFYING", "appointmentscheduled"),
        ("QUALIFIED", "qualifiedtobuy"),
        ("PROPOSAL", "decisionmakerboughtin"),
        ("NEGOTIATING", "contractsent"),
        ("CLOSED_WON", "closedwon"),
        ("CLOSED_LOST", "closedlost"),
    ]
    
    for gcp_stage, hs_stage in test_cases:
        opp = gcp_opportunity.copy()
        opp["salesStage"] = gcp_stage
        deal_props = gcp_opportunity_to_hubspot_deal(opp)
        assert deal_props["dealstage"] == hs_stage, f"Failed for {gcp_stage}"


def test_gcp_opportunity_to_hubspot_deal_adds_gcp_tag():
    """Test that #GCP tag is added if missing."""
    opp = {
        "name": "partners/12345/opportunities/67890",
        "salesStage": "QUALIFIED",
        "dealSize": 10000.0,
    }
    
    lead = {
        "companyName": "Test Corp",
    }
    
    deal_props = gcp_opportunity_to_hubspot_deal(opp, lead)
    assert "#GCP" in deal_props["dealname"]


# ---------------------------------------------------------------------------
# Tests: HubSpot Deal Update → GCP Opportunity Update
# ---------------------------------------------------------------------------

def test_hubspot_deal_to_gcp_opportunity_update_stage(full_deal, gcp_opportunity):
    """Test stage update mapping."""
    full_deal["properties"]["dealstage"] = "contractsent"
    
    update_payload, warnings = hubspot_deal_to_gcp_opportunity_update(
        full_deal, gcp_opportunity, changed_properties={"dealstage"}
    )
    
    assert update_payload is not None
    assert update_payload["salesStage"] == "NEGOTIATING"
    assert update_payload["qualificationState"] == "QUALIFIED"
    assert len(warnings) == 0


def test_hubspot_deal_to_gcp_opportunity_update_amount(full_deal, gcp_opportunity):
    """Test amount update mapping."""
    full_deal["properties"]["amount"] = "200000"
    
    update_payload, warnings = hubspot_deal_to_gcp_opportunity_update(
        full_deal, gcp_opportunity, changed_properties={"amount"}
    )
    
    assert update_payload is not None
    assert update_payload["dealSize"] == 200000.0


def test_hubspot_deal_to_gcp_opportunity_update_description(full_deal, gcp_opportunity):
    """Test description/notes update mapping."""
    full_deal["properties"]["description"] = "Updated project description"
    
    update_payload, warnings = hubspot_deal_to_gcp_opportunity_update(
        full_deal, gcp_opportunity, changed_properties={"description"}
    )
    
    assert update_payload is not None
    assert update_payload["notes"] == "Updated project description"


# ---------------------------------------------------------------------------
# Tests: Helper Functions
# ---------------------------------------------------------------------------

def test_sanitize_website():
    """Test website URL sanitization."""
    assert _sanitize_website("example.com") == "https://example.com"
    assert _sanitize_website("https://example.com") == "https://example.com"
    assert _sanitize_website("http://example.com") == "http://example.com"
    assert _sanitize_website(None) is None
    assert _sanitize_website("") is None


def test_sanitize_phone():
    """Test phone number sanitization."""
    assert _sanitize_phone("+14155551234") == "+14155551234"
    assert _sanitize_phone("4155551234") == "+14155551234"
    assert _sanitize_phone("(415) 555-1234") == "+14155551234"
    assert _sanitize_phone(None) is None
    assert _sanitize_phone("") is None
    assert _sanitize_phone("12") is None  # Too short


def test_parse_close_date():
    """Test close date parsing."""
    # Future date
    future = (date.today() + timedelta(days=60)).isoformat() + "T00:00:00Z"
    parsed = _parse_close_date(future)
    assert parsed is not None
    assert "year" in parsed
    assert "month" in parsed
    assert "day" in parsed
    
    # Past date should be pushed to future
    past = (date.today() - timedelta(days=30)).isoformat() + "T00:00:00Z"
    parsed = _parse_close_date(past)
    assert parsed is not None
    # Should be at least tomorrow
    parsed_date = date(parsed["year"], parsed["month"], parsed["day"])
    assert parsed_date > date.today()
    
    # None should return default (90 days)
    parsed = _parse_close_date(None)
    assert parsed is not None


def test_gcp_date_to_hubspot_iso():
    """Test GCP date format to HubSpot ISO conversion."""
    gcp_date = {"year": 2026, "month": 4, "day": 15}
    iso = _gcp_date_to_hubspot_iso(gcp_date)
    assert iso == "2026-04-15T00:00:00Z"
    
    # Invalid date
    assert _gcp_date_to_hubspot_iso(None) is None
    assert _gcp_date_to_hubspot_iso({}) is None


def test_map_product_family():
    """Test product family mapping."""
    assert _map_product_family("workspace") == "GOOGLE_WORKSPACE"
    assert _map_product_family("chrome") == "CHROME_ENTERPRISE"
    assert _map_product_family("maps") == "GOOGLE_MAPS_PLATFORM"
    assert _map_product_family("apigee") == "APIGEE"
    assert _map_product_family(None) == "GOOGLE_CLOUD_PLATFORM"
    assert _map_product_family("unknown") == "GOOGLE_CLOUD_PLATFORM"


def test_map_qualification_state():
    """Test qualification state mapping."""
    assert _map_qualification_state("QUALIFYING") == "UNQUALIFIED"
    assert _map_qualification_state("QUALIFIED") == "QUALIFIED"
    assert _map_qualification_state("PROPOSAL") == "QUALIFIED"
    assert _map_qualification_state("NEGOTIATING") == "QUALIFIED"
    assert _map_qualification_state("CLOSED_WON") == "QUALIFIED"
    assert _map_qualification_state("CLOSED_LOST") == "DISQUALIFIED"


# ---------------------------------------------------------------------------
# Tests: Stage Mapping Coverage
# ---------------------------------------------------------------------------

def test_stage_mappings_are_bijective():
    """Ensure stage mappings are reversible (where applicable)."""
    # Not all mappings are perfectly bijective due to multiple HubSpot stages
    # mapping to single GCP stages, but we can test the reverse mapping exists
    for hs_stage, gcp_stage in HUBSPOT_STAGE_TO_GCP.items():
        assert gcp_stage in GCP_STAGE_TO_HUBSPOT, f"GCP stage {gcp_stage} not in reverse mapping"
    
    for gcp_stage, hs_stage in GCP_STAGE_TO_HUBSPOT.items():
        assert hs_stage in HUBSPOT_STAGE_TO_GCP, f"HubSpot stage {hs_stage} not in forward mapping"


def test_product_family_enum_values():
    """Test that product family enum values are valid."""
    assert "GOOGLE_CLOUD_PLATFORM" in GCP_PRODUCT_FAMILIES
    assert "GOOGLE_WORKSPACE" in GCP_PRODUCT_FAMILIES
    assert "CHROME_ENTERPRISE" in GCP_PRODUCT_FAMILIES
    assert "GOOGLE_MAPS_PLATFORM" in GCP_PRODUCT_FAMILIES
    assert "APIGEE" in GCP_PRODUCT_FAMILIES
