"""
Lambda handler for permanent audit trail storage and retrieval.

This handler provides:
1. Permanent audit log storage in DynamoDB
2. API to query audit trail
3. Compliance reporting
4. Change history tracking

Note: Requires DynamoDB table 'hubspot-pc-audit-trail' with:
- PK: opportunity ID
- SK: timestamp#action#sync-id
- Attributes: action, source, user, changes, success, metadata

This is a simplified implementation showing the core concepts.
"""

import json
import logging
import os
import uuid
from datetime import datetime
from typing import Dict, Any, Optional

logger = logging.getLogger()
logger.setLevel(os.getenv("LOG_LEVEL", "INFO"))


def lambda_handler(event: dict, context: dict) -> dict:
    """
    Handle audit trail operations.
    
    Args:
        event: API Gateway event
        context: Lambda context
        
    Returns:
        HTTP response
    """
    try:
        path = event.get("path", "")
        http_method = event.get("httpMethod", "")
        
        if http_method == "GET" and "/audit-trail/" in path:
            # Extract opportunity ID from path
            opportunity_id = path.split("/audit-trail/")[-1].split("?")[0]
            query_params = event.get("queryStringParameters") or {}
            return _get_audit_trail(opportunity_id, query_params)
        elif http_method == "POST" and "/audit-trail" in path:
            body = json.loads(event.get("body", "{}"))
            return _log_audit_entry(body)
        else:
            return {
                "statusCode": 400,
                "body": json.dumps({"error": "Unknown operation"})
            }
            
    except Exception as e:
        logger.error(f"Error in audit trail handler: {e}", exc_info=True)
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e)})
        }


def log_sync_operation(
    opportunity_id: str,
    action: str,
    source: str,
    user: str,
    changes: Dict[str, Dict[str, Any]],
    success: bool,
    metadata: Optional[Dict] = None,
    error: Optional[str] = None
) -> str:
    """
    Log a sync operation to the audit trail.
    
    Args:
        opportunity_id: Partner Central opportunity ID
        action: Action performed (e.g., "UPDATE_OPPORTUNITY", "CREATE_DEAL")
        source: Source system ("HUBSPOT" or "PARTNER_CENTRAL")
        user: User who triggered the action
        changes: Dict of field changes {field: {"old": ..., "new": ...}}
        success: Whether the operation succeeded
        metadata: Additional metadata
        error: Error message if operation failed
        
    Returns:
        Audit entry ID
    """
    entry_id = str(uuid.uuid4())
    timestamp = datetime.utcnow().isoformat() + "Z"
    
    # Build audit entry
    entry = {
        "PK": opportunity_id,
        "SK": f"{timestamp}#{action}#{entry_id}",
        "entryId": entry_id,
        "timestamp": timestamp,
        "action": action,
        "source": source,
        "user": user,
        "changes": changes,
        "success": success,
        "metadata": metadata or {},
    }
    
    if error:
        entry["error"] = error
    
    # In full implementation, would write to DynamoDB:
    # dynamodb.put_item(
    #     TableName="hubspot-pc-audit-trail",
    #     Item=entry
    # )
    
    logger.info(f"Audit entry logged: {action} on {opportunity_id} by {user} - success={success}")
    
    return entry_id


def _get_audit_trail(opportunity_id: str, query_params: Dict) -> dict:
    """
    Retrieve audit trail for an opportunity.
    
    Args:
        opportunity_id: Partner Central opportunity ID
        query_params: Query parameters (limit, startDate, endDate, action)
        
    Returns:
        HTTP response with audit entries
    """
    try:
        limit = int(query_params.get("limit", 50))
        start_date = query_params.get("startDate")
        end_date = query_params.get("endDate")
        action_filter = query_params.get("action")
        
        logger.info(f"Retrieving audit trail for {opportunity_id} (limit={limit})")
        
        # In full implementation, would query DynamoDB:
        # response = dynamodb.query(
        #     TableName="hubspot-pc-audit-trail",
        #     KeyConditionExpression="PK = :pk",
        #     ExpressionAttributeValues={":pk": opportunity_id},
        #     Limit=limit,
        #     ScanIndexForward=False  # Most recent first
        # )
        # entries = response["Items"]
        
        # Filter by date range and action if specified
        # if start_date:
        #     entries = [e for e in entries if e["timestamp"] >= start_date]
        # if end_date:
        #     entries = [e for e in entries if e["timestamp"] <= end_date]
        # if action_filter:
        #     entries = [e for e in entries if e["action"] == action_filter]
        
        # For this simplified implementation, return example structure
        entries = [
            {
                "timestamp": "2026-02-18T10:00:00Z",
                "action": "UPDATE_OPPORTUNITY",
                "source": "HUBSPOT",
                "user": "sales-rep@partner.com",
                "changes": {
                    "dealstage": {
                        "old": "Qualified",
                        "new": "Presentation Scheduled"
                    }
                },
                "success": True,
                "metadata": {
                    "dealId": "12345",
                    "syncDurationMs": 1250
                }
            }
        ]
        
        return {
            "statusCode": 200,
            "body": json.dumps({
                "opportunityId": opportunity_id,
                "entries": entries,
                "count": len(entries),
                "note": "This is a simplified implementation. Full implementation requires DynamoDB table setup."
            })
        }
        
    except Exception as e:
        logger.error(f"Error retrieving audit trail: {e}", exc_info=True)
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e)})
        }


