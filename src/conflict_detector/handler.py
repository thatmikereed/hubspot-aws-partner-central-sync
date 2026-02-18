"""
Lambda handler for conflict detection in bidirectional sync.

This handler detects conflicts when both HubSpot and Partner Central
update the same field simultaneously. It provides:
1. Conflict detection logic
2. Automatic resolution based on configured strategy
3. Manual conflict resolution queue
4. Version tracking for optimistic locking

Note: Full implementation requires:
- DynamoDB table for conflict storage
- Version tracking on all opportunities
- Pre/post hooks in all sync operations
- Conflict resolution UI/API

This is a simplified implementation showing the core logic.
"""

import json
import logging
import os
from datetime import datetime
from typing import Optional, Dict, Any

logger = logging.getLogger()
logger.setLevel(os.getenv("LOG_LEVEL", "INFO"))

# Conflict resolution strategies
STRATEGY_LAST_WRITE_WINS = "LAST_WRITE_WINS"
STRATEGY_HUBSPOT_WINS = "HUBSPOT_WINS"
STRATEGY_PARTNER_CENTRAL_WINS = "PARTNER_CENTRAL_WINS"
STRATEGY_MANUAL = "MANUAL"

# Field-specific strategies (can be configured via environment)
FIELD_STRATEGIES = {
    "dealstage": STRATEGY_HUBSPOT_WINS,
    "aws_review_status": STRATEGY_PARTNER_CENTRAL_WINS,
    "amount": STRATEGY_MANUAL,
    "closedate": STRATEGY_MANUAL
}


def lambda_handler(event: dict, context: dict) -> dict:
    """
    Handle conflict detection and resolution.
    
    This is a simplified implementation. Full implementation would:
    - Hook into all sync operations
    - Store conflicts in DynamoDB
    - Provide API for conflict resolution
    - Track version numbers for optimistic locking
    
    Args:
        event: Conflict detection event or API request
        context: Lambda context
        
    Returns:
        Conflict detection/resolution result
    """
    try:
        path = event.get("path", "")
        
        if "/conflicts/pending" in path:
            return _list_pending_conflicts()
        elif "/conflicts/resolve" in path:
            body = json.loads(event.get("body", "{}"))
            return _resolve_conflict(body)
        else:
            return {
                "statusCode": 400,
                "body": json.dumps({
                    "error": "Unknown operation",
                    "note": "This is a simplified conflict detection implementation"
                })
            }
            
    except Exception as e:
        logger.error(f"Error in conflict handler: {e}", exc_info=True)
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e)})
        }


def detect_conflict(
    field_name: str,
    local_value: Any,
    local_timestamp: str,
    remote_value: Any,
    remote_timestamp: str,
    last_sync_timestamp: str
) -> Optional[Dict]:
    """
    Detect if a conflict exists between local and remote values.
    
    Args:
        field_name: Name of the field
        local_value: Local (HubSpot) value
        local_timestamp: When local value was updated
        remote_value: Remote (Partner Central) value
        remote_timestamp: When remote value was updated
        last_sync_timestamp: Last successful sync timestamp
        
    Returns:
        Conflict dict if conflict detected, None otherwise
    """
    # Parse timestamps
    try:
        local_dt = datetime.fromisoformat(local_timestamp.replace("Z", "+00:00"))
        remote_dt = datetime.fromisoformat(remote_timestamp.replace("Z", "+00:00"))
        last_sync_dt = datetime.fromisoformat(last_sync_timestamp.replace("Z", "+00:00"))
    except Exception as e:
        logger.error(f"Failed to parse timestamps: {e}")
        return None
    
    # No conflict if values are the same
    if local_value == remote_value:
        return None
    
    # No conflict if only one side changed since last sync
    local_changed = local_dt > last_sync_dt
    remote_changed = remote_dt > last_sync_dt
    
    if local_changed and not remote_changed:
        # Only local changed, no conflict
        return None
    
    if remote_changed and not local_changed:
        # Only remote changed, no conflict
        return None
    
    if not local_changed and not remote_changed:
        # Neither changed since last sync, no conflict
        return None
    
    # Both changed - conflict detected
    conflict = {
        "field": field_name,
        "localValue": local_value,
        "localTimestamp": local_timestamp,
        "remoteValue": remote_value,
        "remoteTimestamp": remote_timestamp,
        "lastSyncTimestamp": last_sync_timestamp,
        "detectedAt": datetime.utcnow().isoformat() + "Z",
        "status": "PENDING"
    }
    
    logger.warning(f"Conflict detected: {field_name} - local={local_value}, remote={remote_value}")
    
    return conflict


