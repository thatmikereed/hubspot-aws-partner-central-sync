"""
Bidirectional mapping between HubSpot Deal properties and
AWS Partner Central Opportunity fields.
"""

from datetime import datetime
from typing import Optional


# ---------------------------------------------------------------------------
# HubSpot deal stage -> Partner Central stage
# ---------------------------------------------------------------------------
HUBSPOT_STAGE_TO_PC = {
    "appointmentscheduled": "Prospect",
    "qualifiedtobuy": "Qualified",
    "presentationscheduled": "Technical Validation",
    "decisionmakerboughtin": "Business Validation",
    "contractsent": "Committed",
    "closedwon": "Launched",
    "closedlost": "Closed Lost",
}

PC_STAGE_TO_HUBSPOT = {v: k for k, v in HUBSPOT_STAGE_TO_PC.items()}


def hubspot_deal_to_partner_central(deal: dict) -> dict:
    """
    Map a HubSpot deal object (from the CRM API) to an AWS Partner Central
    CreateOpportunity request body.

    Only required and commonly used fields are mapped; extend as needed.
    """
    props = deal.get("properties", {})
    deal_name = props.get("dealname", "Untitled Deal")

    # Parse close date
    close_date = props.get("closedate")
    target_close = None
    if close_date:
        try:
            dt = datetime.fromisoformat(close_date.replace("Z", "+00:00"))
            target_close = dt.strftime("%Y-%m-%d")
        except ValueError:
            target_close = None

    # Map deal stage
    hs_stage = (props.get("dealstage") or "").lower()
    pc_stage = HUBSPOT_STAGE_TO_PC.get(hs_stage, "Prospect")

    # Build opportunity
    opportunity = {
        "Catalog": "AWS",
        "ClientToken": f"hs-{deal['id']}",  # idempotency key
        "OpportunityType": "Net New Business",
        "NationalSecurity": "No",
        "PartnerOpportunityIdentifier": deal["id"],
        "PrimaryNeedsFromAws": ["Co-Sell - Architectural Validation"],
        "Project": {
            "Title": deal_name,
            "CustomerBusinessProblem": props.get("description") or f"HubSpot deal: {deal_name}",
            "DeliveryModels": ["SaaS or PaaS"],
            "ExpectedCustomerSpend": _build_spend(props),
            "SalesActivities": ["Agreed on solution to Business Problem"],
            "Stage": pc_stage,
            "TargetCompletionDate": target_close or _default_close_date(),
        },
        "Customer": {
            "Account": {
                "CompanyName": props.get("company") or "Unknown",
                "CountryCode": "US",
                "Industry": "Other",
                "WebsiteUrl": props.get("website") or "https://example.com",
            }
        },
        "LifeCycle": {
            "Stage": pc_stage,
            "TargetCloseDate": target_close or _default_close_date(),
        },
    }

    return opportunity


def partner_central_opportunity_to_hubspot(pc_opportunity: dict, invitation_id: Optional[str] = None) -> dict:
    """
    Map an AWS Partner Central Opportunity to a HubSpot deal properties dict
    suitable for the CRM create/update API.
    """
    lifecycle = pc_opportunity.get("LifeCycle", {})
    project = pc_opportunity.get("Project", {})
    customer = pc_opportunity.get("Customer", {}).get("Account", {})

    pc_stage = lifecycle.get("Stage", "Prospect")
    hs_stage = PC_STAGE_TO_HUBSPOT.get(pc_stage, "appointmentscheduled")

    # Parse target close date
    close_date = lifecycle.get("TargetCloseDate")
    hs_close = None
    if close_date:
        try:
            dt = datetime.strptime(close_date, "%Y-%m-%d")
            hs_close = dt.isoformat() + "Z"
        except ValueError:
            hs_close = None

    # Extract spend amount
    spend_list = project.get("ExpectedCustomerSpend", [])
    amount = None
    if spend_list:
        amount = spend_list[0].get("Amount")

    title = project.get("Title", "AWS Partner Central Opportunity")
    if "#AWS" not in title:
        title = f"{title} #AWS"

    properties = {
        "dealname": title,
        "dealstage": hs_stage,
        "pipeline": "default",
        "description": project.get("CustomerBusinessProblem", ""),
        "aws_opportunity_id": pc_opportunity.get("Id", ""),
        "aws_opportunity_arn": pc_opportunity.get("Arn", ""),
        "aws_sync_status": "synced",
        "closedate": hs_close or _default_close_date() + "Z",
    }

    if amount:
        properties["amount"] = str(amount)

    if invitation_id:
        properties["aws_invitation_id"] = invitation_id

    return properties


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_spend(props: dict) -> list:
    amount = props.get("amount")
    if amount:
        try:
            return [
                {
                    "Amount": str(float(amount)),
                    "CurrencyCode": "USD",
                    "Frequency": "Monthly",
                    "TargetCompany": "End Customer",
                }
            ]
        except (ValueError, TypeError):
            pass
    return [
        {
            "Amount": "0",
            "CurrencyCode": "USD",
            "Frequency": "Monthly",
            "TargetCompany": "End Customer",
        }
    ]


def _default_close_date() -> str:
    """Return a close date 90 days from today as YYYY-MM-DD."""
    from datetime import timedelta, date
    return (date.today() + timedelta(days=90)).isoformat()
