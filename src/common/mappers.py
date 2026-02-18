"""
Bidirectional mapping between HubSpot Deal properties and
AWS Partner Central Opportunity fields.

This module is the single source of truth for all field-level translation.
It handles:
  - All required and recommended fields for CreateOpportunity
  - All required and recommended fields for UpdateOpportunity
  - The immutability of Project.Title after submission
  - Business-validation constraints (min lengths, enums, date rules)
  - Reverse mapping for AWS-originated opportunities → HubSpot deals
"""

import uuid
from datetime import datetime, timedelta, date, timezone
from typing import Optional

# ---------------------------------------------------------------------------
# Stage mappings
# ---------------------------------------------------------------------------

HUBSPOT_STAGE_TO_PC: dict[str, str] = {
    "appointmentscheduled": "Prospect",
    "qualifiedtobuy": "Qualified",
    "presentationscheduled": "Technical Validation",
    "decisionmakerboughtin": "Business Validation",
    "contractsent": "Committed",
    "closedwon": "Launched",
    "closedlost": "Closed Lost",
}

PC_STAGE_TO_HUBSPOT: dict[str, str] = {v: k for k, v in HUBSPOT_STAGE_TO_PC.items()}

# ---------------------------------------------------------------------------
# Stage → recommended SalesActivities
# ---------------------------------------------------------------------------

STAGE_TO_SALES_ACTIVITIES: dict[str, list[str]] = {
    "Prospect": ["Initialized discussions with customer"],
    "Qualified": ["Customer has shown interest in solution"],
    "Technical Validation": ["Conducted POC / Demo"],
    "Business Validation": ["In evaluation / planning stage"],
    "Committed": ["Agreed on solution to Business Problem"],
    "Launched": ["Finalized Deployment Need"],
    "Closed Lost": [],
}

# ---------------------------------------------------------------------------
# Industry mapping: HubSpot internal values → PC valid enum
# PC valid values (full list from API docs)
# ---------------------------------------------------------------------------

PC_VALID_INDUSTRIES: list[str] = [
    "Aerospace", "Agriculture", "Automotive", "Computers and Electronics",
    "Consumer Goods", "Education", "Energy - Oil and Gas", "Energy - Power and Utilities",
    "Financial Services", "Gaming", "Government", "Healthcare", "Hospitality",
    "Life Sciences", "Manufacturing", "Marketing and Advertising", "Media and Entertainment",
    "Mining", "Non-Profit Organization", "Professional Services",
    "Real Estate and Construction", "Retail", "Software and Internet",
    "Telecommunications", "Transportation and Logistics", "Travel",
    "Wholesale and Distribution", "Other",
]

_HUBSPOT_INDUSTRY_TO_PC: dict[str, str] = {
    # HubSpot company industry values (internal API enum → PC enum)
    "AEROSPACE_AND_DEFENSE":        "Aerospace",
    "AGRICULTURE":                  "Agriculture",
    "APPAREL":                      "Consumer Goods",
    "AUTOMOTIVE":                   "Automotive",
    "BANKING":                      "Financial Services",
    "BIOTECHNOLOGY":                "Life Sciences",
    "CHEMICALS":                    "Manufacturing",
    "COMMUNICATIONS":               "Telecommunications",
    "COMPUTER_HARDWARE":            "Computers and Electronics",
    "COMPUTER_SOFTWARE":            "Software and Internet",
    "CONSTRUCTION":                 "Real Estate and Construction",
    "CONSULTING":                   "Professional Services",
    "CONSUMER_GOODS":               "Consumer Goods",
    "EDUCATION":                    "Education",
    "ELECTRONICS":                  "Computers and Electronics",
    "ENERGY":                       "Energy - Power and Utilities",
    "ENGINEERING":                  "Manufacturing",
    "ENTERTAINMENT":                "Media and Entertainment",
    "ENVIRONMENTAL":                "Other",
    "FINANCE":                      "Financial Services",
    "FINANCIAL_SERVICES":           "Financial Services",
    "FOOD_AND_BEVERAGE":            "Consumer Goods",
    "GAMING":                       "Gaming",
    "GOVERNMENT":                   "Government",
    "HEALTHCARE":                   "Healthcare",
    "HOSPITALITY":                  "Hospitality",
    "INSURANCE":                    "Financial Services",
    "LEGAL":                        "Professional Services",
    "LIFE_SCIENCES":                "Life Sciences",
    "LOGISTICS":                    "Transportation and Logistics",
    "MANUFACTURING":                "Manufacturing",
    "MEDIA":                        "Media and Entertainment",
    "MINING":                       "Mining",
    "NONPROFIT":                    "Non-Profit Organization",
    "PHARMACEUTICALS":              "Life Sciences",
    "PROFESSIONAL_SERVICES":        "Professional Services",
    "REAL_ESTATE":                  "Real Estate and Construction",
    "RETAIL":                       "Retail",
    "SECURITY":                     "Software and Internet",
    "TECHNOLOGY":                   "Software and Internet",
    "TELECOMMUNICATIONS":           "Telecommunications",
    "TRANSPORTATION":               "Transportation and Logistics",
    "TRAVEL_AND_TOURISM":           "Travel",
    "UTILITIES":                    "Energy - Power and Utilities",
    "WHOLESALE":                    "Wholesale and Distribution",
}

