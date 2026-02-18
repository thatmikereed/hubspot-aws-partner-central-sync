"""
AWS Partner Central <-> HubSpot data transformation functions.
Extracted from common/mappers.py for better organization.

This module imports from the parent mappers module to avoid circular imports.
Functions can be accessed via: from common.mappers.aws_mappers import ...
or via: from common.mappers import ...
"""

import sys
import os
import importlib.util  # noqa: E402

# Import from parent directory to avoid circular imports
# We need to import from common.mappers (the file), not common.mappers (the package)
parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

# Import specific functions we want to expose
# Note: This imports from the mappers.py file, not the mappers/ directory

mappers_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), "mappers.py")
spec = importlib.util.spec_from_file_location("_mappers_module", mappers_file)
if spec is None or spec.loader is None:
    raise ImportError("Failed to load mappers module")
_mappers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(_mappers)

# Main transformation functions
hubspot_deal_to_partner_central = _mappers.hubspot_deal_to_partner_central
hubspot_deal_to_partner_central_update = _mappers.hubspot_deal_to_partner_central_update
hubspot_deal_to_partner_central_updates = (
    _mappers.hubspot_deal_to_partner_central_updates
)
partner_central_opportunity_to_hubspot = _mappers.partner_central_opportunity_to_hubspot

# Constants
HUBSPOT_STAGE_TO_PC = _mappers.HUBSPOT_STAGE_TO_PC
PC_STAGE_TO_HUBSPOT = _mappers.PC_STAGE_TO_HUBSPOT
PC_VALID_INDUSTRIES = _mappers.PC_VALID_INDUSTRIES
PC_VALID_DELIVERY_MODELS = _mappers.PC_VALID_DELIVERY_MODELS

# Utility functions
_sanitize_business_problem = _mappers._sanitize_business_problem
_sanitize_website = _mappers._sanitize_website
_map_industry = _mappers._map_industry
_safe_close_date = _mappers._safe_close_date
_build_spend = _mappers._build_spend

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
