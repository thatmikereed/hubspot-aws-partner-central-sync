"""
Tests for the Microsoft Partner Center client.
Uses responses library to mock HTTP requests to the Microsoft API.
"""

import pytest
import responses
from unittest.mock import patch, MagicMock

from common.microsoft_client import (
    MicrosoftPartnerCenterClient,
    get_microsoft_client,
    PARTNER_CENTER_API_BASE,
)


@pytest.fixture
def mock_access_token():
    return "test-access-token-12345"


@pytest.fixture
def microsoft_client(mock_access_token):
    """Create a Microsoft client with a mock token."""
    return MicrosoftPartnerCenterClient(access_token=mock_access_token)


@pytest.fixture
def sample_referral():
    """A sample Microsoft referral payload."""
    return {
        "name": "Test Referral",
        "type": "Independent",
        "qualification": "SalesQualified",
        "externalReferenceId": "ext-123",
        "customerProfile": {
            "name": "Test Customer Inc",
            "address": {
                "addressLine1": "123 Test St",
                "city": "Seattle",
                "state": "WA",
                "postalCode": "98101",
                "country": "US"
            },
            "size": "10to50employees",
            "team": [
                {
                    "firstName": "John",
                    "lastName": "Doe",
                    "emailAddress": "john@test.com",
                    "phoneNumber": "5551234567"
                }
            ]
        },
        "consent": {
            "consentToShareReferralWithMicrosoftSellers": False
        },
        "details": {
            "dealValue": 50000,
            "currency": "USD",
            "notes": "Test deal",
            "closeDate": "2024-12-31"
        }
    }


@pytest.fixture
def sample_referral_response():
    """A sample referral response from Microsoft API."""
    return {
        "id": "ref-12345",
        "eTag": "W/\"datetime'2024-09-19T19%3A45%3A40.1106919Z'\"",
        "name": "Test Referral",
        "status": "New",
        "substatus": "Pending",
        "type": "Independent",
        "qualification": "SalesQualified",
        "externalReferenceId": "ext-123",
        "createdDateTime": "2024-09-19T19:45:40.1106919Z",
        "updatedDateTime": "2024-09-19T19:45:40.1106919Z",
        "customerProfile": {
            "name": "Test Customer Inc",
            "address": {
                "addressLine1": "123 Test St",
                "city": "Seattle",
                "state": "WA",
                "postalCode": "98101",
                "country": "US"
            },
            "size": "10to50employees",
            "team": []
        },
        "consent": {
            "consentToShareReferralWithMicrosoftSellers": False
        },
        "details": {
            "dealValue": 50000,
            "currency": "USD",
            "notes": "Test deal",
            "closeDate": "2024-12-31"
        }
    }


# ---------------------------------------------------------------------------
# Tests: Client Initialization
# ---------------------------------------------------------------------------

def test_client_initialization_with_token(mock_access_token):
    """Test client initialization with explicit token."""
    client = MicrosoftPartnerCenterClient(access_token=mock_access_token)
    
    assert client.access_token == mock_access_token
    assert client.base_url == PARTNER_CENTER_API_BASE
    assert "Authorization" in client.session.headers
    assert client.session.headers["Authorization"] == f"Bearer {mock_access_token}"


def test_client_initialization_without_token():
    """Test that client raises error when token is missing."""
    with patch.dict("os.environ", {}, clear=True):
        with pytest.raises(ValueError, match="Microsoft access token is required"):
            MicrosoftPartnerCenterClient()


def test_get_microsoft_client():
    """Test the factory function."""
    with patch.dict("os.environ", {"MICROSOFT_ACCESS_TOKEN": "env-token"}):
        client = get_microsoft_client()
        assert client.access_token == "env-token"


# ---------------------------------------------------------------------------
# Tests: Create Referral
# ---------------------------------------------------------------------------

@responses.activate
def test_create_referral_success(microsoft_client, sample_referral, sample_referral_response):
    """Test successful referral creation."""
    url = f"{PARTNER_CENTER_API_BASE}/engagements/referrals"
    
    responses.add(
        responses.POST,
        url,
        json=sample_referral_response,
        status=201
    )
    
    result = microsoft_client.create_referral(sample_referral)
    
    assert result["id"] == "ref-12345"
    assert result["status"] == "New"
    assert result["name"] == "Test Referral"
    assert "eTag" in result
    
    # Verify request
    assert len(responses.calls) == 1
    assert responses.calls[0].request.url == url


@responses.activate
def test_create_referral_api_error(microsoft_client, sample_referral):
    """Test referral creation with API error."""
    url = f"{PARTNER_CENTER_API_BASE}/engagements/referrals"
    
    responses.add(
        responses.POST,
        url,
        json={"error": {"code": "BadRequest", "message": "Invalid request"}},
        status=400
    )
    
    with pytest.raises(Exception):  # requests.HTTPError
        microsoft_client.create_referral(sample_referral)


# ---------------------------------------------------------------------------
# Tests: Update Referral
# ---------------------------------------------------------------------------