# ---------------------------------------------------------------------------
# PC DeliveryModel valid values
# ---------------------------------------------------------------------------

PC_VALID_DELIVERY_MODELS: list[str] = [
    "SaaS or PaaS", "BYOL or AMI", "Managed Services",
    "Professional Services", "Resell", "Other",
]

# ---------------------------------------------------------------------------
# Fields that are immutable in Partner Central after StartEngagementFromOpportunityTask
# These must NEVER be sent in an UpdateOpportunity call once the opportunity
# has been submitted. The most critical one is Project.Title.
# ---------------------------------------------------------------------------

PC_IMMUTABLE_AFTER_SUBMISSION: frozenset[str] = frozenset({
    "Project.Title",
})

# ReviewStatus values that block ALL updates
PC_UPDATE_BLOCKED_STATUSES: frozenset[str] = frozenset({
    "Submitted",
    "In Review",
})

# ---------------------------------------------------------------------------
# Public: HubSpot deal → Partner Central CreateOpportunity payload
# ---------------------------------------------------------------------------

def hubspot_deal_to_partner_central(deal: dict, associated_company: Optional[dict] = None,
                                    associated_contacts: Optional[list] = None) -> dict:
    """
    Map a HubSpot deal (from CRM API) to an AWS Partner Central
    CreateOpportunity request body.

    Satisfies all business-validation required fields:
      - Customer.Account.CompanyName  (required)
      - Customer.Account.Industry     (required, strict enum)
      - Customer.Account.WebsiteUrl   (required, 4-255 chars)
      - Project.CustomerBusinessProblem (required, 20-2000 chars)
      - Project.DeliveryModels        (required, enum list)
      - Project.ExpectedCustomerSpend.CurrencyCode (required)
      - Project.ExpectedCustomerSpend.Frequency = "Monthly" (only valid value)
      - Project.ExpectedCustomerSpend.TargetCompany = "AWS" (only valid value)
      - LifeCycle.Stage               (required, enum)
      - LifeCycle.TargetCloseDate     (required, YYYY-MM-DD, must not be past)
      - Origin = "Partner Referral"   (required for Catalog=AWS)

    Args:
        deal: HubSpot deal object from /crm/v3/objects/deals/{id}
        associated_company: Optional HubSpot company object for the deal
        associated_contacts: Optional list of HubSpot contact objects for the deal
    """
    props = deal.get("properties", {})
    deal_name = (props.get("dealname") or "Untitled Deal").strip()

    # ---- Customer data (prefer company record, fall back to deal props) ----
    co_props = (associated_company or {}).get("properties", {})

    company_name = (
        co_props.get("name")
        or props.get("company")
        or "Unknown Customer"
    ).strip()[:120]  # API constraint: max 120 chars

    website_url = _sanitize_website(
        co_props.get("website")
        or co_props.get("domain")
        or props.get("website")
    )

    country_code = _map_country_code(
        co_props.get("country") or props.get("country") or "US"
    )

    city = (co_props.get("city") or props.get("city") or "")[:255]
    state = _map_state(co_props.get("state") or props.get("state") or "")
    postal_code = (co_props.get("zip") or props.get("zip") or "")[:20]
    street = (co_props.get("address") or props.get("address") or "")[:255]

    industry = _map_industry(
        co_props.get("industry")
        or props.get("industry")
        or props.get("aws_industry")
    )

    national_security = "Yes" if industry == "Government" else "No"

    # ---- Lifecycle ----
    hs_stage = (props.get("dealstage") or "").lower().strip()
    pc_stage = HUBSPOT_STAGE_TO_PC.get(hs_stage, "Prospect")

    target_close = _safe_close_date(props.get("closedate"))

    next_steps = (props.get("hs_next_step") or props.get("notes_next_activity_description") or "")[:255]

    # ---- Project ----
    business_problem = _sanitize_business_problem(
        props.get("description") or props.get("hs_deal_description"),
        deal_name=deal_name,
    )

    delivery_models = _parse_delivery_models(props.get("aws_delivery_models"))

    sales_activities = STAGE_TO_SALES_ACTIVITIES.get(pc_stage, ["Initialized discussions with customer"])

    use_case = _parse_use_case(props.get("aws_use_case") or props.get("dealtype"))

    # ---- Contacts ----
    contacts = _map_contacts(associated_contacts)

    # ---- Spend ----
    spend = _build_spend(props)

    # ---- PrimaryNeedsFromAws ----
    primary_needs = _parse_primary_needs(props.get("aws_primary_needs"))

    opportunity: dict = {
        "Catalog": "AWS",
        "ClientToken": _make_client_token(deal["id"]),
        "Origin": "Partner Referral",
        "OpportunityType": _map_opportunity_type(props.get("dealtype")),
        "NationalSecurity": national_security,
        "PartnerOpportunityIdentifier": str(deal["id"])[:64],
        "PrimaryNeedsFromAws": primary_needs,
        "Customer": {
            "Account": {
                "CompanyName": company_name,
                "Industry": industry,
                "WebsiteUrl": website_url,
                "Address": {
                    "CountryCode": country_code,
                    **({"City": city} if city else {}),
                    **({"StateOrRegion": state} if state else {}),
                    **({"PostalCode": postal_code} if postal_code else {}),
                    **({"StreetAddress": street} if street else {}),
                },
            },
            **({"Contacts": contacts} if contacts else {}),
        },
        "LifeCycle": {
            "Stage": pc_stage,
            "TargetCloseDate": target_close,
            **({"NextSteps": next_steps} if next_steps else {}),
        },
        "Project": {
            "Title": deal_name[:255],
            "CustomerBusinessProblem": business_problem,
            "DeliveryModels": delivery_models,
            "ExpectedCustomerSpend": spend,
            "SalesActivities": sales_activities,
            **({"CustomerUseCase": use_case} if use_case else {}),
        },
    }

    return opportunity


