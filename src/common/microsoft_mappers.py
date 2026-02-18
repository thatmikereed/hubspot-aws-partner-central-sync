"""
Bidirectional mapping between HubSpot Deal properties and
Microsoft Partner Center Referral fields.

This module handles:
  - All required and recommended fields for create/update referral
  - Status and qualification mappings
  - Business-validation constraints
  - Reverse mapping for Microsoft-originated referrals → HubSpot deals
"""

import uuid
from datetime import datetime, timedelta, date, timezone
from typing import Optional

# ---------------------------------------------------------------------------
# Status mappings
# ---------------------------------------------------------------------------

HUBSPOT_STAGE_TO_MICROSOFT_STATUS: dict[str, tuple[str, str]] = {
    # HubSpot stage -> (Microsoft status, Microsoft substatus)
    "appointmentscheduled": ("New", "Pending"),
    "qualifiedtobuy": ("Active", "Accepted"),
    "presentationscheduled": ("Active", "Engaged"),
    "decisionmakerboughtin": ("Active", "Engaged"),
    "contractsent": ("Active", "Engaged"),
    "closedwon": ("Closed", "Won"),
    "closedlost": ("Closed", "Lost"),
}

MICROSOFT_STATUS_TO_HUBSPOT: dict[tuple[str, str], str] = {
    # (Microsoft status, substatus) -> HubSpot stage
    ("New", "Pending"): "appointmentscheduled",
    ("New", "Received"): "appointmentscheduled",
    ("Active", "Accepted"): "qualifiedtobuy",
    ("Active", "Engaged"): "presentationscheduled",
    ("Closed", "Won"): "closedwon",
    ("Closed", "Lost"): "closedlost",
    ("Closed", "Declined"): "closedlost",
    ("Closed", "Expired"): "closedlost",
}

# ---------------------------------------------------------------------------
# Qualification mappings
# ---------------------------------------------------------------------------

HUBSPOT_QUALIFICATION: dict[str, str] = {
    # Map HubSpot stages to Microsoft qualification levels
    "appointmentscheduled": "MarketingQualified",
    "qualifiedtobuy": "SalesQualified",
    "presentationscheduled": "SalesQualified",
    "decisionmakerboughtin": "SalesQualified",
    "contractsent": "SalesQualified",
    "closedwon": "SalesQualified",
    "closedlost": "SalesQualified",
}

# ---------------------------------------------------------------------------
# Industry mapping: HubSpot internal values → Microsoft industries
# ---------------------------------------------------------------------------

HUBSPOT_INDUSTRY_TO_MICROSOFT: dict[str, str] = {
    "RETAIL": "Retail",
    "FINANCIAL_SERVICES": "Financial Services",
    "HEALTHCARE": "Healthcare",
    "MANUFACTURING": "Manufacturing",
    "EDUCATION": "Education",
    "TECHNOLOGY": "Technology",
    "TELECOMMUNICATIONS": "Telecommunications",
    "GOVERNMENT": "Government",
    "NONPROFIT": "Non-Profit",
    "HOSPITALITY": "Hospitality",
    "REAL_ESTATE": "Real Estate",
    "CONSTRUCTION": "Construction",
    "AUTOMOTIVE": "Automotive",
    "AGRICULTURE": "Agriculture",
    "ENERGY": "Energy",
    "MEDIA": "Media & Entertainment",
    "TRANSPORTATION": "Transportation",
    "OTHER": "Other",
}

# ---------------------------------------------------------------------------
# Country code mapping
# ---------------------------------------------------------------------------

def _normalize_country_code(country: Optional[str]) -> str:
    """Normalize country code to ISO 3166-1 alpha-2 format."""
    if not country:
        return "US"
    
    country = country.upper().strip()
    
    # Common mappings
    country_map = {
        "USA": "US",
        "UNITED STATES": "US",
        "UNITED STATES OF AMERICA": "US",
        "UK": "GB",
        "UNITED KINGDOM": "GB",
        "ENGLAND": "GB",
    }
    
    return country_map.get(country, country[:2] if len(country) == 2 else "US")