@responses.activate
def test_update_referral_success(microsoft_client, sample_referral_response):
    """Test successful referral update."""
    referral_id = "ref-12345"
    url = f"{PARTNER_CENTER_API_BASE}/engagements/referrals/{referral_id}"
    
    updates = {
        "details": {
            "dealValue": 75000,
            "notes": "Updated deal value"
        }
    }
    
    updated_response = {**sample_referral_response, "details": updates["details"]}
    updated_response["eTag"] = "W/\"datetime'2024-09-20T10%3A00%3A00.0000000Z'\""
    
    responses.add(
        responses.PATCH,
        url,
        json=updated_response,
        status=200
    )
    
    etag = sample_referral_response["eTag"]
    result = microsoft_client.update_referral(referral_id, updates, etag)
    
    assert result["details"]["dealValue"] == 75000
    assert "eTag" in result
    
    # Verify If-Match header was sent
    assert len(responses.calls) == 1
    assert responses.calls[0].request.headers["If-Match"] == etag


@responses.activate
def test_update_referral_etag_mismatch(microsoft_client):
    """Test update with stale eTag (conflict)."""
    referral_id = "ref-12345"
    url = f"{PARTNER_CENTER_API_BASE}/engagements/referrals/{referral_id}"
    
    responses.add(
        responses.PATCH,
        url,
        json={"error": {"code": "PreconditionFailed", "message": "ETag mismatch"}},
        status=412
    )
    
    updates = {"details": {"dealValue": 80000}}
    stale_etag = "W/\"datetime'2024-09-19T00%3A00%3A00.0000000Z'\""
    
    with pytest.raises(Exception):  # requests.HTTPError
        microsoft_client.update_referral(referral_id, updates, stale_etag)


# ---------------------------------------------------------------------------
# Tests: Get Referral
# ---------------------------------------------------------------------------

@responses.activate
def test_get_referral_success(microsoft_client, sample_referral_response):
    """Test getting a referral by ID."""
    referral_id = "ref-12345"
    url = f"{PARTNER_CENTER_API_BASE}/engagements/referrals/{referral_id}"
    
    responses.add(
        responses.GET,
        url,
        json=sample_referral_response,
        status=200
    )
    
    result = microsoft_client.get_referral(referral_id)
    
    assert result["id"] == referral_id
    assert result["name"] == "Test Referral"


@responses.activate
def test_get_referral_not_found(microsoft_client):
    """Test getting a non-existent referral."""
    referral_id = "ref-nonexistent"
    url = f"{PARTNER_CENTER_API_BASE}/engagements/referrals/{referral_id}"
    
    responses.add(
        responses.GET,
        url,
        json={"error": {"code": "NotFound", "message": "Referral not found"}},
        status=404
    )
    
    with pytest.raises(Exception):  # requests.HTTPError
        microsoft_client.get_referral(referral_id)


# ---------------------------------------------------------------------------
# Tests: List Referrals
# ---------------------------------------------------------------------------

@responses.activate
def test_list_referrals_no_filter(microsoft_client, sample_referral_response):
    """Test listing referrals without filters."""
    url = f"{PARTNER_CENTER_API_BASE}/engagements/referrals"
    
    responses.add(
        responses.GET,
        url,
        json={"value": [sample_referral_response]},
        status=200
    )
    
    result = microsoft_client.list_referrals()
    
    assert len(result) == 1
    assert result[0]["id"] == "ref-12345"
    
    # Check query parameters
    request = responses.calls[0].request
    assert "createdDateTime" in request.url
    assert "top=100" in request.url


@responses.activate
def test_list_referrals_with_filters(microsoft_client, sample_referral_response):
    """Test listing referrals with status filter."""
    url = f"{PARTNER_CENTER_API_BASE}/engagements/referrals"
    
    responses.add(
        responses.GET,
        url,
        json={"value": [sample_referral_response]},
        status=200
    )
    
    result = microsoft_client.list_referrals(status="Active", substatus="Accepted", top=50)
    
    assert len(result) == 1
    
    # Check query parameters
    request = responses.calls[0].request
    assert "Active" in request.url
    assert "Accepted" in request.url
    assert "top=50" in request.url


@responses.activate
def test_list_referrals_pagination(microsoft_client, sample_referral_response):
    """Test pagination parameters."""
    url = f"{PARTNER_CENTER_API_BASE}/engagements/referrals"
    
    responses.add(
        responses.GET,
        url,
        json={"value": [sample_referral_response]},
        status=200
    )
    
    result = microsoft_client.list_referrals(top=25, skip=50)
    
    assert len(result) == 1
    
    # Check query parameters
    request = responses.calls[0].request
    assert "top=25" in request.url
    assert "skip=50" in request.url


# ---------------------------------------------------------------------------
# Tests: Session Management
# ---------------------------------------------------------------------------

def test_client_close(microsoft_client):
    """Test that client session can be closed."""
    microsoft_client.close()
    # No exception should be raised