# ---------------------------------------------------------------------------
# Public: HubSpot deal → Partner Central UpdateOpportunity payload
# Excludes immutable fields and respects review-status blocking.
# ---------------------------------------------------------------------------

def hubspot_deal_to_partner_central_update(
    deal: dict,
    current_pc_opportunity: dict,
    associated_company: Optional[dict] = None,
    associated_contacts: Optional[list] = None,
    changed_properties: Optional[set[str]] = None,
) -> tuple[dict | None, list[str]]:
    """
    Build an UpdateOpportunity payload from a HubSpot deal, respecting all
    Partner Central immutability and review-status rules.

    Returns:
        (payload, warnings)
        - payload: dict ready for pc_client.update_opportunity(), or None if
                   no update should be sent (e.g. blocked by review status)
        - warnings: list of human-readable warning strings (e.g. title skip)
    """
    warnings: list[str] = []
    review_status = current_pc_opportunity.get("LifeCycle", {}).get("ReviewStatus", "")

    # Block all updates when submitted / in review
    if review_status in PC_UPDATE_BLOCKED_STATUSES:
        warnings.append(
            f"Update blocked: PC opportunity is in '{review_status}' status. "
            "No changes will be sent until AWS review is complete."
        )
        return None, warnings

    # Build the full create payload first, then strip immutable fields
    full = hubspot_deal_to_partner_central(deal, associated_company, associated_contacts)

    update_payload: dict = {
        "Catalog": "AWS",
        "Identifier": current_pc_opportunity["Id"],
        "Customer": full["Customer"],
        "LifeCycle": full["LifeCycle"],
        "Project": {k: v for k, v in full["Project"].items() if k != "Title"},
        "NationalSecurity": full["NationalSecurity"],
        "PrimaryNeedsFromAws": full["PrimaryNeedsFromAws"],
        "OpportunityType": full["OpportunityType"],
    }

    # Always omit Title from updates — it is immutable after submission
    if changed_properties and "dealname" in changed_properties:
        warnings.append(
            "⚠️  The opportunity title cannot be changed in AWS Partner Central after submission. "
            "The deal name change in HubSpot has NOT been pushed to Partner Central. "
            f"The title in Partner Central remains: '{current_pc_opportunity.get('Project', {}).get('Title', '')}'"
        )

    return update_payload, warnings


