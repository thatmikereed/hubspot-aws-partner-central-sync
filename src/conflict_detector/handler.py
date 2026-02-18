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
import os
from datetime import datetime
from typing import Optional, Dict, Any

from common.base_handler import BaseLambdaHandler

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
    "closedate": STRATEGY_MANUAL,
}


class ConflictDetectorHandler(BaseLambdaHandler):
    """Handler for conflict detection and resolution."""

    def _execute(self, event: dict, context: dict) -> dict:
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
        path = event.get("path", "")

        if "/conflicts/pending" in path:
            return self._list_pending_conflicts()
        elif "/conflicts/resolve" in path:
            body = json.loads(event.get("body", "{}"))
            return self._resolve_conflict(body)
        else:
            return self._error_response(
                "Unknown operation. This is a simplified conflict detection implementation.",
                400,
            )

    def detect_conflict(
        self,
        field_name: str,
        local_value: Any,
        local_timestamp: str,
        remote_value: Any,
        remote_timestamp: str,
        last_sync_timestamp: str,
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
        try:
            local_dt = datetime.fromisoformat(local_timestamp.replace("Z", "+00:00"))
            remote_dt = datetime.fromisoformat(remote_timestamp.replace("Z", "+00:00"))
            last_sync_dt = datetime.fromisoformat(
                last_sync_timestamp.replace("Z", "+00:00")
            )
        except Exception as e:
            self.logger.error(f"Failed to parse timestamps: {e}")
            return None

        if local_value == remote_value:
            return None

        local_changed = local_dt > last_sync_dt
        remote_changed = remote_dt > last_sync_dt

        if local_changed and not remote_changed:
            return None

        if remote_changed and not local_changed:
            return None

        if not local_changed and not remote_changed:
            return None

        conflict = {
            "field": field_name,
            "localValue": local_value,
            "localTimestamp": local_timestamp,
            "remoteValue": remote_value,
            "remoteTimestamp": remote_timestamp,
            "lastSyncTimestamp": last_sync_timestamp,
            "detectedAt": datetime.utcnow().isoformat() + "Z",
            "status": "PENDING",
        }

        self.logger.warning(
            f"Conflict detected: {field_name} - local={local_value}, remote={remote_value}"
        )

        return conflict

    def resolve_conflict_automatically(self, conflict: Dict) -> Optional[Dict]:
        """
        Attempt to resolve a conflict automatically based on configured strategy.

        Args:
            conflict: Conflict dict from detect_conflict()

        Returns:
            Resolution dict with winning value, or None if manual resolution required
        """
        field_name = conflict["field"]

        strategy = FIELD_STRATEGIES.get(
            field_name, os.getenv("DEFAULT_CONFLICT_STRATEGY", STRATEGY_LAST_WRITE_WINS)
        )

        if strategy == STRATEGY_MANUAL:
            self.logger.info(f"Manual resolution required for {field_name}")
            return None

        if strategy == STRATEGY_HUBSPOT_WINS:
            winner = "HUBSPOT"
            winning_value = conflict["localValue"]
        elif strategy == STRATEGY_PARTNER_CENTRAL_WINS:
            winner = "PARTNER_CENTRAL"
            winning_value = conflict["remoteValue"]
        else:
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
            "automatic": True,
        }

        self.logger.info(
            f"Conflict auto-resolved: {field_name} - winner={winner}, strategy={strategy}"
        )

        return resolution

    def _list_pending_conflicts(self) -> dict:
        """
        List pending conflicts requiring manual resolution.

        Note: Full implementation would query DynamoDB table.
        """
        self.logger.info("Listing pending conflicts (simplified implementation)")

        return self._success_response(
            {
                "conflicts": [],
                "count": 0,
                "note": "This is a simplified implementation. Full implementation requires DynamoDB table setup.",
            }
        )

    def _resolve_conflict(self, body: dict) -> dict:
        """
        Manually resolve a conflict.

        Args:
            body: Request body with conflictId, resolution, reason
        """
        conflict_id = body.get("conflictId")
        resolution = body.get("resolution")

        if not conflict_id or not resolution:
            return self._error_response("Missing conflictId or resolution", 400)

        self.logger.info(f"Manual conflict resolution: {conflict_id} - {resolution}")

        return self._success_response(
            {
                "message": "Conflict resolved",
                "conflictId": conflict_id,
                "resolution": resolution,
                "note": "This is a simplified implementation.",
            }
        )


def lambda_handler(event: dict, context: dict) -> dict:
    """Lambda handler entry point."""
    handler = ConflictDetectorHandler()
    return handler.handle(event, context)
