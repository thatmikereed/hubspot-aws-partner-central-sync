"""
Tests for Sync AWS Summary handler - PSM extraction and field mapping.
"""

import json
import pytest
from unittest.mock import MagicMock, patch, call
from datetime import datetime, timezone


@pytest.fixture
def mock_hubspot_client():
    """Mock HubSpotClient."""
    with patch('sync_aws_summary.handler.HubSpotClient') as mock:
        client = MagicMock()
        mock.return_value = client
        yield client


@pytest.fixture
def mock_pc_client():
    """Mock Partner Central client."""
    with patch('sync_aws_summary.handler.get_partner_central_client') as mock:
        client = MagicMock()
        mock.return_value = client
        yield client


@pytest.fixture
def sample_deal():
    """Sample HubSpot deal."""
    return {
        "id": "12345",
        "properties": {
            "dealname": "Test Deal #AWS",
            "aws_opportunity_id": "O1234567890",
            "aws_engagement_score": "70",
            "aws_review_status": "Submitted",
            "aws_seller_name": "",
            "aws_psm_name": "",
            "aws_psm_email": "",
            "aws_psm_phone": "",
        }
    }


@pytest.fixture
def sample_aws_summary_with_psm():
    """Sample AWS Opportunity Summary with PSM."""
    return {
        "Insights": {
            "EngagementScore": 85
        },
        "LifeCycle": {
            "ReviewStatus": "Approved",
            "InvolvementType": "Co-Sell",
            "NextSteps": "Schedule joint customer call"
        },
        "OpportunityTeam": [
            {
                "FirstName": "John",
                "LastName": "Smith",
                "Email": "john.smith@aws.amazon.com",
                "BusinessTitle": "Solutions Architect"
            },
            {
                "FirstName": "Jane",
                "LastName": "Doe",
                "Email": "jane.doe@aws.amazon.com",
                "BusinessTitle": "Partner Success Manager",
                "Phone": "+1-555-0123"
            }
        ]
    }


@pytest.fixture
def sample_aws_summary_with_psm_variant():
    """Sample AWS Opportunity Summary with PSM (variant title)."""
    return {
        "Insights": {
            "EngagementScore": 90
        },
        "LifeCycle": {
            "ReviewStatus": "Approved",
            "InvolvementType": "Co-Sell",
        },
        "OpportunityTeam": [
            {
                "FirstName": "Bob",
                "LastName": "Johnson",
                "Email": "bob.johnson@aws.amazon.com",
                "BusinessTitle": "Account Manager"
            },
            {
                "FirstName": "Alice",
                "LastName": "Brown",
                "Email": "alice.brown@aws.amazon.com",
                "BusinessTitle": "PSM - Enterprise",
                "Phone": "+1-555-9999"
            }
        ]
    }


@pytest.fixture
def sample_aws_summary_without_psm():
    """Sample AWS Opportunity Summary without PSM."""
    return {
        "Insights": {
            "EngagementScore": 75
        },
        "LifeCycle": {
            "ReviewStatus": "Approved",
            "InvolvementType": "For Visibility Only"
        },
        "OpportunityTeam": [
            {
                "FirstName": "Chris",
                "LastName": "Wilson",
                "Email": "chris.wilson@aws.amazon.com",
                "BusinessTitle": "Solutions Architect"
            }
        ]
    }


def test_psm_extraction_with_partner_success_title(mock_hubspot_client, mock_pc_client, sample_deal, sample_aws_summary_with_psm):
    """Test PSM is correctly extracted when BusinessTitle contains 'Partner Success'."""
    from sync_aws_summary.handler import _sync_aws_summary
    
    # Setup
    mock_pc_client.get_aws_opportunity_summary.return_value = sample_aws_summary_with_psm
    
    # Execute
    result = _sync_aws_summary(
        "12345",
        "O1234567890",
        mock_hubspot_client,
        mock_pc_client,
        sample_deal["properties"]
    )
    
    # Verify
    assert result is not None
    assert result["awsPsm"] == "Jane Doe"
    
    # Check that update_deal was called with PSM fields
    mock_hubspot_client.update_deal.assert_called_once()
    call_args = mock_hubspot_client.update_deal.call_args
    updates = call_args[0][1]  # Second argument to update_deal
    
    assert updates["aws_psm_name"] == "Jane Doe"
    assert updates["aws_psm_email"] == "jane.doe@aws.amazon.com"
    assert updates["aws_psm_phone"] == "+1-555-0123"
    assert updates["aws_seller_name"] == "John Smith"


