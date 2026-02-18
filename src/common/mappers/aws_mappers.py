"""
AWS Partner Central <-> HubSpot data transformation functions.
Extracted from common/mappers.py for better organization.

This module imports from the parent mappers module to avoid circular imports.
Functions can be accessed via: from common.mappers.aws_mappers import ...
or via: from common.mappers import ...
"""

import sys
import os

# Import from parent directory to avoid circular imports
# We need to import from common.mappers (the file), not common.mappers (the package)
parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

# Import specific functions we want to expose
# Note: This imports from the mappers.py file, not the mappers/ directory
import importlib.util

mappers_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), "mappers.py")
spec = importlib.util.spec_from_file_location("_mappers_module", mappers_file)
_mappers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(_mappers)

hubspot_deal_to_partner_central = _mappers.hubspot_deal_to_partner_central
hubspot_deal_to_partner_central_update = _mappers.hubspot_deal_to_partner_central_update
partner_central_opportunity_to_hubspot = _mappers.partner_central_opportunity_to_hubspot

__all__ = [
    "hubspot_deal_to_partner_central",
    "hubspot_deal_to_partner_central_update",
    "partner_central_opportunity_to_hubspot",
]