# ---------------------------------------------------------------------------
# HubSpot Deal → Microsoft Referral
# ---------------------------------------------------------------------------

def hubspot_deal_to_microsoft_referral(
    deal: dict,
    company: Optional[dict] = None,
    contacts: Optional[list[dict]] = None,
) -> dict:
    """
    Convert a HubSpot deal to a Microsoft Partner Center referral payload.
    
    Args:
        deal: HubSpot deal object
        company: Associated HubSpot company (optional)
        contacts: List of associated HubSpot contacts (optional)
    
    Returns:
        Microsoft referral payload ready for create_referral API
    """
    props = deal.get("properties", {})
    
    deal_name = props.get("dealname", "Untitled Deal")
    amount = float(props.get("amount") or 0)
    close_date = props.get("closedate", "")
    deal_stage = props.get("dealstage", "appointmentscheduled")
    description = props.get("description", "")
    
    # Get status and substatus from deal stage
    status, substatus = HUBSPOT_STAGE_TO_MICROSOFT_STATUS.get(
        deal_stage, ("New", "Pending")
    )
    
    # Get qualification level
    qualification = HUBSPOT_QUALIFICATION.get(deal_stage, "MarketingQualified")
    
    # Build customer profile
    customer_profile = _build_customer_profile(deal, company, contacts)
    
    # Build details section
    details = {
        "dealValue": amount,
        "currency": "USD",  # Default to USD, can be enhanced
        "notes": description[:500] if description else "Deal synced from HubSpot",
    }
    
    # Add close date if available
    if close_date:
        # Convert HubSpot date format to ISO format
        try:
            if "T" in close_date:
                # Already in ISO format
                details["closeDate"] = close_date.split("T")[0]
            else:
                # Unix timestamp in milliseconds
                timestamp = int(close_date) / 1000
                dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
                details["closeDate"] = dt.strftime("%Y-%m-%d")
        except (ValueError, TypeError):
            # Default to 90 days from now if date parsing fails
            future_date = date.today() + timedelta(days=90)
            details["closeDate"] = future_date.isoformat()
    else:
        # Default to 90 days from now
        future_date = date.today() + timedelta(days=90)
        details["closeDate"] = future_date.isoformat()
    
    # Build the referral payload
    referral = {
        "name": deal_name,
        "externalReferenceId": deal.get("id", str(uuid.uuid4())),
        "type": "Independent",  # Can be "Shared" for co-sell
        "qualification": qualification,
        "customerProfile": customer_profile,
        "consent": {
            "consentToShareReferralWithMicrosoftSellers": False,
        },
        "details": details,
    }
    
    return referral


def _build_customer_profile(
    deal: dict,
    company: Optional[dict] = None,
    contacts: Optional[list[dict]] = None,
) -> dict:
    """Build customer profile section from HubSpot data."""
    props = deal.get("properties", {})
    
    # Start with company info if available
    if company:
        company_props = company.get("properties", {})
        customer_name = company_props.get("name", "Unknown Customer")
        company_domain = company_props.get("domain", "")
        company_city = company_props.get("city", "")
        company_state = company_props.get("state", "")
        company_zip = company_props.get("zip", "")
        company_country = company_props.get("country", "US")
        company_address = company_props.get("address", "")
        company_size = company_props.get("numberofemployees", "")
        company_industry = company_props.get("industry", "")
    else:
        # Try to extract from deal
        customer_name = props.get("customer_name", "Unknown Customer")
        company_domain = ""
        company_city = ""
        company_state = ""
        company_zip = ""
        company_country = "US"
        company_address = ""
        company_size = ""
        company_industry = ""
    
    # Build address
    address = {
        "addressLine1": company_address or "N/A",
        "city": company_city or "Unknown",
        "state": company_state or "",
        "postalCode": company_zip or "",
        "country": _normalize_country_code(company_country),
    }
    
    # Build team (contacts)
    team = []
    if contacts:
        for contact in contacts[:5]:  # Limit to 5 contacts
            contact_props = contact.get("properties", {})
            contact_entry = {
                "firstName": contact_props.get("firstname", ""),
                "lastName": contact_props.get("lastname", ""),
                "emailAddress": contact_props.get("email", ""),
                "phoneNumber": contact_props.get("phone", ""),
            }
            # Only add if we have at least email
            if contact_entry["emailAddress"]:
                team.append(contact_entry)
    
    # If no contacts, create a placeholder
    if not team:
        team.append({
            "firstName": "Contact",
            "lastName": "Unknown",
            "emailAddress": "contact@example.com",
            "phoneNumber": "",
        })
    
    # Determine company size enum
    size = "Unknown"
    if company_size:
        try:
            num_employees = int(company_size)
            if num_employees < 10:
                size = "1to9employees"
            elif num_employees < 50:
                size = "10to50employees"
            elif num_employees < 250:
                size = "51to250employees"
            elif num_employees < 1000:
                size = "251to1000employees"
            elif num_employees < 5000:
                size = "1001to5000employees"
            elif num_employees < 10000:
                size = "5001to10000employees"
            else:
                size = "10001+employees"
        except (ValueError, TypeError):
            size = "Unknown"
    
    customer_profile = {
        "name": customer_name,
        "address": address,
        "size": size,
        "team": team,
    }
    
    # Add industry if available
    if company_industry:
        microsoft_industry = HUBSPOT_INDUSTRY_TO_MICROSOFT.get(
            company_industry.upper(), "Other"
        )
        customer_profile["industry"] = microsoft_industry
    
    return customer_profile


