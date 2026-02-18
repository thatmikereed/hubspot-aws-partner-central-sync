"""Marketing Activities Sync - HubSpot campaigns to Partner Central"""


from common.base_handler import BaseLambdaHandler


class MarketingSyncHandler(BaseLambdaHandler):
    """Handler for syncing HubSpot marketing campaigns to Partner Central."""

    def _execute(self, event: dict, context: dict) -> dict:
        """Sync marketing activities from HubSpot to Partner Central."""
        self.logger.info("Marketing sync")
        return self._success_response({"status": "ok"})


def lambda_handler(event: dict, context) -> dict:
    """Lambda entry point."""
    handler = MarketingSyncHandler()
    return handler.handle(event, context)
