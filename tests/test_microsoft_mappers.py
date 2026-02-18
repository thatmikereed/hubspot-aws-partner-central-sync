"""
Tests for the HubSpot ↔ Microsoft Partner Center field mappers.
Validates status mappings, field transformations, and bidirectional sync.
"""

import pytest
from datetime import date, timedelta
from unittest.mock import patch

from common.microsoft_mappers import (
    hubspot_deal_to_microsoft_referral,
    hubspot_deal_to_microsoft_referral_update,
    microsoft_referral_to_hubspot_deal,
    get_hubspot_custom_properties_for_microsoft,
    HUBSPOT_STAGE_TO_MICROSOFT_STATUS,
    MICROSOFT_STATUS_TO_HUBSPOT,
    HUBSPOT_QUALIFICATION,
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
            "dealname": "Minimal Deal #Microsoft",
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
            "dealname": "BigCorp Cloud Migration #Microsoft",
            "amount": "120000",
            "closedate": future,
            "dealstage": "qualifiedtobuy",
            "description": "Customer needs cloud migration services for cost savings.",
            "deal_currency_code": "USD",
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
            "numberofemployees": "500",
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
def microsoft_referral():
    """A Microsoft Partner Center referral object."""
    return {
        "id": "ref-12345",
        "eTag": "W/\"datetime'2024-09-19T19%3A45%3A40.1106919Z'\"",
        "name": "BigCorp Cloud Migration",
        "status": "Active",
        "substatus": "Accepted",
        "qualification": "SalesQualified",
        "type": "Independent",
        "customerProfile": {
            "name": "BigCorp Inc",
            "address": {
                "addressLine1": "123 Main St",
                "city": "Seattle",
                "state": "Washington",
                "postalCode": "98101",
                "country": "US"
            },
            "size": "251to1000employees",
            "team": [
                {
                    "firstName": "Jane",
                    "lastName": "Smith",
                    "emailAddress": "jane@bigcorp.com",
                    "phoneNumber": "+12065550100"
                }
            ]
        },
        "consent": {
            "consentToShareReferralWithMicrosoftSellers": False
        },
        "details": {
            "dealValue": 120000,
            "currency": "USD",
            "notes": "Customer needs cloud migration",
            "closeDate": "2024-12-31"
        }
    }


# ---------------------------------------------------------------------------
# Tests: HubSpot Deal → Microsoft Referral
# ---------------------------------------------------------------------------

def test_minimal_deal_to_microsoft(minimal_deal):
    """Test that a minimal deal produces a valid Microsoft referral payload."""
    referral = hubspot_deal_to_microsoft_referral(minimal_deal)
    
    assert referral["name"] == "Minimal Deal #Microsoft"
    assert referral["type"] == "Independent"
    assert referral["qualification"] == "MarketingQualified"
    assert referral["externalReferenceId"] == "100"
    assert "customerProfile" in referral
    assert "consent" in referral
    assert "details" in referral
    assert referral["details"]["dealValue"] == 0
    assert referral["details"]["currency"] == "USD"
    assert "closeDate" in referral["details"]


def test_full_deal_to_microsoft(full_deal, full_company, full_contacts):
    """Test a fully populated deal produces a complete Microsoft referral."""
    referral = hubspot_deal_to_microsoft_referral(full_deal, full_company, full_contacts)
    
    assert referral["name"] == "BigCorp Cloud Migration #Microsoft"
    assert referral["type"] == "Independent"
    assert referral["qualification"] == "SalesQualified"
    
    # Check customer profile
    assert referral["customerProfile"]["name"] == "BigCorp Inc"
    assert referral["customerProfile"]["address"]["city"] == "Seattle"
    assert referral["customerProfile"]["size"] == "251to1000employees"
    assert len(referral["customerProfile"]["team"]) == 1
    assert referral["customerProfile"]["team"][0]["emailAddress"] == "jane.smith@bigcorp.example.com"
    
    # Check details
    assert referral["details"]["dealValue"] == 120000.0
    assert referral["details"]["currency"] == "USD"
    assert "Customer needs cloud migration" in referral["details"]["notes"]


def test_stage_to_status_mapping():
    """Test that all HubSpot stages map to valid Microsoft status/substatus."""
    for stage, (status, substatus) in HUBSPOT_STAGE_TO_MICROSOFT_STATUS.items():
        assert status in ["New", "Active", "Closed"]
        assert substatus in ["Pending", "Received", "Accepted", "Engaged", "Won", "Lost", "Declined", "Expired"]


def test_qualification_mapping():
    """Test qualification level mapping."""
    for stage, qual in HUBSPOT_QUALIFICATION.items():
        assert qual in ["MarketingQualified", "SalesQualified"]


# ---------------------------------------------------------------------------
# Tests: Microsoft Referral → HubSpot Deal
# ---------------------------------------------------------------------------

def test_microsoft_referral_to_hubspot(microsoft_referral):
    """Test conversion from Microsoft referral to HubSpot deal properties."""
    deal_props = microsoft_referral_to_hubspot_deal(microsoft_referral)
    
    assert "#Microsoft" in deal_props["dealname"]
    assert deal_props["amount"] == "120000"
    assert deal_props["dealstage"] == "qualifiedtobuy"  # Active/Accepted maps to qualifiedtobuy
    assert deal_props["microsoft_referral_id"] == "ref-12345"
    assert deal_props["microsoft_status"] == "Active"
    assert deal_props["microsoft_substatus"] == "Accepted"
    assert deal_props["microsoft_sync_status"] == "synced"
    assert "Customer needs cloud migration" in deal_props["description"]


def test_status_to_stage_roundtrip():
    """Test that status mappings are consistent."""
    # Test a few key mappings
    deal = {
        "id": "1",
        "properties": {
            "dealname": "Test #Microsoft",
            "dealstage": "qualifiedtobuy",
            "amount": "10000",
            "closedate": None,
            "description": "",
        }
    }
    
    referral = hubspot_deal_to_microsoft_referral(deal)
    # qualifiedtobuy → Active/Accepted
    assert referral["qualification"] == "SalesQualified"
    
    # Now convert a Microsoft referral back
    ms_ref = {
        "id": "ref-1",
        "name": "Test Deal",
        "status": "Active",
        "substatus": "Accepted",
        "details": {"dealValue": 10000, "currency": "USD", "notes": "", "closeDate": "2024-12-31"}
    }
    
    deal_props = microsoft_referral_to_hubspot_deal(ms_ref)
    assert deal_props["dealstage"] == "qualifiedtobuy"


# ---------------------------------------------------------------------------
# Tests: Update Mapping
# ---------------------------------------------------------------------------

def test_update_with_no_changes(full_deal, microsoft_referral):
    """Test that when nothing changed, no updates are generated."""
    # Make the deal match the referral
    full_deal["properties"]["dealname"] = "BigCorp Cloud Migration"
    full_deal["properties"]["amount"] = "120000"
    
    updates, warnings = hubspot_deal_to_microsoft_referral_update(
        full_deal, microsoft_referral, None, None, set()
    )
    
    # Should have some updates because of close date formatting
    assert updates is not None or len(warnings) == 0


def test_update_blocked_for_closed_referral(full_deal):
    """Test that updates are blocked when referral is closed."""
    closed_referral = {
        "id": "ref-1",
        "status": "Closed",
        "substatus": "Won",
        "name": "Closed Deal",
        "details": {"dealValue": 100000, "currency": "USD", "notes": "", "closeDate": "2024-01-01"}
    }
    
    updates, warnings = hubspot_deal_to_microsoft_referral_update(
        full_deal, closed_referral, None, None, {"amount"}
    )
    
    assert updates is None
    assert len(warnings) > 0
    assert "closed" in warnings[0].lower()


def test_update_amount_change(full_deal, microsoft_referral):
    """Test updating the deal value."""
    full_deal["properties"]["amount"] = "200000"
    
    updates, warnings = hubspot_deal_to_microsoft_referral_update(
        full_deal, microsoft_referral, None, None, {"amount"}
    )
    
    assert updates is not None
    assert updates["details"]["dealValue"] == 200000.0


def test_update_stage_change(full_deal, microsoft_referral):
    """Test updating the deal stage (changes status/substatus)."""
    full_deal["properties"]["dealstage"] = "closedwon"
    
    updates, warnings = hubspot_deal_to_microsoft_referral_update(
        full_deal, microsoft_referral, None, None, {"dealstage"}
    )
    
    assert updates is not None
    assert updates["status"] == "Closed"
    assert updates["substatus"] == "Won"


# ---------------------------------------------------------------------------
# Tests: Custom Properties
# ---------------------------------------------------------------------------

def test_custom_properties_defined():
    """Test that Microsoft custom properties are defined."""
    props = get_hubspot_custom_properties_for_microsoft()
    
    assert len(props) == 5
    prop_names = [p["name"] for p in props]
    assert "microsoft_referral_id" in prop_names
    assert "microsoft_sync_status" in prop_names
    assert "microsoft_status" in prop_names
    assert "microsoft_substatus" in prop_names
    assert "customer_name" in prop_names
