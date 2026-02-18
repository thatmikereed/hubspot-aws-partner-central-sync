"""
Lambda handler for syncing HubSpot company changes to AWS Partner Central.

When a company property changes in HubSpot, this handler:
1. Finds all deals associated with the company
2. For each deal with an AWS opportunity, updates the Partner Central opportunity
3. Adds a note to the deal documenting the sync
4. Updates the aws_contact_company_last_sync property

Trigger: HubSpot webhook (company.propertyChange)

Refactored to use BaseLambdaHandler pattern for consistent error handling and client initialization.
"""

from common.base_handler import BaseLambdaHandler

# Industry mapping from HubSpot to Partner Central
HUBSPOT_INDUSTRY_TO_PC = {
    "AEROSPACE": "Aerospace",
    "AGRICULTURE": "Agriculture",
    "AUTOMOTIVE": "Automotive",
    "BANKING": "Financial Services",
    "BIOTECHNOLOGY": "Life Sciences",
    "CHEMICALS": "Manufacturing",
    "COMMUNICATIONS": "Telecommunications",
    "COMPUTER_HARDWARE": "Computers and Electronics",
    "COMPUTER_SOFTWARE": "Software and Internet",
    "CONSTRUCTION": "Real Estate and Construction",
    "CONSULTING": "Professional Services",
    "CONSUMER_GOODS": "Consumer Goods",
    "EDUCATION": "Education",
    "ELECTRONICS": "Computers and Electronics",
    "ENERGY": "Energy - Power and Utilities",
    "ENTERTAINMENT": "Media and Entertainment",
    "FINANCE": "Financial Services",
    "FINANCIAL_SERVICES": "Financial Services",
    "FOOD_BEVERAGE": "Consumer Goods",
    "GAMING": "Gaming",
    "GOVERNMENT": "Government",
    "HEALTHCARE": "Healthcare",
    "HOSPITALITY": "Hospitality",
    "INSURANCE": "Financial Services",
    "LEGAL": "Professional Services",
    "LIFE_SCIENCES": "Life Sciences",
    "LOGISTICS": "Transportation and Logistics",
    "MANUFACTURING": "Manufacturing",
    "MEDIA": "Media and Entertainment",
    "MINING": "Mining",
    "NONPROFIT": "Non-Profit Organization",
    "PHARMACEUTICALS": "Life Sciences",
    "REAL_ESTATE": "Real Estate and Construction",
    "RETAIL": "Retail",
    "SOFTWARE": "Software and Internet",
    "TELECOMMUNICATIONS": "Telecommunications",
    "TRANSPORTATION": "Transportation and Logistics",
    "TRAVEL": "Travel",
    "WHOLESALE": "Wholesale and Distribution",
}


