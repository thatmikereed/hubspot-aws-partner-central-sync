"""
Lambda handler for advanced resource management with AWS Partner Central.

This handler provides:
1. Upload partner resources to Partner Central
2. Associate resources with opportunities
3. List resources for an opportunity
4. Disassociate resources

Triggers:
- API Gateway POST /resources/upload
- API Gateway GET /resources/{opportunityId}
- API Gateway POST /resources/associate
- API Gateway DELETE /resources/disassociate
"""

import json
from datetime import datetime

from common.base_handler import BaseLambdaHandler

# Allowed resource types
RESOURCE_TYPES = [
    "Case Study",
    "Whitepaper",
    "Solution Brief",
    "Reference Architecture",
    "Technical Documentation",
    "Presentation",
    "Video",
    "Training Material",
    "Custom",
]

# Resource type to emoji mapping
RESOURCE_EMOJI = {
    "Case Study": "📄",
    "Whitepaper": "📃",
    "Solution Brief": "📋",
    "Reference Architecture": "🏗️",
    "Technical Documentation": "📚",
    "Presentation": "📊",
    "Video": "🎥",
    "Training Material": "🎓",
    "Custom": "📎",
}


class ResourceManagementHandler(BaseLambdaHandler):
    """Handler for resource management operations."""

    def _execute(self, event: dict, context: dict) -> dict:
        """
        Handle resource management operations.

        Args:
            event: API Gateway event
            context: Lambda context

        Returns:
            HTTP response
        """
        path = event.get("path", "")
        http_method = event.get("httpMethod", "")
        body = json.loads(event.get("body", "{}")) if event.get("body") else {}

        self.logger.info(f"Resource operation: {http_method} {path}")

        if http_method == "POST" and "/upload" in path:
            return self._handle_upload_resource(body)
        elif http_method == "GET" and "/resources/" in path:
            opportunity_id = path.split("/")[-1]
            return self._handle_list_resources(opportunity_id)
        elif http_method == "POST" and "/associate" in path:
            return self._handle_associate_resource(body)
        elif http_method == "DELETE" and "/disassociate" in path:
            return self._handle_disassociate_resource(body)
        else:
            return self._error_response("Unknown operation", 400)

    def _handle_upload_resource(self, body: dict) -> dict:
        """
        Handle partner resource upload to Partner Central.

        Args:
            body: Request body with dealId, resourceType, title, description, url, tags

        Returns:
            HTTP response
        """
        deal_id = body.get("dealId")
        resource_type = body.get("resourceType")
        title = body.get("title")
        description = body.get("description", "")
        url = body.get("url")
        tags = body.get("tags", [])

        if not all([deal_id, resource_type, title, url]):
            return self._error_response(
                "Missing required fields: dealId, resourceType, title, url", 400
            )

        if resource_type not in RESOURCE_TYPES:
            return self._error_response(
                f"Invalid resource type. Allowed: {RESOURCE_TYPES}", 400
            )

        if not url.startswith("http"):
            return self._error_response("URL must start with http or https", 400)

        self.logger.info(f"Uploading resource '{title}' for deal {deal_id}")

        deal = self.hubspot_client.get_deal(deal_id)
        if not deal:
            return self._error_response("Deal not found", 404)

        opportunity_id = deal.get("properties", {}).get("aws_opportunity_id")
        if not opportunity_id:
            return self._error_response("Deal has no AWS opportunity", 400)

        try:
            snapshot_response = self.pc_client.create_resource_snapshot(
                Catalog="AWS",
                ResourceType=resource_type,
                Name=title[:255],
                Description=description[:1000] if description else None,
                Url=url[:500],
                Tags=tags[:10] if tags else None,
            )

            resource_id = snapshot_response.get("Id")
            self.logger.info(f"Created resource snapshot: {resource_id}")

        except Exception as e:
            self.logger.error(f"Failed to create resource snapshot: {e}")
            return self._error_response(f"Resource creation failed: {str(e)}", 500)

        try:
            self.pc_client.associate_opportunity(
                Catalog="AWS",
                OpportunityIdentifier=opportunity_id,
                RelatedEntityIdentifier=resource_id,
                RelatedEntityType="Resources",
            )

            self.logger.info(
                f"Associated resource {resource_id} with opportunity {opportunity_id}"
            )

        except Exception as e:
            self.logger.error(f"Failed to associate resource: {e}")
            return self._error_response(f"Resource association failed: {str(e)}", 500)

        emoji = RESOURCE_EMOJI.get(resource_type, "📎")
        note_text = f"""{emoji} Partner Resource Uploaded

**Type:** {resource_type}
**Title:** {title}
**URL:** {url}

This resource has been uploaded to AWS Partner Central and associated with the opportunity.

*Uploaded on {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}*"""

        self.hubspot_client.create_deal_note(deal_id, note_text)

        properties = deal.get("properties", {})
        partner_resources = properties.get("aws_partner_resources", "")
        if partner_resources:
            resources_list = json.loads(partner_resources)
        else:
            resources_list = []

        resources_list.append(
            {
                "id": resource_id,
                "type": resource_type,
                "title": title,
                "url": url,
                "uploadedAt": datetime.utcnow().isoformat() + "Z",
            }
        )

        self.hubspot_client.update_deal(
            deal_id,
            {
                "aws_partner_resources": json.dumps(resources_list),
                "aws_last_resource_upload": self.hubspot_client.now_timestamp_ms(),
                "aws_total_resources": len(resources_list),
            },
        )

        return self._success_response(
            {
                "message": "Resource uploaded successfully",
                "resourceId": resource_id,
                "opportunityId": opportunity_id,
                "dealId": deal_id,
            }
        )

    def _handle_list_resources(self, opportunity_id: str) -> dict:
        """
        List all resources for an opportunity.

        Args:
            opportunity_id: Partner Central opportunity ID

        Returns:
            HTTP response with resource list
        """
        self.logger.info(f"Listing resources for opportunity {opportunity_id}")

        try:
            associations = self.pc_client.list_engagement_resource_associations(
                Catalog="AWS", OpportunityIdentifier=opportunity_id
            )

            resources = associations.get("ResourceAssociationList", [])

        except Exception as e:
            self.logger.error(f"Failed to list resources: {e}")
            return self._error_response(f"Failed to list resources: {str(e)}", 500)

        resource_list = []
        for resource in resources:
            resource_list.append(
                {
                    "id": resource.get("ResourceId"),
                    "type": resource.get("ResourceType"),
                    "title": resource.get("Name"),
                    "description": resource.get("Description"),
                    "url": resource.get("Url"),
                    "source": resource.get("Source", "Partner"),
                    "createdDate": resource.get("CreatedDate"),
                }
            )

        return self._success_response(
            {
                "opportunityId": opportunity_id,
                "resources": resource_list,
                "count": len(resource_list),
            }
        )

    def _handle_associate_resource(self, body: dict) -> dict:
        """Associate an existing resource with an opportunity."""
        deal_id = body.get("dealId")
        resource_id = body.get("resourceId")

        if not deal_id or not resource_id:
            return self._error_response("Missing dealId or resourceId", 400)

        deal = self.hubspot_client.get_deal(deal_id)

        if not deal:
            return self._error_response("Deal not found", 404)

        opportunity_id = deal.get("properties", {}).get("aws_opportunity_id")
        if not opportunity_id:
            return self._error_response("Deal has no AWS opportunity", 400)

        self.pc_client.associate_opportunity(
            Catalog="AWS",
            OpportunityIdentifier=opportunity_id,
            RelatedEntityIdentifier=resource_id,
            RelatedEntityType="Resources",
        )

        self.logger.info(
            f"Associated resource {resource_id} with opportunity {opportunity_id}"
        )

        return self._success_response(
            {
                "message": "Resource associated successfully",
                "resourceId": resource_id,
                "opportunityId": opportunity_id,
            }
        )

    def _handle_disassociate_resource(self, body: dict) -> dict:
        """Disassociate a resource from an opportunity."""
        deal_id = body.get("dealId")
        resource_id = body.get("resourceId")

        if not deal_id or not resource_id:
            return self._error_response("Missing dealId or resourceId", 400)

        deal = self.hubspot_client.get_deal(deal_id)

        if not deal:
            return self._error_response("Deal not found", 404)

        opportunity_id = deal.get("properties", {}).get("aws_opportunity_id")
        if not opportunity_id:
            return self._error_response("Deal has no AWS opportunity", 400)

        self.pc_client.disassociate_opportunity(
            Catalog="AWS",
            OpportunityIdentifier=opportunity_id,
            RelatedEntityIdentifier=resource_id,
            RelatedEntityType="Resources",
        )

        self.logger.info(
            f"Disassociated resource {resource_id} from opportunity {opportunity_id}"
        )

        return self._success_response(
            {
                "message": "Resource disassociated successfully",
                "resourceId": resource_id,
                "opportunityId": opportunity_id,
            }
        )


def lambda_handler(event: dict, context: dict) -> dict:
    """Lambda handler entry point."""
    handler = ResourceManagementHandler()
    return handler.handle(event, context)