def resolve_conflict_automatically(conflict: Dict) -> Optional[Dict]:
    """
    Attempt to resolve a conflict automatically based on configured strategy.
    
    Args:
        conflict: Conflict dict from detect_conflict()
        
    Returns:
        Resolution dict with winning value, or None if manual resolution required
    """
    field_name = conflict["field"]
    
    # Get strategy for this field
    strategy = FIELD_STRATEGIES.get(
        field_name,
        os.getenv("DEFAULT_CONFLICT_STRATEGY", STRATEGY_LAST_WRITE_WINS)
    )
    
    if strategy == STRATEGY_MANUAL:
        logger.info(f"Manual resolution required for {field_name}")
        return None
    
    if strategy == STRATEGY_HUBSPOT_WINS:
        winner = "HUBSPOT"
        winning_value = conflict["localValue"]
    elif strategy == STRATEGY_PARTNER_CENTRAL_WINS:
        winner = "PARTNER_CENTRAL"
        winning_value = conflict["remoteValue"]
    else:  # LAST_WRITE_WINS
        # Compare timestamps
        local_ts = conflict["localTimestamp"]
        remote_ts = conflict["remoteTimestamp"]
        
        if local_ts > remote_ts:
            winner = "HUBSPOT"
            winning_value = conflict["localValue"]
        else:
            winner = "PARTNER_CENTRAL"
            winning_value = conflict["remoteValue"]
    
    resolution = {
        "conflictField": field_name,
        "strategy": strategy,
        "winner": winner,
        "winningValue": winning_value,
        "resolvedAt": datetime.utcnow().isoformat() + "Z",
        "automatic": True
    }
    
    logger.info(f"Conflict auto-resolved: {field_name} - winner={winner}, strategy={strategy}")
    
    return resolution


def _list_pending_conflicts() -> dict:
    """
    List pending conflicts requiring manual resolution.
    
    Note: Full implementation would query DynamoDB table.
    """
    logger.info("Listing pending conflicts (simplified implementation)")
    
    # In full implementation, would query DynamoDB:
    # conflicts = dynamodb.query(
    #     TableName="hubspot-pc-conflicts",
    #     IndexName="status-index",
    #     KeyConditionExpression="status = :pending",
    #     ExpressionAttributeValues={":pending": "PENDING"}
    # )
    
    return {
        "statusCode": 200,
        "body": json.dumps({
            "conflicts": [],
            "count": 0,
            "note": "This is a simplified implementation. Full implementation requires DynamoDB table setup."
        })
    }


def _resolve_conflict(body: dict) -> dict:
    """
    Manually resolve a conflict.
    
    Args:
        body: Request body with conflictId, resolution, reason
    """
    conflict_id = body.get("conflictId")
    resolution = body.get("resolution")  # USE_HUBSPOT_VALUE or USE_PARTNER_CENTRAL_VALUE
    reason = body.get("reason", "")
    
    if not conflict_id or not resolution:
        return {
            "statusCode": 400,
            "body": json.dumps({"error": "Missing conflictId or resolution"})
        }
    
    logger.info(f"Manual conflict resolution: {conflict_id} - {resolution}")
    
    # In full implementation, would:
    # 1. Get conflict from DynamoDB
    # 2. Apply the resolution
    # 3. Update both systems with winning value
    # 4. Mark conflict as resolved in DynamoDB
    # 5. Log to audit trail
    
    return {
        "statusCode": 200,
        "body": json.dumps({
            "message": "Conflict resolved",
            "conflictId": conflict_id,
            "resolution": resolution,
            "note": "This is a simplified implementation."
        })
    }


# Example usage in sync operations:
def example_sync_with_conflict_detection():
    """
    Example of how conflict detection would be integrated into sync operations.
    """
    # Before updating Partner Central:
    # 1. Get current PC value and timestamp
    # 2. Get HubSpot value and timestamp
    # 3. Get last sync timestamp from HubSpot property
    # 4. Detect conflict
    # 5. Resolve automatically if possible
    # 6. Queue for manual resolution if needed
    # 7. Proceed with sync only if no unresolved conflicts
    
    # Pseudo-code:
    """
    conflict = detect_conflict(
        field_name="dealstage",
        local_value=hubspot_stage,
        local_timestamp=hubspot_updated_at,
        remote_value=pc_stage,
        remote_timestamp=pc_updated_at,
        last_sync_timestamp=last_sync
    )
    
    if conflict:
        resolution = resolve_conflict_automatically(conflict)
        if resolution:
            # Use winning value
            value_to_sync = resolution["winningValue"]
        else:
            # Queue for manual resolution
            store_conflict_in_dynamodb(conflict)
            create_hubspot_task_for_resolution(conflict)
            return  # Don't sync until resolved
    else:
        # No conflict, proceed with sync
        value_to_sync = local_value
    
    # Perform the sync
    pc_client.update_opportunity(...)
    """
    pass
