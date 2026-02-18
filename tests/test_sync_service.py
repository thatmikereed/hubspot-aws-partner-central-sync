"""
Tests for SyncOrchestrator service.
"""

import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime
from common.sync_service import SyncOrchestrator


@pytest.fixture
def mock_hubspot_client():
    """Mock HubSpot client"""
    client = MagicMock()
    return client


@pytest.fixture
def mock_pc_client():
    """Mock Partner Central client"""
    client = MagicMock()
    return client


@pytest.fixture
def sync_orchestrator(mock_hubspot_client, mock_pc_client):
    """Create SyncOrchestrator with mocked clients"""
    return SyncOrchestrator(mock_hubspot_client, mock_pc_client)


def test_sync_orchestrator_initialization(mock_hubspot_client, mock_pc_client):
    """Test SyncOrchestrator initialization"""
    orchestrator = SyncOrchestrator(mock_hubspot_client, mock_pc_client)
    assert orchestrator.hubspot == mock_hubspot_client
    assert orchestrator.pc == mock_pc_client


def test_sync_deal_to_opportunity_success(sync_orchestrator, mock_hubspot_client, mock_pc_client):
    """Test successful deal to opportunity sync"""
    # Setup mocks
    mock_deal = {
        "id": "123",
        "properties": {"dealname": "Test Deal", "dealstage": "qualifiedtobuy"},
    }
    mock_hubspot_client.get_deal.return_value = mock_deal
    
    mock_opportunity = {
        "Lifecycle": {"ReviewStatus": "Draft"}
    }
    mock_pc_client.get_opportunity.return_value = mock_opportunity
    
    with patch("common.mappers.hubspot_deal_to_partner_central_update") as mock_mapper:
        mock_mapper.return_value = {
            "Catalog": "AWS",
            "Identifier": "OPP-123",
            "Project": {"Title": "Test"},
        }
        
        success, error = sync_orchestrator.sync_deal_to_opportunity("123", "OPP-123")
        
        assert success is True
        assert error is None
        mock_hubspot_client.get_deal.assert_called_once_with("123")
        mock_pc_client.update_opportunity.assert_called_once()
        mock_hubspot_client.update_deal.assert_called_once()


def test_sync_deal_to_opportunity_blocked_by_review_status(
    sync_orchestrator, mock_pc_client
):
    """Test sync blocked when opportunity is under review"""
    mock_opportunity = {"Lifecycle": {"ReviewStatus": "Submitted"}}
    mock_pc_client.get_opportunity.return_value = mock_opportunity

    success, error = sync_orchestrator.sync_deal_to_opportunity("123", "OPP-123")

    assert success is False
    assert "Cannot sync" in error
    assert "Submitted" in error


def test_sync_deal_to_opportunity_force_sync(
    sync_orchestrator, mock_hubspot_client, mock_pc_client
):
    """Test force sync overrides review status check"""
    mock_deal = {
        "id": "123",
        "properties": {"dealname": "Test Deal"},
    }
    mock_hubspot_client.get_deal.return_value = mock_deal
    
    with patch("common.mappers.hubspot_deal_to_partner_central_update") as mock_mapper:
        mock_mapper.return_value = {
            "Catalog": "AWS",
            "Identifier": "OPP-123",
        }
        
        success, error = sync_orchestrator.sync_deal_to_opportunity(
            "123", "OPP-123", force=True
        )
        
        # Should succeed even if review status would normally block
        assert success is True
        assert error is None
        # Should not check review status when force=True
        mock_pc_client.get_opportunity.assert_not_called()


def test_sync_deal_to_opportunity_error_handling(
    sync_orchestrator, mock_hubspot_client
):
    """Test error handling during sync"""
    mock_hubspot_client.get_deal.side_effect = Exception("API Error")

    success, error = sync_orchestrator.sync_deal_to_opportunity("123", "OPP-123", force=True)

    assert success is False
    assert "API Error" in error


def test_sync_opportunity_to_deal_success(
    sync_orchestrator, mock_hubspot_client, mock_pc_client
):
    """Test successful opportunity to deal sync"""
    mock_opportunity = {
        "Identifier": "OPP-123",
        "Project": {"Title": "Test Opportunity"},
    }
    mock_pc_client.get_opportunity.return_value = mock_opportunity
    
    with patch("common.mappers.partner_central_opportunity_to_hubspot") as mock_mapper:
        mock_mapper.return_value = {
            "dealname": "Test Opportunity",
            "dealstage": "qualifiedtobuy",
        }
        
        success, error = sync_orchestrator.sync_opportunity_to_deal("OPP-123", "123")
        
        assert success is True
        assert error is None
        mock_pc_client.get_opportunity.assert_called_once_with(
            Catalog="AWS", Identifier="OPP-123"
        )
        mock_hubspot_client.update_deal.assert_called_once()


def test_sync_opportunity_to_deal_error_handling(
    sync_orchestrator, mock_pc_client
):
    """Test error handling during opportunity to deal sync"""
    mock_pc_client.get_opportunity.side_effect = Exception("PC Error")

    success, error = sync_orchestrator.sync_opportunity_to_deal("OPP-123", "123")

    assert success is False
    assert "PC Error" in error


def test_get_review_status_success(sync_orchestrator, mock_pc_client):
    """Test getting review status from opportunity"""
    mock_opportunity = {"Lifecycle": {"ReviewStatus": "In-Review"}}
    mock_pc_client.get_opportunity.return_value = mock_opportunity

    status = sync_orchestrator._get_review_status("OPP-123")

    assert status == "In-Review"


def test_get_review_status_missing(sync_orchestrator, mock_pc_client):
    """Test getting review status when not present"""
    mock_opportunity = {"Lifecycle": {}}
    mock_pc_client.get_opportunity.return_value = mock_opportunity

    status = sync_orchestrator._get_review_status("OPP-123")

    assert status is None


def test_get_review_status_error(sync_orchestrator, mock_pc_client):
    """Test getting review status when API call fails"""
    mock_pc_client.get_opportunity.side_effect = Exception("API Error")

    status = sync_orchestrator._get_review_status("OPP-123")

    assert status is None
