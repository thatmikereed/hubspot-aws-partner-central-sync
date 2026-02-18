"""
Bidirectional mapping between HubSpot Deal properties and
Google Cloud CRM Partners API Opportunity/Lead fields.

This module handles:
  - Creating leads and opportunities in GCP Partners API from HubSpot deals
  - Converting GCP opportunities back to HubSpot deal properties
  - Field validation and constraint handling
  - Bidirectional update synchronization
"""

import logging
from datetime import datetime, timedelta, date, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Stage mappings: HubSpot → GCP Partners API
# ---------------------------------------------------------------------------

HUBSPOT_STAGE_TO_GCP: dict[str, str] = {
    "appointmentscheduled": "QUALIFYING",
    "qualifiedtobuy": "QUALIFIED",
    "presentationscheduled": "QUALIFIED",
    "decisionmakerboughtin": "PROPOSAL",
    "contractsent": "NEGOTIATING",
    "closedwon": "CLOSED_WON",
    "closedlost": "CLOSED_LOST",
}

# GCP to HubSpot mapping - handle multiple HubSpot stages mapping to same GCP stage
GCP_STAGE_TO_HUBSPOT: dict[str, str] = {
    "QUALIFYING": "appointmentscheduled",
    "QUALIFIED": "qualifiedtobuy",
    "PROPOSAL": "decisionmakerboughtin",
    "NEGOTIATING": "contractsent",
    "CLOSED_WON": "closedwon",
    "CLOSED_LOST": "closedlost",
}

# ---------------------------------------------------------------------------
# GCP Product Family enum values
# ---------------------------------------------------------------------------

GCP_PRODUCT_FAMILIES = [
    "GOOGLE_CLOUD_PLATFORM",
    "GOOGLE_WORKSPACE",
    "CHROME_ENTERPRISE",
    "GOOGLE_MAPS_PLATFORM",
    "APIGEE",
]

# Default product family for cloud infrastructure deals
DEFAULT_PRODUCT_FAMILY = "GOOGLE_CLOUD_PLATFORM"

# ---------------------------------------------------------------------------
# GCP Qualification State enum values
# ---------------------------------------------------------------------------

GCP_QUALIFICATION_STATES = [
    "UNQUALIFIED",
    "QUALIFIED",
    "DISQUALIFIED",
]

# ---------------------------------------------------------------------------
# Public: HubSpot deal → GCP Partners Lead (required for opportunity creation)
# ---------------------------------------------------------------------------


def hubspot_deal_to_gcp_lead(
    deal: dict,
    associated_company: Optional[dict] = None,
    associated_contacts: Optional[list] = None,
) -> dict:
    """
    Map a HubSpot deal to a GCP Partners Lead payload.

    In GCP Partners API, leads are created first, then opportunities reference them.
    This function creates the lead payload from HubSpot deal data.

    Args:
        deal: HubSpot deal object
        associated_company: Optional HubSpot company object
        associated_contacts: Optional list of HubSpot contact objects

    Returns:
        Lead payload dict for partners.leads.create API call
    """
    props = deal.get("properties", {})
    co_props = (associated_company or {}).get("properties", {})

    # Build customer/company information
    company_name = (
        co_props.get("name") or props.get("company") or "Unknown Customer"
    ).strip()

    website_url = _sanitize_website(
        co_props.get("website") or co_props.get("domain") or props.get("website")
    )

    # Get primary contact
    primary_contact = None
    if associated_contacts:
        contact = associated_contacts[0]
        contact_props = contact.get("properties", {})
        primary_contact = {
            "givenName": contact_props.get("firstname", "").strip(),
            "familyName": contact_props.get("lastname", "").strip(),
            "email": contact_props.get("email", "").strip(),
            "phone": _sanitize_phone(
                contact_props.get("phone") or contact_props.get("mobilephone")
            ),
        }
        # Remove empty fields
        primary_contact = {k: v for k, v in primary_contact.items() if v}

    # Build notes from deal description
    notes = (props.get("description") or props.get("hs_deal_description") or "").strip()
    if not notes:
        notes = f"HubSpot deal: {props.get('dealname', 'Untitled Deal')}"

    # External system ID for idempotency
    external_id = f"hubspot-deal-{deal['id']}"

    lead_payload = {
        "companyName": company_name[:255],
        "externalSystemId": external_id,
    }

    if website_url:
        lead_payload["companyWebsite"] = website_url

    if primary_contact:
        lead_payload["contact"] = primary_contact

    if notes:
        lead_payload["notes"] = notes[:2000]

    return lead_payload