# ---------------------------------------------------------------------------
# Public: Partner Central Opportunity → HubSpot deal properties
# ---------------------------------------------------------------------------

def partner_central_opportunity_to_hubspot(
    pc_opportunity: dict,
    invitation_id: Optional[str] = None,
) -> dict:
    """
    Map an AWS Partner Central Opportunity to a HubSpot deal properties dict
    for the CRM create/update API.
    """
    lifecycle = pc_opportunity.get("LifeCycle", {})
    project = pc_opportunity.get("Project", {})
    customer_account = pc_opportunity.get("Customer", {}).get("Account", {})

    pc_stage = lifecycle.get("Stage") or "Prospect"
    hs_stage = PC_STAGE_TO_HUBSPOT.get(pc_stage, "appointmentscheduled")

    close_date = lifecycle.get("TargetCloseDate")
    hs_close = _pc_date_to_hubspot_iso(close_date)

    spend_list = project.get("ExpectedCustomerSpend", [])
    amount = spend_list[0].get("Amount") if spend_list else None

    raw_title = project.get("Title") or "AWS Partner Central Opportunity"
    # Ensure the deal title contains #AWS so it round-trips correctly
    title = raw_title if "#AWS" in raw_title else f"{raw_title} #AWS"

    properties: dict = {
        "dealname": title,
        # Store the canonical PC title so we can detect HubSpot-side renames
        "aws_opportunity_title": raw_title,
        "dealstage": hs_stage,
        "pipeline": "default",
        "description": project.get("CustomerBusinessProblem", ""),
        "aws_opportunity_id": pc_opportunity.get("Id", ""),
        "aws_opportunity_arn": pc_opportunity.get("Arn", ""),
        "aws_review_status": lifecycle.get("ReviewStatus", ""),
        "aws_sync_status": "synced",
        "closedate": hs_close,
    }

    if customer_account.get("CompanyName"):
        properties["company"] = customer_account["CompanyName"]

    if amount:
        properties["amount"] = str(amount)

    if invitation_id:
        properties["aws_invitation_id"] = invitation_id

    return {k: v for k, v in properties.items() if v is not None and v != ""}


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _make_client_token(deal_id: str) -> str:
    """
    Deterministic idempotency token derived from the HubSpot deal ID.
    Matches the pattern .{1,255}.
    """
    return f"hs-deal-{deal_id}"


