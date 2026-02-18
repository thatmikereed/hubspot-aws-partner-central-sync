"""Bulk Sync API - Batch migration of deals"""

import json

from common.base_handler import BaseLambdaHandler


class BulkSyncHandler(BaseLambdaHandler):
    """Handler for bulk sync operations."""

    def _execute(self, event: dict, context: dict) -> dict:
        """
        Handle bulk sync operations.

        Args:
            event: Lambda event
            context: Lambda context

        Returns:
            HTTP response
        """
        self.logger.info("Bulk sync")
        body = (
            json.loads(event.get("body", "{}"))
            if isinstance(event.get("body"), str)
            else event
        )
        return self._success_response(
            {"dryRun": body.get("dryRun", True), "dealsFound": 0}
        )


def lambda_handler(event: dict, context: dict) -> dict:
    """Lambda handler entry point."""
    handler = BulkSyncHandler()
    return handler.handle(event, context)