def _log_audit_entry(body: dict) -> dict:
    """
    Manually log an audit entry (for testing or external integrations).
    
    Args:
        body: Audit entry data
        
    Returns:
        HTTP response
    """
    try:
        required_fields = ["opportunityId", "action", "source", "user"]
        if not all(field in body for field in required_fields):
            return {
                "statusCode": 400,
                "body": json.dumps({
                    "error": f"Missing required fields: {required_fields}"
                })
            }
        
        entry_id = log_sync_operation(
            opportunity_id=body["opportunityId"],
            action=body["action"],
            source=body["source"],
            user=body["user"],
            changes=body.get("changes", {}),
            success=body.get("success", True),
            metadata=body.get("metadata"),
            error=body.get("error")
        )
        
        return {
            "statusCode": 200,
            "body": json.dumps({
                "message": "Audit entry logged",
                "entryId": entry_id
            })
        }
        
    except Exception as e:
        logger.error(f"Error logging audit entry: {e}", exc_info=True)
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e)})
        }


def get_compliance_report(
    opportunity_id: str,
    start_date: str,
    end_date: str
) -> Dict:
    """
    Generate compliance report for an opportunity.
    
    Args:
        opportunity_id: Partner Central opportunity ID
        start_date: Start date (ISO 8601)
        end_date: End date (ISO 8601)
        
    Returns:
        Compliance report dict
    """
    # Query audit trail for date range
    # In full implementation:
    # entries = query_dynamodb_date_range(opportunity_id, start_date, end_date)
    
    entries = []  # Simplified
    
    # Aggregate statistics
    report = {
        "opportunityId": opportunity_id,
        "reportPeriod": {
            "start": start_date,
            "end": end_date
        },
        "totalOperations": len(entries),
        "successfulOperations": len([e for e in entries if e.get("success")]),
        "failedOperations": len([e for e in entries if not e.get("success")]),
        "operationsBySource": {
            "HUBSPOT": len([e for e in entries if e.get("source") == "HUBSPOT"]),
            "PARTNER_CENTRAL": len([e for e in entries if e.get("source") == "PARTNER_CENTRAL"])
        },
        "operationsByAction": {},
        "uniqueUsers": list(set(e.get("user") for e in entries if e.get("user"))),
        "firstOperation": entries[0].get("timestamp") if entries else None,
        "lastOperation": entries[-1].get("timestamp") if entries else None
    }
    
    # Count by action type
    for entry in entries:
        action = entry.get("action", "UNKNOWN")
        report["operationsByAction"][action] = report["operationsByAction"].get(action, 0) + 1
    
    return report


# Example usage in sync operations:
def example_audit_logging():
    """
    Example of how audit logging would be integrated into sync operations.
    """
    # At the start of any sync operation:
    start_time = datetime.utcnow()
    
    # Track changes
    changes = {}
    
    try:
        # Perform sync operation
        old_value = "Qualified"
        new_value = "Presentation Scheduled"
        
        changes["dealstage"] = {
            "old": old_value,
            "new": new_value
        }
        
        # Update system
        # pc_client.update_opportunity(...)
        
        # Log success
        log_sync_operation(
            opportunity_id="opp-12345",
            action="UPDATE_OPPORTUNITY",
            source="HUBSPOT",
            user="sales-rep@partner.com",
            changes=changes,
            success=True,
            metadata={
                "dealId": "12345",
                "syncDurationMs": int((datetime.utcnow() - start_time).total_seconds() * 1000),
                "triggeredBy": "deal.propertyChange webhook"
            }
        )
        
    except Exception as e:
        # Log failure
        log_sync_operation(
            opportunity_id="opp-12345",
            action="UPDATE_OPPORTUNITY",
            source="HUBSPOT",
            user="sales-rep@partner.com",
            changes=changes,
            success=False,
            metadata={
                "dealId": "12345",
                "syncDurationMs": int((datetime.utcnow() - start_time).total_seconds() * 1000)
            },
            error=str(e)
        )
        raise


# DynamoDB Table Schema (for reference):
"""
Table: hubspot-pc-audit-trail

Primary Key:
- PK (String): Opportunity ID (e.g., "opp-12345")
- SK (String): Timestamp#Action#EntryID (e.g., "2026-02-18T10:00:00Z#UPDATE_OPPORTUNITY#uuid")

Attributes:
- entryId (String): Unique entry ID
- timestamp (String): ISO 8601 timestamp
- action (String): Action performed
- source (String): HUBSPOT or PARTNER_CENTRAL
- user (String): User email or system identifier
- changes (Map): Field changes {"field": {"old": ..., "new": ...}}
- success (Boolean): Operation success status
- metadata (Map): Additional metadata
- error (String): Error message if failed
- ttl (Number): Optional TTL for automatic deletion

GSI (for compliance reporting):
- GSI1-PK: "ALL_ENTRIES"
- GSI1-SK: timestamp (for date range queries across all opportunities)

Point-in-time recovery: Enabled
Encryption: AWS KMS
"""