def test_psm_extraction_with_psm_acronym(mock_hubspot_client, mock_pc_client, sample_deal, sample_aws_summary_with_psm_variant):
    """Test PSM is correctly extracted when BusinessTitle contains 'PSM'."""
    from sync_aws_summary.handler import _sync_aws_summary
    
    # Setup
    mock_pc_client.get_aws_opportunity_summary.return_value = sample_aws_summary_with_psm_variant
    
    # Execute
    result = _sync_aws_summary(
        "12345",
        "O1234567890",
        mock_hubspot_client,
        mock_pc_client,
        sample_deal["properties"]
    )
    
    # Verify
    assert result is not None
    assert result["awsPsm"] == "Alice Brown"
    
    # Check that update_deal was called with PSM fields
    mock_hubspot_client.update_deal.assert_called_once()
    call_args = mock_hubspot_client.update_deal.call_args
    updates = call_args[0][1]
    
    assert updates["aws_psm_name"] == "Alice Brown"
    assert updates["aws_psm_email"] == "alice.brown@aws.amazon.com"
    assert updates["aws_psm_phone"] == "+1-555-9999"


def test_no_psm_in_team(mock_hubspot_client, mock_pc_client, sample_deal, sample_aws_summary_without_psm):
    """Test that no PSM fields are set when no PSM is in the team."""
    from sync_aws_summary.handler import _sync_aws_summary
    
    # Setup
    mock_pc_client.get_aws_opportunity_summary.return_value = sample_aws_summary_without_psm
    
    # Execute
    result = _sync_aws_summary(
        "12345",
        "O1234567890",
        mock_hubspot_client,
        mock_pc_client,
        sample_deal["properties"]
    )
    
    # Verify
    assert result is not None
    assert result.get("awsPsm") is None
    
    # Check that update_deal was called without PSM fields
    mock_hubspot_client.update_deal.assert_called_once()
    call_args = mock_hubspot_client.update_deal.call_args
    updates = call_args[0][1]
    
    assert "aws_psm_name" not in updates
    assert "aws_psm_email" not in updates
    assert "aws_psm_phone" not in updates
    # Should still have seller name
    assert updates["aws_seller_name"] == "Chris Wilson"


def test_psm_without_phone(mock_hubspot_client, mock_pc_client, sample_deal):
    """Test PSM extraction when phone is not provided."""
    summary = {
        "Insights": {"EngagementScore": 80},
        "LifeCycle": {"ReviewStatus": "Approved"},
        "OpportunityTeam": [
            {
                "FirstName": "Test",
                "LastName": "PSM",
                "Email": "test.psm@aws.amazon.com",
                "BusinessTitle": "Partner Success Manager"
                # No Phone field
            }
        ]
    }
    
    from sync_aws_summary.handler import _sync_aws_summary
    
    # Setup
    mock_pc_client.get_aws_opportunity_summary.return_value = summary
    
    # Execute
    result = _sync_aws_summary(
        "12345",
        "O1234567890",
        mock_hubspot_client,
        mock_pc_client,
        sample_deal["properties"]
    )
    
    # Verify
    assert result is not None
    assert result["awsPsm"] == "Test PSM"
    
    # Check updates
    mock_hubspot_client.update_deal.assert_called_once()
    call_args = mock_hubspot_client.update_deal.call_args
    updates = call_args[0][1]
    
    assert updates["aws_psm_name"] == "Test PSM"
    assert updates["aws_psm_email"] == "test.psm@aws.amazon.com"
    assert "aws_psm_phone" not in updates


def test_case_insensitive_psm_matching(mock_hubspot_client, mock_pc_client, sample_deal):
    """Test that PSM matching is case-insensitive."""
    summary = {
        "Insights": {"EngagementScore": 80},
        "LifeCycle": {"ReviewStatus": "Approved"},
        "OpportunityTeam": [
            {
                "FirstName": "Test",
                "LastName": "Manager",
                "Email": "test@aws.amazon.com",
                "BusinessTitle": "PARTNER SUCCESS MANAGER"  # All caps
            }
        ]
    }
    
    from sync_aws_summary.handler import _sync_aws_summary
    
    # Setup
    mock_pc_client.get_aws_opportunity_summary.return_value = summary
    
    # Execute
    result = _sync_aws_summary(
        "12345",
        "O1234567890",
        mock_hubspot_client,
        mock_pc_client,
        sample_deal["properties"]
    )
    
    # Verify - should still match
    assert result is not None
    assert result["awsPsm"] == "Test Manager"
