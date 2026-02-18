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
import logging
import os
from datetime import datetime
from typing import Optional

logger = logging.getLogger()
logger.setLevel(os.getenv("LOG_LEVEL", "INFO"))

from common.hubspot_client import HubSpotClient
from common.aws_client import get_partner_central_client


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
    "Custom"
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
    "Custom": "📎"
}


def lambda_handler(event: dict, context: dict) -> dict:
    """
    Handle resource management operations.
    
    Args:
        event: API Gateway event
        context: Lambda context
        
    Returns:
        HTTP response
    """
    try:
        path = event.get("path", "")
        http_method = event.get("httpMethod", "")
        body = json.loads(event.get("body", "{}")) if event.get("body") else {}
        
        logger.info(f"Resource operation: {http_method} {path}")
        
        # Route to appropriate handler
        if http_method == "POST" and "/upload" in path:
            return _handle_upload_resource(body)
        elif http_method == "GET" and "/resources/" in path:
            opportunity_id = path.split("/")[-1]
            return _handle_list_resources(opportunity_id)
        elif http_method == "POST" and "/associate" in path:
            return _handle_associate_resource(body)
        elif http_method == "DELETE" and "/disassociate" in path:
            return _handle_disassociate_resource(body)
        else:
            return {
                "statusCode": 400,
                "body": json.dumps({"error": "Unknown operation"})
            }
            
    except Exception as e:
        logger.error(f"Fatal error in resource handler: {e}", exc_info=True)
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e)})
        }


def _handle_upload_resource(body: dict) -> dict:
    """
    Handle partner resource upload to Partner Central.
    
    Args:
        body: Request body with dealId, resourceType, title, description, url, tags
        
    Returns:
        HTTP response
    """
    try:
        deal_id = body.get("dealId")
        resource_type = body.get("resourceType")
        title = body.get("title")
        description = body.get("description", "")
        url = body.get("url")
        tags = body.get("tags", [])
        
        # Validate required fields
        if not all([deal_id, resource_type, title, url]):
            return {
                "statusCode": 400,
                "body": json.dumps({
                    "error": "Missing required fields: dealId, resourceType, title, url"
                })
            }
        
        # Validate resource type
        if resource_type not in RESOURCE_TYPES:
            return {
                "statusCode": 400,
                "body": json.dumps({
                    "error": f"Invalid resource type. Allowed: {RESOURCE_TYPES}"
                })
            }
        
        # Validate URL
        if not url.startswith("http"):
            return {
                "statusCode": 400,
                "body": json.dumps({"error": "URL must start with http or https"})
            }
        
        logger.info(f"Uploading resource '{title}' for deal {deal_id}")
        
        # Initialize clients
        hubspot_client = HubSpotClient()
        pc_client = get_partner_central_client()
        
        # Get deal
        deal = hubspot_client.get_deal(deal_id)
        if not deal:
            return {
                "statusCode": 404,
                "body": json.dumps({"error": "Deal not found"})
            }
        
        # Get opportunity ID
        opportunity_id = deal.get("properties", {}).get("aws_opportunity_id")
        if not opportunity_id:
            return {
                "statusCode": 400,
                "body": json.dumps({"error": "Deal has no AWS opportunity"})
            }
        
        # Create resource snapshot in Partner Central
        try:
            snapshot_response = pc_client.create_resource_snapshot(
                Catalog="AWS",
                ResourceType=resource_type,
                Name=title[:255],
                Description=description[:1000] if description else None,
                Url=url[:500],
                Tags=tags[:10] if tags else None
            )
            
            resource_id = snapshot_response.get("Id")
            logger.info(f"Created resource snapshot: {resource_id}")
            
        except Exception as e:
            logger.error(f"Failed to create resource snapshot: {e}")
            return {
                "statusCode": 500,
                "body": json.dumps({"error": f"Resource creation failed: {str(e)}"})
            }
        
        # Associate resource with opportunity
        try:
            pc_client.associate_opportunity(
                Catalog="AWS",
                OpportunityIdentifier=opportunity_id,
                RelatedEntityIdentifier=resource_id,
                RelatedEntityType="Resources"
            )
            
            logger.info(f"Associated resource {resource_id} with opportunity {opportunity_id}")
            
        except Exception as e:
            logger.error(f"Failed to associate resource: {e}")
            return {
                "statusCode": 500,
                "body": json.dumps({"error": f"Resource association failed: {str(e)}"})
            }
        
        # Update HubSpot
        emoji = RESOURCE_EMOJI.get(resource_type, "📎")
        note_text = f"""{emoji} Partner Resource Uploaded

**Type:** {resource_type}
**Title:** {title}
**URL:** {url}

This resource has been uploaded to AWS Partner Central and associated with the opportunity.

*Uploaded on {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}*"""
        
        hubspot_client.create_deal_note(deal_id, note_text)
        
        # Update resource tracking property
        properties = deal.get("properties", {})
        partner_resources = properties.get("aws_partner_resources", "")
        if partner_resources:
            resources_list = json.loads(partner_resources)
        else:
            resources_list = []
        
        resources_list.append({
            "id": resource_id,
            "type": resource_type,
            "title": title,
            "url": url,
            "uploadedAt": datetime.utcnow().isoformat() + "Z"
        })
        
        hubspot_client.update_deal(deal_id, {
            "aws_partner_resources": json.dumps(resources_list),
            "aws_last_resource_upload": hubspot_client.now_timestamp_ms(),
            "aws_total_resources": len(resources_list)
        })
        
        return {
            "statusCode": 200,
            "body": json.dumps({
                "message": "Resource uploaded successfully",
                "resourceId": resource_id,
                "opportunityId": opportunity_id,
                "dealId": deal_id
            })
        }
        
    except Exception as e:
        logger.error(f"Error uploading resource: {e}", exc_info=True)
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e)})
        }


