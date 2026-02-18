"""
HubSpot-specific data formatting and extraction utilities.
"""

from typing import Any, Dict, List


def format_hubspot_properties(raw_properties: Dict[str, Any]) -> Dict[str, Any]:
    """
    Format properties for HubSpot API submission.

    Args:
        raw_properties: Raw property dict

    Returns:
        Formatted properties dict
    """
    # HubSpot API expects properties in a specific format
    # This is a utility function for future use
    return raw_properties


def extract_deal_properties(
    deal: Dict[str, Any], property_names: List[str]
) -> Dict[str, Any]:
    """
    Extract specific properties from HubSpot deal object.

    Args:
        deal: HubSpot deal object
        property_names: List of property names to extract

    Returns:
        Dict of extracted properties
    """
    properties = deal.get("properties", {})
    return {name: properties.get(name) for name in property_names if name in properties}
