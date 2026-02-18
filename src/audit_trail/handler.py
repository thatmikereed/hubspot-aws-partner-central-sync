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
import uuid
from datetime import datetime
from typing import Dict, Any, Optional

from common.base_handler import BaseLambdaHandler


class AuditTrailHandler(BaseLambdaHandler):
    """Handler for audit trail operations."""

    def _execute(self, event: dict, context: dict) -> dict:
        """
        Handle audit trail operations.

        Args:
            event: API Gateway event
            context: Lambda context

        Returns:
            HTTP response
        """
        path = event.get("path", "")
        http_method = event.get("httpMethod", "")

        if http_method == "GET" and "/audit-trail/" in path:
            opportunity_id = path.split("/audit-trail/")[-1].split("?")[0]
            query_params = event.get("queryStringParameters") or {}
            return self._get_audit_trail(opportunity_id, query_params)
        elif http_method == "POST" and "/audit-trail" in path:
            body = json.loads(event.get("body", "{}"))
            return self._log_audit_entry(body)
        else:
            return self._error_response("Unknown operation", 400)

    def log_sync_operation(
        self,
        opportunity_id: str,
        action: str,
        source: str,
        user: str,
        changes: Dict[str, Dict[str, Any]],
        success: bool,
        metadata: Optional[Dict] = None,
        error: Optional[str] = None,
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

        self.logger.info(
            f"Audit entry logged: {action} on {opportunity_id} by {user} - success={success}"
        )

        return entry_id

    def _get_audit_trail(self, opportunity_id: str, query_params: Dict) -> dict:
        """
        Retrieve audit trail for an opportunity.

        Args:
            opportunity_id: Partner Central opportunity ID
            query_params: Query parameters (limit, startDate, endDate, action)

        Returns:
            HTTP response with audit entries
        """
        limit = int(query_params.get("limit", 50))

        self.logger.info(f"Retrieving audit trail for {opportunity_id} (limit={limit})")

        entries = [
            {
                "timestamp": "2026-02-18T10:00:00Z",
                "action": "UPDATE_OPPORTUNITY",
                "source": "HUBSPOT",
                "user": "sales-rep@partner.com",
                "changes": {
                    "dealstage": {"old": "Qualified", "new": "Presentation Scheduled"}
                },
                "success": True,
                "metadata": {"dealId": "12345", "syncDurationMs": 1250},
            }
        ]

        return self._success_response(
            {
                "opportunityId": opportunity_id,
                "entries": entries,
                "count": len(entries),
                "note": "This is a simplified implementation. Full implementation requires DynamoDB table setup.",
            }
        )

    def _log_audit_entry(self, body: dict) -> dict:
        """
        Manually log an audit entry (for testing or external integrations).

        Args:
            body: Audit entry data

        Returns:
            HTTP response
        """
        required_fields = ["opportunityId", "action", "source", "user"]
        if not all(field in body for field in required_fields):
            return self._error_response(
                f"Missing required fields: {required_fields}", 400
            )

        entry_id = self.log_sync_operation(
            opportunity_id=body["opportunityId"],
            action=body["action"],
            source=body["source"],
            user=body["user"],
            changes=body.get("changes", {}),
            success=body.get("success", True),
            metadata=body.get("metadata"),
            error=body.get("error"),
        )

        return self._success_response(
            {"message": "Audit entry logged", "entryId": entry_id}
        )

    def get_compliance_report(
        self, opportunity_id: str, start_date: str, end_date: str
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
        entries = []

        report = {
            "opportunityId": opportunity_id,
            "reportPeriod": {"start": start_date, "end": end_date},
            "totalOperations": len(entries),
            "successfulOperations": len([e for e in entries if e.get("success")]),
            "failedOperations": len([e for e in entries if not e.get("success")]),
            "operationsBySource": {
                "HUBSPOT": len([e for e in entries if e.get("source") == "HUBSPOT"]),
                "PARTNER_CENTRAL": len(
                    [e for e in entries if e.get("source") == "PARTNER_CENTRAL"]
                ),
            },
            "operationsByAction": {},
            "uniqueUsers": list(set(e.get("user") for e in entries if e.get("user"))),
            "firstOperation": entries[0].get("timestamp") if entries else None,
            "lastOperation": entries[-1].get("timestamp") if entries else None,
        }

        for entry in entries:
            action = entry.get("action", "UNKNOWN")
            report["operationsByAction"][action] = (
                report["operationsByAction"].get(action, 0) + 1
            )

        return report


def lambda_handler(event: dict, context: dict) -> dict:
    """Lambda handler entry point."""
    handler = AuditTrailHandler()
    return handler.handle(event, context)