class CompanySyncHandler(BaseLambdaHandler):
    """
    Handles HubSpot company property change webhooks.

    When a company property changes:
    1. Finds all deals associated with the company
    2. For each deal with an AWS opportunity, updates Partner Central
    3. Adds sync notes to deals
    """

    def _execute(self, event: dict, context: dict) -> dict:
        # Parse webhook payload
        body = self._parse_webhook_body(event)

        company_id = body.get("objectId")
        property_name = body.get("propertyName")
        property_value = body.get("propertyValue")

        if not company_id:
            return self._error_response("Missing company ID", 400)

        self.logger.info(
            f"Company {company_id} property '{property_name}' changed to '{property_value}'"
        )

        # Get full company details
        company = self.hubspot_client.get_company(company_id)
        if not company:
            return self._error_response("Company not found", 404)

        # Find all deals associated with this company
        associated_deals = self.hubspot_client.get_company_associations(
            company_id, "deals"
        )

        if not associated_deals:
            self.logger.info(f"No deals associated with company {company_id}")
            return self._success_response(
                {"message": "No deals to sync", "companyId": company_id}
            )

        self.logger.info(f"Found {len(associated_deals)} associated deals")

        # Sync each deal's opportunity
        synced_count = 0
        skipped_count = 0
        errors = []

        for deal_id in associated_deals:
            try:
                result = self._sync_deal(
                    deal_id, company, property_name, property_value
                )
                if result["synced"]:
                    synced_count += 1
                else:
                    skipped_count += 1
                if result.get("error"):
                    errors.append(result["error"])

            except Exception as e:
                self.logger.error(f"Error syncing deal {deal_id}: {e}", exc_info=True)
                errors.append(f"Deal {deal_id}: {str(e)}")

        # Return summary
        result = {
            "companyId": company_id,
            "propertyChanged": property_name,
            "dealsFound": len(associated_deals),
            "dealsSynced": synced_count,
            "dealsSkipped": skipped_count,
            "errors": errors,
        }

        self.logger.info(f"Company sync complete: {result}")
        return self._success_response(result)

    def _sync_deal(
        self, deal_id: str, company: dict, property_name: str, property_value: str
    ) -> dict:
        """
        Sync a single deal's opportunity with company information.

        Args:
            deal_id: HubSpot deal ID
            company: HubSpot company object
            property_name: Name of the property that changed
            property_value: New value of the property

        Returns:
            Dict with 'synced' boolean and optional 'error' message
        """
        # Get deal details
        deal = self.hubspot_client.get_deal(deal_id)
        if not deal:
            self.logger.warning(f"Deal {deal_id} not found, skipping")
            return {"synced": False}

        # Check if deal has an AWS opportunity
        properties = deal.get("properties", {})
        opportunity_id = properties.get("aws_opportunity_id")

        if not opportunity_id:
            self.logger.debug(f"Deal {deal_id} has no AWS opportunity, skipping")
            return {"synced": False}

        # Get current opportunity from Partner Central
        try:
            current_opportunity = self.pc_client.get_opportunity(
                Catalog="AWS", Identifier=opportunity_id
            )
        except Exception as e:
            self.logger.error(f"Failed to get opportunity {opportunity_id}: {e}")
            return {"synced": False, "error": f"Deal {deal_id}: {str(e)}"}

        # Build updated customer account information
        company_props = company.get("properties", {})
        customer_account = _map_company_to_partner_central_account(company_props)

        # Build customer object
        customer = {"Account": customer_account}

        # Preserve existing contacts
        existing_customer = current_opportunity.get("Customer", {})
        if "Contacts" in existing_customer:
            customer["Contacts"] = existing_customer["Contacts"]

        # Update the opportunity
        update_payload = {
            "Catalog": "AWS",
            "Identifier": opportunity_id,
            "Customer": customer,
            "LifeCycle": current_opportunity.get("LifeCycle", {}),
            "Project": current_opportunity.get("Project", {}),
        }

        # Remove Title from Project (immutable)
        if "Title" in update_payload["Project"]:
            del update_payload["Project"]["Title"]

        self.logger.info(f"Updating opportunity {opportunity_id} with new company info")
        self.pc_client.update_opportunity(**update_payload)

        # Add note to HubSpot deal
        note_text = f"""🔄 Company Information Synced to AWS Partner Central

Company: {company_props.get('name', 'Unknown')}
Property changed: {property_name}
New value: {property_value}

Company information for this opportunity has been updated in AWS Partner Central."""

        self.hubspot_client.create_deal_note(deal_id, note_text)

        # Update sync timestamp
        self.hubspot_client.update_deal(
            deal_id,
            {"aws_contact_company_last_sync": self.hubspot_client.now_timestamp_ms()},
        )

        self.logger.info(f"Successfully synced company to opportunity {opportunity_id}")
        return {"synced": True}


def _map_company_to_partner_central_account(company_props: dict) -> dict:
    """
    Map HubSpot company properties to Partner Central Account format.

    Args:
        company_props: HubSpot company properties dict

    Returns:
        Partner Central Account dict
    """
    # Company name (required)
    company_name = company_props.get("name", "Unknown Company")[:120]

    # Industry mapping
    raw_industry = company_props.get("industry", "").upper().replace(" ", "_")
    industry = HUBSPOT_INDUSTRY_TO_PC.get(raw_industry, "Other")

    # Website URL
    website = company_props.get("website", "").strip()
    if website and not website.startswith("http"):
        website = "https://" + website
    website = website[:255] if website else None

    # Address components
    street = company_props.get("address", "").strip()[:255]
    city = company_props.get("city", "").strip()[:50]
    state = company_props.get("state", "").strip()[:50]
    zip_code = company_props.get("zip", "").strip()[:10]
    country = company_props.get("country", "").strip()[:2].upper()

    # Default to US if no country
    if not country:
        country = "US"

    # Build account dict
    account = {
        "CompanyName": company_name,
        "Industry": industry,
        "Address": {"CountryCode": country},
    }

    # Add optional fields
    if website:
        account["WebsiteUrl"] = website
    if city:
        account["Address"]["City"] = city
    if state:
        account["Address"]["StateOrRegion"] = state
    if zip_code:
        account["Address"]["PostalCode"] = zip_code
    if street:
        account["Address"]["StreetAddress"] = street

    return account


# Lambda entry point
def lambda_handler(event: dict, context: dict) -> dict:
    """
    Lambda entry point for company sync handler.

    Args:
        event: API Gateway event with webhook payload
        context: Lambda context

    Returns:
        HTTP response with status and details
    """
    handler = CompanySyncHandler()
    return handler.handle(event, context)