# ---------------------------------------------------------------------------
# HubSpot Deal → Microsoft Referral Update
# ---------------------------------------------------------------------------

def hubspot_deal_to_microsoft_referral_update(
    deal: dict,
    current_referral: dict,
    company: Optional[dict] = None,
    contacts: Optional[list[dict]] = None,
    changed_properties: Optional[set[str]] = None,
) -> tuple[Optional[dict], list[str]]:
    """
    Convert HubSpot deal changes to a Microsoft referral update payload.
    
    Args:
        deal: Current HubSpot deal object
        current_referral: Current Microsoft referral state (with eTag)
        company: Associated HubSpot company (optional)
        contacts: List of associated HubSpot contacts (optional)
        changed_properties: Set of HubSpot property names that changed
    
    Returns:
        Tuple of (update_payload, warnings)
        - update_payload: Dict to pass to update_referral, or None if no updates
        - warnings: List of warning messages
    """
    warnings = []
    updates = {}
    props = deal.get("properties", {})
    
    # Check if referral is in a final state (closed)
    current_status = current_referral.get("status", "")
    if current_status == "Closed":
        warnings.append(
            "Cannot update Microsoft referral - it is already closed. "
            "Reopen the referral in Partner Center first."
        )
        return None, warnings
    
    # Update name if changed
    if not changed_properties or "dealname" in changed_properties:
        new_name = props.get("dealname", "")
        if new_name and new_name != current_referral.get("name"):
            updates["name"] = new_name
    
    # Update status/substatus based on stage
    if not changed_properties or "dealstage" in changed_properties:
        deal_stage = props.get("dealstage", "")
        if deal_stage:
            status, substatus = HUBSPOT_STAGE_TO_MICROSOFT_STATUS.get(
                deal_stage, (None, None)
            )
            if status and status != current_referral.get("status"):
                updates["status"] = status
            if substatus and substatus != current_referral.get("substatus"):
                updates["substatus"] = substatus
    
    # Update details section
    details_updates = {}
    
    if not changed_properties or "amount" in changed_properties:
        amount = props.get("amount")
        if amount:
            new_value = float(amount)
            current_value = current_referral.get("details", {}).get("dealValue", 0)
            if new_value != current_value:
                details_updates["dealValue"] = new_value
    
    if not changed_properties or "closedate" in changed_properties:
        close_date = props.get("closedate", "")
        if close_date:
            try:
                if "T" in close_date:
                    iso_date = close_date.split("T")[0]
                else:
                    timestamp = int(close_date) / 1000
                    dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
                    iso_date = dt.strftime("%Y-%m-%d")
                details_updates["closeDate"] = iso_date
            except (ValueError, TypeError):
                pass
    
    if not changed_properties or "description" in changed_properties:
        description = props.get("description", "")
        if description:
            details_updates["notes"] = description[:500]
    
    if details_updates:
        # Merge with existing details
        current_details = current_referral.get("details", {})
        updates["details"] = {**current_details, **details_updates}
    
    # If no updates, return None
    if not updates:
        return None, warnings
    
    return updates, warnings


