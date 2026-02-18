"""
Domain-specific mapper modules for data transformation.
Split from monolithic mappers.py for better organization.

This package structure allows for future organization of mapper functions
by domain (AWS, HubSpot, etc.) while maintaining backward compatibility.
"""

# Re-export from aws_mappers for convenience
from .aws_mappers import (
    hubspot_deal_to_partner_central,
    hubspot_deal_to_partner_central_update,
    hubspot_deal_to_partner_central_updates,
    partner_central_opportunity_to_hubspot,
    # Also expose constants and utility functions for backward compatibility
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

__all__ = [
    "hubspot_deal_to_partner_central",
    "hubspot_deal_to_partner_central_update",
    "hubspot_deal_to_partner_central_updates",
    "partner_central_opportunity_to_hubspot",
    "HUBSPOT_STAGE_TO_PC",
    "PC_STAGE_TO_HUBSPOT",
    "PC_VALID_INDUSTRIES",
    "PC_VALID_DELIVERY_MODELS",
    "_sanitize_business_problem",
    "_sanitize_website",
    "_map_industry",
    "_safe_close_date",
    "_build_spend",
]