def _sanitize_business_problem(raw: Optional[str], deal_name: str = "") -> str:
    """
    Ensure the CustomerBusinessProblem satisfies the 20-2000 char constraint.
    If the raw value is too short or missing, synthesise a meaningful fallback.
    """
    text = (raw or "").strip()
    if len(text) < 20:
        # Build a fallback that always exceeds 20 chars
        fallback = (
            f"HubSpot deal '{deal_name}' is being co-sold with AWS. "
            "The customer is evaluating AWS services to solve their business needs."
        )
        text = (text + " " + fallback).strip() if text else fallback
    return text[:2000]


def _sanitize_website(url: Optional[str]) -> str:
    """
    Ensure WebsiteUrl is 4-255 chars and starts with https://.
    Falls back to a placeholder if nothing is available.
    """
    if not url:
        return "https://www.example.com"
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url[:255] if len(url) >= 4 else "https://www.example.com"


def _map_industry(raw: Optional[str]) -> str:
    """
    Map a HubSpot industry string to the Partner Central enum.
    Accepts both HubSpot internal (COMPUTER_SOFTWARE) and natural-language forms.
    Falls back to "Other" if no match is found.
    """
    if not raw:
        return "Other"
    # Direct PC value?
    if raw in PC_VALID_INDUSTRIES:
        return raw
    # HubSpot uppercase enum key?
    upper = raw.upper().replace(" ", "_").replace("-", "_")
    if upper in _HUBSPOT_INDUSTRY_TO_PC:
        return _HUBSPOT_INDUSTRY_TO_PC[upper]
    # Case-insensitive partial match against PC values
    lower = raw.lower()
    for pc_industry in PC_VALID_INDUSTRIES:
        if lower in pc_industry.lower() or pc_industry.lower() in lower:
            return pc_industry
    return "Other"


def _map_country_code(raw: Optional[str]) -> str:
    """
    Best-effort mapping to a 2-letter ISO country code accepted by Partner Central.
    Falls back to "US".
    """
    if not raw:
        return "US"
    clean = raw.strip().upper()
    # Already a 2-letter code?
    if len(clean) == 2:
        return clean
    # Common full-name fallbacks
    _name_to_code = {
        "UNITED STATES": "US", "USA": "US",
        "UNITED KINGDOM": "GB", "UK": "GB",
        "CANADA": "CA", "AUSTRALIA": "AU",
        "GERMANY": "DE", "FRANCE": "FR",
        "INDIA": "IN", "JAPAN": "JP",
        "BRAZIL": "BR", "MEXICO": "MX",
    }
    return _name_to_code.get(clean, "US")


def _map_state(raw: str) -> str:
    """Pass through state/region value; truncate to 255 chars."""
    return raw[:255] if raw else ""


def _safe_close_date(raw: Optional[str]) -> str:
    """
    Parse a HubSpot close date, ensure it is not in the past (Partner Central
    rejects past dates), and return YYYY-MM-DD.
    If the date is in the past, push it forward to 90 days from today.
    """
    today = date.today()
    minimum_date = today + timedelta(days=1)

    if raw:
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            parsed = dt.date()
            if parsed >= minimum_date:
                return parsed.isoformat()
        except (ValueError, AttributeError):
            pass

    return (today + timedelta(days=90)).isoformat()


def _pc_date_to_hubspot_iso(raw: Optional[str]) -> Optional[str]:
    """Convert a YYYY-MM-DD date string to HubSpot ISO format."""
    if not raw:
        return None
    try:
        dt = datetime.strptime(raw, "%Y-%m-%d")
        return dt.isoformat() + "Z"
    except ValueError:
        return None


def _parse_delivery_models(raw: Optional[str]) -> list[str]:
    """
    Parse a comma-separated delivery models string from a HubSpot property.
    Falls back to ["SaaS or PaaS"].
    """
    if not raw:
        return ["SaaS or PaaS"]
    parts = [p.strip() for p in raw.split(",")]
    valid = [p for p in parts if p in PC_VALID_DELIVERY_MODELS]
    return valid if valid else ["SaaS or PaaS"]