# ---------------------------------------------------------------------------
# Public: HubSpot deal → GCP Partners Opportunity
# ---------------------------------------------------------------------------


def hubspot_deal_to_gcp_opportunity(
    deal: dict,
    lead_name: str,
    associated_company: Optional[dict] = None,
    associated_contacts: Optional[list] = None,
) -> dict:
    """
    Map a HubSpot deal to a GCP Partners Opportunity payload.

    Args:
        deal: HubSpot deal object
        lead_name: GCP lead resource name (e.g., "partners/12345/leads/67890")
        associated_company: Optional HubSpot company object
        associated_contacts: Optional list of HubSpot contact objects

    Returns:
        Opportunity payload dict for partners.opportunities.create API call
    """
    props = deal.get("properties", {})

    # Map sales stage
    hs_stage = (props.get("dealstage") or "").lower().strip()
    gcp_stage = HUBSPOT_STAGE_TO_GCP.get(hs_stage, "QUALIFYING")

    # Parse deal amount
    deal_size = None
    amount_str = props.get("amount") or props.get("gcp_expected_spend") or "0"
    try:
        deal_size = float(amount_str)
    except (ValueError, TypeError):
        deal_size = 0.0

    # Parse close date
    close_date = _parse_close_date(props.get("closedate"))

    # Product family (default to Google Cloud Platform)
    product_family = _map_product_family(
        props.get("gcp_product_family") or props.get("dealtype")
    )

    # Term in months (for subscription-based deals)
    term_months = _parse_term_months(props.get("gcp_term_months"))

    # Notes and next steps
    notes = (props.get("description") or props.get("hs_deal_description") or "").strip()
    next_steps = (
        props.get("hs_next_step") or props.get("notes_next_activity_description") or ""
    ).strip()

    # Qualification state
    qualification_state = _map_qualification_state(gcp_stage)

    # External ID for tracking
    external_id = f"hubspot-deal-{deal['id']}"

    opportunity_payload: dict = {
        "lead": lead_name,
        "salesStage": gcp_stage,
        "qualificationState": qualification_state,
        "productFamily": product_family,
        "externalSystemId": external_id,
        "closeDate": close_date,  # Always include close date
    }

    if deal_size > 0:
        opportunity_payload["dealSize"] = float(deal_size)

    # Close date is now already included above

    if term_months:
        opportunity_payload["termMonths"] = str(term_months)

    if notes:
        opportunity_payload["notes"] = notes[:2000]

    if next_steps:
        opportunity_payload["nextSteps"] = next_steps[:500]

    # Mark as confidential if deal has sensitive data flag
    if props.get("gcp_is_confidential") == "true":
        opportunity_payload["isConfidential"] = True

    return opportunity_payload


# ---------------------------------------------------------------------------
# Public: GCP Opportunity → HubSpot deal properties
# ---------------------------------------------------------------------------


