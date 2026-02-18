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
    partner_central_opportunity_to_hubspot,
)

__all__ = [
    "hubspot_deal_to_partner_central",
    "hubspot_deal_to_partner_central_update",
    "partner_central_opportunity_to_hubspot",
]