def _parse_primary_needs(raw: Optional[str]) -> list[str]:
    """
    Parse a comma-separated primary needs string from a HubSpot property.
    Falls back to a sensible default.
    """
    valid_needs = {
        "Co-Sell - Architectural Validation",
        "Co-Sell - Business Presentation",
        "Co-Sell - Competitive Information",
        "Co-Sell - Pricing Assistance",
        "Co-Sell - Technical Consultation",
        "Co-Sell - Total Cost of Ownership Evaluation",
        "Co-Sell - Deal Support",
        "Co-Sell - Support for Public Tender / RFx",
    }
    if not raw:
        return ["Co-Sell - Deal Support"]
    parts = [p.strip() for p in raw.split(",")]
    matched = [p for p in parts if p in valid_needs]
    return matched if matched else ["Co-Sell - Deal Support"]


def _parse_use_case(raw: Optional[str]) -> Optional[str]:
    """
    Map a HubSpot use-case or deal-type string to a PC CustomerUseCase value.
    """
    valid_use_cases = {
        "AI Machine Learning and Analytics",
        "Archiving",
        "Big Data: Data Warehouse/Data Integration/ETL/Data Lake/BI",
        "Blockchain",
        "Business Applications: Mainframe Modernization",
        "Business Applications & Contact Center",
        "Business Applications & SAP Production",
        "Centralized Operations Management",
        "Cloud Management Tools",
        "Cloud Management Tools & DevOps with Continuous Integration & Continuous Delivery (CICD)",
        "Configuration, Compliance & Auditing",
        "Connected Services",
        "Containers & Serverless",
        "Content Delivery & Edge Services",
        "Database",
        "Edge Computing/End User Computing",
        "Energy",
        "Enterprise Governance & Controls",
        "Enterprise Resource Planning",
        "Financial Services",
        "Healthcare and Life Sciences",
        "High Performance Computing",
        "Hybrid Application Platform",
        "Industrial Software",
        "IOT",
        "Manufacturing, Supply Chain and Operations",
        "Media & High performance computing (HPC)",
        "Migration/Database Migration",
        "Monitoring, logging and performance",
        "Monitoring & Observability",
        "Networking",
        "Outpost",
        "SAP",
        "Security & Compliance",
        "Storage & Backup",
        "Training",
        "VMC",
        "VMWare",
        "Web development & DevOps",
    }
    if not raw:
        return None
    if raw in valid_use_cases:
        return raw
    lower = raw.lower()
    for uc in valid_use_cases:
        if lower in uc.lower():
            return uc
    return None


def _map_opportunity_type(raw: Optional[str]) -> str:
    """Map HubSpot dealtype to PC OpportunityType."""
    if not raw:
        return "Net New Business"
    lower = raw.lower()
    if "renew" in lower:
        return "Flat Renewal"
    if "expan" in lower or "upsell" in lower:
        return "Expansion"
    return "Net New Business"


def _build_spend(props: dict) -> list[dict]:
    """
    Build ExpectedCustomerSpend list.
    - Frequency must be "Monthly" (the only valid value)
    - TargetCompany must be "AWS" (the only valid value per API docs)
    - CurrencyCode is required
    """
    amount = props.get("amount") or props.get("aws_expected_spend")
    currency = (props.get("deal_currency_code") or "USD").upper()

    try:
        amount_str = f"{float(amount):.2f}" if amount else "0.00"
    except (ValueError, TypeError):
        amount_str = "0.00"

    return [
        {
            "Amount": amount_str,
            "CurrencyCode": currency,
            "Frequency": "Monthly",
            "TargetCompany": "AWS",
        }
    ]


def _map_contacts(contacts: Optional[list]) -> list[dict]:
    """
    Map HubSpot contacts to Partner Central Contact objects.
    Filters out contacts missing both email and name.
    """
    if not contacts:
        return []

    result = []
    for c in contacts[:10]:  # API max: 10 contacts
        p = c.get("properties", {})
        email = p.get("email", "").strip()
        first = p.get("firstname", "").strip()[:80]
        last = p.get("lastname", "").strip()[:80]
        phone = _sanitize_phone(p.get("phone") or p.get("mobilephone"))
        title = p.get("jobtitle", "").strip()[:80]

        if not email and not first and not last:
            continue  # skip empty contacts

        contact: dict = {}
        if email:
            contact["Email"] = email[:80]
        if first:
            contact["FirstName"] = first
        if last:
            contact["LastName"] = last
        if phone:
            contact["Phone"] = phone
        if title:
            contact["BusinessTitle"] = title

        result.append(contact)

    return result


