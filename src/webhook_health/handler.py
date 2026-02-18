"""Webhook Health Check - Monitor webhook delivery"""


from common.base_handler import BaseLambdaHandler


class WebhookHealthHandler(BaseLambdaHandler):
    """Handler for webhook health checks."""

    def _execute(self, event: dict, context: dict) -> dict:
        """
        Handle webhook health check.

        Args:
            event: Lambda event
            context: Lambda context

        Returns:
            HTTP response
        """
        self.logger.info("Webhook health")
        return self._success_response({"status": "healthy"})


def lambda_handler(event: dict, context: dict) -> dict:
    """Lambda handler entry point."""
    handler = WebhookHealthHandler()
    return handler.handle(event, context)