def gcp_opportunity_to_hubspot_deal(
    gcp_opportunity: dict, gcp_lead: Optional[dict] = None
) -> dict:
    """
    Map a GCP Partners Opportunity to HubSpot deal properties.

    Args:
        gcp_opportunity: GCP opportunity object from API
        gcp_lead: Optional GCP lead object associated with opportunity

    Returns:
        HubSpot deal properties dict
    """
    # Extract opportunity name and ID
    opp_name = gcp_opportunity.get("name", "")
    # Extract ID from name like "partners/12345/opportunities/67890"
    opp_id = opp_name.split("/")[-1] if "/" in opp_name else opp_name

    # Map sales stage back to HubSpot
    gcp_stage = gcp_opportunity.get("salesStage", "QUALIFYING")
    hs_stage = GCP_STAGE_TO_HUBSPOT.get(gcp_stage, "appointmentscheduled")

    # Build deal name with #GCP tag for round-trip filtering
    base_name = (
        gcp_lead.get("companyName", "GCP Partner Opportunity")
        if gcp_lead
        else "GCP Partner Opportunity"
    )
    deal_name = f"{base_name} #GCP" if "#GCP" not in base_name else base_name

    # Parse amount
    deal_size = gcp_opportunity.get("dealSize")
    amount_str = str(deal_size) if deal_size else None

    # Parse close date
    close_date_obj = gcp_opportunity.get("closeDate", {})
    close_date = _gcp_date_to_hubspot_iso(close_date_obj)

    # Build properties dict
    properties = {
        "dealname": deal_name,
        "dealstage": hs_stage,
        "pipeline": "default",
        "gcp_opportunity_id": opp_id,
        "gcp_opportunity_name": opp_name,
        "gcp_sync_status": "synced",
    }

    if amount_str:
        properties["amount"] = amount_str

    if close_date:
        properties["closedate"] = close_date

    # Add notes to description
    notes = gcp_opportunity.get("notes", "")
    if notes:
        properties["description"] = notes

    # Add company name if available
    if gcp_lead:
        company_name = gcp_lead.get("companyName")
        if company_name:
            properties["company"] = company_name

    # Add product family as custom property
    product_family = gcp_opportunity.get("productFamily")
    if product_family:
        properties["gcp_product_family"] = product_family

    # Filter out None/empty values
    return {k: v for k, v in properties.items() if v is not None and v != ""}


# ---------------------------------------------------------------------------
# Public: HubSpot deal update → GCP Opportunity update
# ---------------------------------------------------------------------------


def hubspot_deal_to_gcp_opportunity_update(
    deal: dict,
    current_gcp_opportunity: dict,
    associated_company: Optional[dict] = None,
    associated_contacts: Optional[list] = None,
    changed_properties: Optional[set[str]] = None,
) -> tuple[dict | None, list[str]]:
    """
    Build a GCP Opportunity update payload from a HubSpot deal change.

    Args:
        deal: HubSpot deal object
        current_gcp_opportunity: Current GCP opportunity state
        associated_company: Optional company object
        associated_contacts: Optional contacts
        changed_properties: Set of properties that changed

    Returns:
        Tuple of (update_payload, warnings)
        - update_payload: dict for PATCH request, or None if no update needed
        - warnings: list of warning strings
    """
    warnings: list[str] = []
    props = deal.get("properties", {})

    update_payload: dict = {}

    # Stage changes
    if not changed_properties or "dealstage" in changed_properties:
        hs_stage = (props.get("dealstage") or "").lower().strip()
        gcp_stage = HUBSPOT_STAGE_TO_GCP.get(hs_stage, "QUALIFYING")
        update_payload["salesStage"] = gcp_stage
        update_payload["qualificationState"] = _map_qualification_state(gcp_stage)

    # Amount changes
    if not changed_properties or "amount" in changed_properties:
        amount_str = props.get("amount") or "0"
        try:
            deal_size = float(amount_str)
            if deal_size > 0:
                update_payload["dealSize"] = float(deal_size)
        except (ValueError, TypeError):
            pass

    # Close date changes
    if not changed_properties or "closedate" in changed_properties:
        close_date = _parse_close_date(props.get("closedate"))
        update_payload["closeDate"] = close_date

    # Description/notes changes
    if not changed_properties or "description" in changed_properties:
        notes = (
            props.get("description") or props.get("hs_deal_description") or ""
        ).strip()
        if notes:
            update_payload["notes"] = notes[:2000]

    # Next steps changes
    if not changed_properties or "hs_next_step" in changed_properties:
        next_steps = (props.get("hs_next_step") or "").strip()
        if next_steps:
            update_payload["nextSteps"] = next_steps[:500]

    if not update_payload:
        return None, warnings

    return update_payload, warnings


# ---------------------------------------------------------------------------
# Private helper functions
# ---------------------------------------------------------------------------


