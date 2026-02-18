"""
Tests for custom exception classes.
"""

import pytest
from common.exceptions import (
    SyncException,
    HubSpotAPIException,
    PartnerCentralException,
    ValidationException,
    ConflictException,
    ReviewStatusException,
)


def test_sync_exception_basic():
    """Test basic SyncException functionality"""
    exc = SyncException("Test error")
    assert str(exc) == "Test error"
    assert exc.details == {}


def test_sync_exception_with_details():
    """Test SyncException with details dict"""
    details = {"field": "dealname", "value": "Test Deal"}
    exc = SyncException("Test error", details=details)
    assert str(exc) == "Test error"
    assert exc.details == details


def test_hubspot_api_exception():
    """Test HubSpotAPIException is a SyncException"""
    exc = HubSpotAPIException("API call failed")
    assert isinstance(exc, SyncException)
    assert str(exc) == "API call failed"


def test_partner_central_exception():
    """Test PartnerCentralException is a SyncException"""
    exc = PartnerCentralException("Partner Central error")
    assert isinstance(exc, SyncException)
    assert str(exc) == "Partner Central error"


def test_validation_exception():
    """Test ValidationException is a SyncException"""
    exc = ValidationException("Validation failed")
    assert isinstance(exc, SyncException)
    assert str(exc) == "Validation failed"


def test_conflict_exception():
    """Test ConflictException with field-level conflict info"""
    exc = ConflictException(
        message="Conflict detected",
        field_name="dealstage",
        local_value="closedwon",
        remote_value="qualified",
    )
    assert isinstance(exc, SyncException)
    assert str(exc) == "Conflict detected"
    assert exc.field_name == "dealstage"
    assert exc.local_value == "closedwon"
    assert exc.remote_value == "qualified"


def test_review_status_exception():
    """Test ReviewStatusException is a SyncException"""
    exc = ReviewStatusException("Cannot update - under review")
    assert isinstance(exc, SyncException)
    assert str(exc) == "Cannot update - under review"