def _handle_list_resources(opportunity_id: str) -> dict:
    """
    List all resources for an opportunity.
    
    Args:
        opportunity_id: Partner Central opportunity ID
        
    Returns:
        HTTP response with resource list
    """
    try:
        logger.info(f"Listing resources for opportunity {opportunity_id}")
        
        pc_client = get_partner_central_client()
        
        # List resource associations
        try:
            associations = pc_client.list_engagement_resource_associations(
                Catalog="AWS",
                OpportunityIdentifier=opportunity_id
            )
            
            resources = associations.get("ResourceAssociationList", [])
            
        except Exception as e:
            logger.error(f"Failed to list resources: {e}")
            return {
                "statusCode": 500,
                "body": json.dumps({"error": f"Failed to list resources: {str(e)}"})
            }
        
        # Format resources
        resource_list = []
        for resource in resources:
            resource_list.append({
                "id": resource.get("ResourceId"),
                "type": resource.get("ResourceType"),
                "title": resource.get("Name"),
                "description": resource.get("Description"),
                "url": resource.get("Url"),
                "source": resource.get("Source", "Partner"),
                "createdDate": resource.get("CreatedDate")
            })
        
        return {
            "statusCode": 200,
            "body": json.dumps({
                "opportunityId": opportunity_id,
                "resources": resource_list,
                "count": len(resource_list)
            })
        }
        
    except Exception as e:
        logger.error(f"Error listing resources: {e}", exc_info=True)
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e)})
        }


def _handle_associate_resource(body: dict) -> dict:
    """Associate an existing resource with an opportunity."""
    try:
        deal_id = body.get("dealId")
        resource_id = body.get("resourceId")
        
        if not deal_id or not resource_id:
            return {
                "statusCode": 400,
                "body": json.dumps({"error": "Missing dealId or resourceId"})
            }
        
        # Get opportunity ID from deal
        hubspot_client = HubSpotClient()
        deal = hubspot_client.get_deal(deal_id)
        
        if not deal:
            return {
                "statusCode": 404,
                "body": json.dumps({"error": "Deal not found"})
            }
        
        opportunity_id = deal.get("properties", {}).get("aws_opportunity_id")
        if not opportunity_id:
            return {
                "statusCode": 400,
                "body": json.dumps({"error": "Deal has no AWS opportunity"})
            }
        
        # Associate resource
        pc_client = get_partner_central_client()
        pc_client.associate_opportunity(
            Catalog="AWS",
            OpportunityIdentifier=opportunity_id,
            RelatedEntityIdentifier=resource_id,
            RelatedEntityType="Resources"
        )
        
        logger.info(f"Associated resource {resource_id} with opportunity {opportunity_id}")
        
        return {
            "statusCode": 200,
            "body": json.dumps({
                "message": "Resource associated successfully",
                "resourceId": resource_id,
                "opportunityId": opportunity_id
            })
        }
        
    except Exception as e:
        logger.error(f"Error associating resource: {e}", exc_info=True)
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e)})
        }


def _handle_disassociate_resource(body: dict) -> dict:
    """Disassociate a resource from an opportunity."""
    try:
        deal_id = body.get("dealId")
        resource_id = body.get("resourceId")
        
        if not deal_id or not resource_id:
            return {
                "statusCode": 400,
                "body": json.dumps({"error": "Missing dealId or resourceId"})
            }
        
        # Get opportunity ID from deal
        hubspot_client = HubSpotClient()
        deal = hubspot_client.get_deal(deal_id)
        
        if not deal:
            return {
                "statusCode": 404,
                "body": json.dumps({"error": "Deal not found"})
            }
        
        opportunity_id = deal.get("properties", {}).get("aws_opportunity_id")
        if not opportunity_id:
            return {
                "statusCode": 400,
                "body": json.dumps({"error": "Deal has no AWS opportunity"})
            }
        
        # Disassociate resource
        pc_client = get_partner_central_client()
        pc_client.disassociate_opportunity(
            Catalog="AWS",
            OpportunityIdentifier=opportunity_id,
            RelatedEntityIdentifier=resource_id,
            RelatedEntityType="Resources"
        )
        
        logger.info(f"Disassociated resource {resource_id} from opportunity {opportunity_id}")
        
        return {
            "statusCode": 200,
            "body": json.dumps({
                "message": "Resource disassociated successfully",
                "resourceId": resource_id,
                "opportunityId": opportunity_id
            })
        }
        
    except Exception as e:
        logger.error(f"Error disassociating resource: {e}", exc_info=True)
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e)})
        }