def _sanitize_website(url: Optional[str]) -> Optional[str]:
    """Ensure website URL starts with http:// or https://."""
    if not url:
        return None
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url[:500] if url else None


def _sanitize_phone(raw: Optional[str]) -> Optional[str]:
    """
    Sanitize phone number to E.164 format: +[country code][number]
    Returns None if invalid.
    """
    if not raw:
        return None
    digits = "".join(c for c in raw if c.isdigit() or c == "+")
    if not digits.startswith("+"):
        digits = "+1" + digits  # Assume US if no country code
    # Must be at least 4 digits total (country code + number), max 16
    # For US (+1), we need at least 10 digits after the country code
    if len(digits) < 8 or len(digits) > 16:  # Changed from 4 to 8 minimum
        return None
    return digits


def _parse_close_date(raw: Optional[str]) -> dict:
    """
    Parse HubSpot close date to GCP date format.

    GCP expects date as: {"year": 2024, "month": 12, "day": 31}
    Always returns a valid future date (never None).
    """
    if not raw:
        # Default to 90 days from today
        future_date = date.today() + timedelta(days=90)
        return {
            "year": future_date.year,
            "month": future_date.month,
            "day": future_date.day,
        }

    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        parsed_date = dt.date()

        # Ensure date is not in the past
        if parsed_date < date.today():
            # Push to 90 days in future
            parsed_date = date.today() + timedelta(days=90)

        return {
            "year": parsed_date.year,
            "month": parsed_date.month,
            "day": parsed_date.day,
        }
    except (ValueError, AttributeError):
        # Default to 90 days from today
        future_date = date.today() + timedelta(days=90)
        return {
            "year": future_date.year,
            "month": future_date.month,
            "day": future_date.day,
        }


def _gcp_date_to_hubspot_iso(date_obj: dict) -> Optional[str]:
    """
    Convert GCP date object to HubSpot ISO format.

    Args:
        date_obj: Dict with year, month, day keys

    Returns:
        ISO format string like "2024-12-31T00:00:00Z"
    """
    if not date_obj or not isinstance(date_obj, dict):
        return None

    try:
        year = date_obj.get("year")
        month = date_obj.get("month")
        day = date_obj.get("day")

        if not all([year, month, day]):
            return None

        dt = datetime(int(year), int(month), int(day), tzinfo=timezone.utc)
        return dt.isoformat().replace("+00:00", "Z")
    except (ValueError, TypeError):
        return None


def _map_product_family(raw: Optional[str]) -> str:
    """
    Map HubSpot dealtype or custom property to GCP ProductFamily enum.
    Defaults to GOOGLE_CLOUD_PLATFORM.
    """
    if not raw:
        return DEFAULT_PRODUCT_FAMILY

    upper = raw.upper().replace(" ", "_").replace("-", "_")

    if "WORKSPACE" in upper or "GSUITE" in upper:
        return "GOOGLE_WORKSPACE"
    elif "CHROME" in upper:
        return "CHROME_ENTERPRISE"
    elif "MAPS" in upper or "LOCATION" in upper:
        return "GOOGLE_MAPS_PLATFORM"
    elif "APIGEE" in upper or "API" in upper:
        return "APIGEE"

    # Default to Google Cloud Platform for infrastructure/cloud deals
    return DEFAULT_PRODUCT_FAMILY


def _parse_term_months(raw: Optional[str]) -> Optional[int]:
    """Parse term in months from HubSpot property."""
    if not raw:
        return None
    try:
        return int(raw)
    except (ValueError, TypeError):
        return None


def _map_qualification_state(sales_stage: str) -> str:
    """
    Map sales stage to qualification state.

    Args:
        sales_stage: GCP sales stage enum value

    Returns:
        GCP qualification state enum value
    """
    if sales_stage in ["CLOSED_LOST"]:
        return "DISQUALIFIED"
    elif sales_stage in ["QUALIFIED", "PROPOSAL", "NEGOTIATING", "CLOSED_WON"]:
        return "QUALIFIED"
    else:
        return "UNQUALIFIED"