def _sanitize_phone(raw: Optional[str]) -> Optional[str]:
    """
    Sanitize a phone number to the Partner Central format: +[1-9][0-9]{1,14}
    Returns None if the number can't be normalized.
    """
    if not raw:
        return None
    digits = "".join(c for c in raw if c.isdigit() or c == "+")
    if not digits.startswith("+"):
        digits = "+1" + digits  # assume US if no country code
    # Must be +[country][number], 2-15 digits after +
    if len(digits) < 4 or len(digits) > 16:
        return None
    return digits


# ---------------------------------------------------------------------------
# Public: Simplified HubSpot deal property update → Partner Central update
# Used by webhook handler for incremental property changes
# ---------------------------------------------------------------------------

def hubspot_deal_to_partner_central_updates(
    deal: dict,
    associated_company: Optional[dict] = None,
    associated_contacts: Optional[list] = None,
    changed_property: Optional[str] = None,
    new_value: Optional[str] = None,
) -> Optional[dict]:
    """
    Create a Partner Central UpdateOpportunity payload from a HubSpot deal
    property change. This is a lightweight version for webhook-driven updates.
    
    Args:
        deal: Full HubSpot deal object
        associated_company: Associated company object (optional)
        associated_contacts: Associated contacts (optional)
        changed_property: The property that changed (e.g., "dealstage")
        new_value: The new value of the property
    
    Returns:
        Update payload dict or None if no update is needed
        
    Note:
        Caller must verify the deal has an aws_opportunity_id before calling.
        This function only builds the update payload and does not validate
        that the deal is linked to a Partner Central opportunity.
    """
    props = deal.get("properties", {})
    
    # Build minimal update payload based on changed property
    update_payload = {}
    
    # Stage changes
    if changed_property == "dealstage":
        hs_stage = (new_value or "").lower().strip()
        pc_stage = HUBSPOT_STAGE_TO_PC.get(hs_stage, "Prospect")
        sales_activities = STAGE_TO_SALES_ACTIVITIES.get(pc_stage, ["Initialized discussions with customer"])
        
        update_payload["LifeCycle"] = {
            "Stage": pc_stage,
            "TargetCloseDate": _safe_close_date(props.get("closedate")),
        }
        update_payload["Project"] = {
            "SalesActivities": sales_activities,
        }
    
    # Close date changes
    elif changed_property == "closedate":
        target_close = _safe_close_date(new_value)
        update_payload["LifeCycle"] = {
            "Stage": HUBSPOT_STAGE_TO_PC.get((props.get("dealstage") or "").lower(), "Prospect"),
            "TargetCloseDate": target_close,
        }
    
    # Amount changes
    elif changed_property == "amount":
        spend = _build_spend(props)
        update_payload["Project"] = {
            "ExpectedCustomerSpend": spend,
        }
    
    # Description changes
    elif changed_property in ["description", "hs_deal_description"]:
        business_problem = _sanitize_business_problem(
            new_value or props.get("description") or props.get("hs_deal_description"),
            deal_name=props.get("dealname", ""),
        )
        update_payload["Project"] = {
            "CustomerBusinessProblem": business_problem,
        }
    
    # Deal name changes (note: title is immutable in PC after submission)
    elif changed_property == "dealname":
        # We cannot update the title in Partner Central after submission
        # Log this but don't include in payload
        return None
    
    # Currency code changes
    elif changed_property == "deal_currency_code":
        spend = _build_spend(props)
        update_payload["Project"] = {
            "ExpectedCustomerSpend": spend,
        }
    
    return update_payload if update_payload else None