# ---------------------------------------------------------------------------
# Microsoft Referral → HubSpot Deal
# ---------------------------------------------------------------------------

def microsoft_referral_to_hubspot_deal(referral: dict) -> dict:
    """
    Convert a Microsoft referral to HubSpot deal properties.
    
    Args:
        referral: Microsoft referral object
    
    Returns:
        Dict of HubSpot deal properties
    """
    # Extract referral fields
    name = referral.get("name", "Untitled Referral")
    referral_id = referral.get("id", "")
    status = referral.get("status", "New")
    substatus = referral.get("substatus", "Pending")
    details = referral.get("details", {})
    customer_profile = referral.get("customerProfile", {})
    
    # Map status to HubSpot stage
    deal_stage = MICROSOFT_STATUS_TO_HUBSPOT.get(
        (status, substatus),
        "appointmentscheduled"
    )
    
    # Extract deal value
    amount = details.get("dealValue", 0)
    currency = details.get("currency", "USD")
    notes = details.get("notes", "")
    close_date = details.get("closeDate", "")
    
    # Convert close date to HubSpot format (Unix timestamp in milliseconds)
    closedate_timestamp = ""
    if close_date:
        try:
            dt = datetime.fromisoformat(close_date.replace("Z", "+00:00"))
            closedate_timestamp = str(int(dt.timestamp() * 1000))
        except (ValueError, TypeError):
            pass
    
    # Build HubSpot deal properties
    deal_properties = {
        "dealname": f"{name} #Microsoft",  # Add tag for identification
        "amount": str(amount),
        "dealstage": deal_stage,
        "description": notes,
        "pipeline": "default",
        "microsoft_referral_id": referral_id,
        "microsoft_sync_status": "synced",
        "microsoft_status": status,
        "microsoft_substatus": substatus,
    }
    
    # Add close date if available
    if closedate_timestamp:
        deal_properties["closedate"] = closedate_timestamp
    
    # Add customer name as a custom field
    customer_name = customer_profile.get("name", "")
    if customer_name:
        deal_properties["customer_name"] = customer_name
    
    return deal_properties


def get_hubspot_custom_properties_for_microsoft() -> list[dict]:
    """
    Get the list of custom HubSpot properties needed for Microsoft integration.
    
    Returns:
        List of property definitions for HubSpot API
    """
    return [
        {
            "name": "microsoft_referral_id",
            "label": "Microsoft Referral ID",
            "description": "The unique ID of the referral in Microsoft Partner Center",
            "groupName": "dealinformation",
            "type": "string",
            "fieldType": "text",
        },
        {
            "name": "microsoft_sync_status",
            "label": "Microsoft Sync Status",
            "description": "Synchronization status with Microsoft Partner Center",
            "groupName": "dealinformation",
            "type": "enumeration",
            "fieldType": "select",
            "options": [
                {"label": "Not Synced", "value": "not_synced"},
                {"label": "Synced", "value": "synced"},
                {"label": "Error", "value": "error"},
            ],
        },
        {
            "name": "microsoft_status",
            "label": "Microsoft Status",
            "description": "Current status in Microsoft Partner Center",
            "groupName": "dealinformation",
            "type": "string",
            "fieldType": "text",
        },
        {
            "name": "microsoft_substatus",
            "label": "Microsoft Substatus",
            "description": "Current substatus in Microsoft Partner Center",
            "groupName": "dealinformation",
            "type": "string",
            "fieldType": "text",
        },
        {
            "name": "customer_name",
            "label": "Customer Name",
            "description": "Name of the customer organization",
            "groupName": "dealinformation",
            "type": "string",
            "fieldType": "text",
        },
    ]
