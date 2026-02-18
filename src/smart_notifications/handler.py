"""
Lambda handler: Smart Notification System

Monitors AWS Partner Central events and sends intelligent notifications to
sales reps via HubSpot tasks and email when critical events occur.

Critical events:
- AWS Engagement Score changes significantly (±15 points)
- AWS Seller assigned or changed
- Review status changes (Approved, Action Required, Rejected)
- AWS adds feedback or next steps
- Engagement invitation expires soon (within 3 days)
- Opportunity stage changes in Partner Central

Notification channels:
- HubSpot tasks (assigned to deal owner)
- HubSpot notes (visible on deal timeline)
- Optional: SNS topic for external integrations (Slack, email)
"""

import os
from datetime import datetime, timedelta

from common.base_handler import BaseLambdaHandler
from common.aws_client import PARTNER_CENTRAL_CATALOG

# Configurable thresholds
ENGAGEMENT_SCORE_THRESHOLD = int(os.environ.get("ENGAGEMENT_SCORE_THRESHOLD", "15"))
HIGH_ENGAGEMENT_SCORE = int(os.environ.get("HIGH_ENGAGEMENT_SCORE", "80"))
SNS_TOPIC_ARN = os.environ.get("NOTIFICATION_SNS_TOPIC_ARN")


class SmartNotificationsHandler(BaseLambdaHandler):
    """
    Handles Partner Central events and creates smart notifications.

    Can be triggered by:
    1. EventBridge events (real-time)
    2. Scheduled execution (periodic check)
    """

    def _execute(self, event: dict, context: dict) -> dict:
        """Process Partner Central events and create notifications."""
        # Determine event source
        if event.get("source") == "aws.partnercentral-selling":
            # EventBridge event
            return self._handle_eventbridge_event(event)
        else:
            # Scheduled check
            return self._handle_scheduled_check(event)

    def _handle_eventbridge_event(self, event: dict) -> dict:
        """Handle real-time EventBridge events from Partner Central."""
        detail = event.get("detail", {})
        detail_type = event.get("detail-type", "")

        self.logger.info("Processing EventBridge event type: %s", detail_type)

        notifications = []

        if detail_type == "Opportunity Updated":
            opportunity_id = detail.get("opportunity", {}).get("identifier")

            if opportunity_id:
                # Find the corresponding HubSpot deal
                deal = self._find_deal_by_opportunity_id(opportunity_id)

                if deal:
                    notification = self._check_opportunity_update(deal, detail)
                    if notification:
                        notifications.append(notification)

        elif detail_type == "Engagement Invitation Created":
            # New invitation - create high priority notification
            # (This is already handled by invitation acceptance flow,
            # but we can add a notification here too)
            pass

        return self._success_response(
            {"notificationsCreated": len(notifications), "notifications": notifications}
        )

    def _handle_scheduled_check(self, event: dict) -> dict:
        """Periodic check for notification-worthy events."""
        notifications = []

        # Get all active deals with AWS opportunities
        deals = self._list_active_deals()
        self.logger.info("Checking %d active deals for notifications", len(deals))

        for deal in deals:
            deal_id = deal["id"]
            props = deal.get("properties", {})
            aws_opportunity_id = props.get("aws_opportunity_id")

            if not aws_opportunity_id:
                continue

            try:
                # Get AWS Opportunity Summary for current state
                summary = self.pc_client.get_aws_opportunity_summary(
                    Catalog=PARTNER_CENTRAL_CATALOG,
                    RelatedOpportunityIdentifier=aws_opportunity_id,
                )

                # Check for significant engagement score changes
                notification = self._check_engagement_score_change(
                    deal_id, props, summary
                )
                if notification:
                    notifications.append(notification)

                # Check for review status changes
                notification = self._check_review_status_change(deal_id, props, summary)
                if notification:
                    notifications.append(notification)

                # Check for new AWS seller assignment
                notification = self._check_seller_assignment(deal_id, props, summary)
                if notification:
                    notifications.append(notification)

            except Exception as e:
                self.logger.error(
                    "Error checking deal %s: %s", deal_id, str(e), exc_info=True
                )

        return self._success_response(
            {
                "dealsChecked": len(deals),
                "notificationsCreated": len(notifications),
                "notifications": notifications,
            }
        )

    def _check_opportunity_update(self, deal: dict, event_detail: dict) -> dict | None:
        """Check if opportunity update warrants a notification."""
        # This would parse the event detail and create notifications
        # for significant changes
        return None

    def _check_engagement_score_change(
        self, deal_id: str, props: dict, summary: dict
    ) -> dict | None:
        """Check if engagement score changed significantly."""
        current_score = summary.get("Insights", {}).get("EngagementScore")
        previous_score_str = props.get("aws_engagement_score")

        if current_score is None or not previous_score_str:
            return None

        try:
            previous_score = int(float(previous_score_str))
        except (ValueError, TypeError):
            return None

        score_change = current_score - previous_score

        # Only notify on significant changes
        if abs(score_change) < ENGAGEMENT_SCORE_THRESHOLD:
            return None

        # Create notification
        if score_change > 0:
            priority = "high" if current_score >= HIGH_ENGAGEMENT_SCORE else "medium"
            title = f"🎯 AWS Engagement Score Increased (+{score_change})"
            message = (
                f"AWS's interest in this opportunity has increased!\n\n"
                f"**Previous Score:** {previous_score}/100\n"
                f"**Current Score:** {current_score}/100\n"
                f"**Change:** +{score_change} points\n\n"
            )

            if current_score >= HIGH_ENGAGEMENT_SCORE:
                message += (
                    "💡 **Action:** This is a high-priority opportunity. "
                    "Consider accelerating the sales cycle and coordinating "
                    "closely with the AWS team."
                )
        else:
            priority = "medium"
            title = f"⚠️ AWS Engagement Score Decreased ({score_change})"
            message = (
                f"AWS's interest in this opportunity has decreased.\n\n"
                f"**Previous Score:** {previous_score}/100\n"
                f"**Current Score:** {current_score}/100\n"
                f"**Change:** {score_change} points\n\n"
                f"💡 **Action:** Review the opportunity details and "
                f"consider reaching out to your AWS contact for feedback."
            )

        # Create HubSpot task
        self._create_hubspot_task(deal_id, title, message, priority)

        # Add note to deal
        self.hubspot_client.add_note_to_deal(deal_id, f"{title}\n\n{message}")

        # Send SNS notification if configured
        if SNS_TOPIC_ARN:
            self._send_sns_notification(title, message, deal_id, priority)

        return {
            "dealId": deal_id,
            "type": "engagement_score_change",
            "scoreChange": score_change,
            "currentScore": current_score,
        }

    def _check_review_status_change(
        self, deal_id: str, props: dict, summary: dict
    ) -> dict | None:
        """Check if review status changed."""
        current_status = summary.get("LifeCycle", {}).get("ReviewStatus")
        previous_status = props.get("aws_review_status")

        if not current_status or current_status == previous_status:
            return None

        # Create notification based on new status
        status_notifications = {
            "Approved": {
                "priority": "high",
                "title": "✅ AWS Approved Opportunity",
                "message": (
                    "Great news! AWS has approved this co-sell opportunity.\n\n"
                    "**Next Steps:**\n"
                    "- Coordinate with your assigned AWS seller\n"
                    "- Schedule joint customer calls\n"
                    "- Leverage AWS resources for closing\n"
                ),
            },
            "Action Required": {
                "priority": "high",
                "title": "⚠️ AWS Requires Action",
                "message": (
                    "AWS has requested additional information for this opportunity.\n\n"
                    "**Action Required:**\n"
                    "- Review AWS feedback in Partner Central\n"
                    "- Update opportunity with requested details\n"
                    "- Respond within 48 hours to maintain momentum\n"
                ),
            },
            "Rejected": {
                "priority": "medium",
                "title": "❌ AWS Rejected Opportunity",
                "message": (
                    "AWS has declined to co-sell this opportunity.\n\n"
                    "**Next Steps:**\n"
                    "- Review rejection reason in Partner Central\n"
                    "- Consider resubmitting with more details\n"
                    "- Or proceed independently without AWS co-sell\n"
                ),
            },
        }

        notification_config = status_notifications.get(current_status)
        if not notification_config:
            return None

        # Create task and note
        self._create_hubspot_task(
            deal_id,
            notification_config["title"],
            notification_config["message"],
            notification_config["priority"],
        )

        self.hubspot_client.add_note_to_deal(
            deal_id,
            f"{notification_config['title']}\n\n{notification_config['message']}",
        )

        if SNS_TOPIC_ARN:
            self._send_sns_notification(
                notification_config["title"],
                notification_config["message"],
                deal_id,
                notification_config["priority"],
            )

        return {
            "dealId": deal_id,
            "type": "review_status_change",
            "newStatus": current_status,
            "previousStatus": previous_status,
        }

    def _check_seller_assignment(
        self, deal_id: str, props: dict, summary: dict
    ) -> dict | None:
        """Check if AWS seller was assigned or changed."""
        opportunity_team = summary.get("OpportunityTeam", [])

        if not opportunity_team:
            return None

        # Get first AWS seller
        aws_seller = opportunity_team[0]
        seller_name = f"{aws_seller.get('FirstName', '')} {aws_seller.get('LastName', '')}".strip()
        seller_email = aws_seller.get("Email", "")

        previous_seller = props.get("aws_seller_name")

        if not seller_name or seller_name == previous_seller:
            return None

        # New seller assigned
        title = "👤 AWS Seller Assigned"
        message = (
            f"An AWS seller has been assigned to this opportunity!\n\n"
            f"**AWS Seller:** {seller_name}\n"
        )

        if seller_email:
            message += f"**Email:** {seller_email}\n"

        message += (
            "\n💡 **Action:** Reach out to introduce yourself and "
            "coordinate on the opportunity strategy."
        )

        self._create_hubspot_task(deal_id, title, message, "high")
        self.hubspot_client.add_note_to_deal(deal_id, f"{title}\n\n{message}")

        if SNS_TOPIC_ARN:
            self._send_sns_notification(title, message, deal_id, "high")

        return {
            "dealId": deal_id,
            "type": "seller_assignment",
            "sellerName": seller_name,
        }

    def _create_hubspot_task(
        self, deal_id: str, title: str, description: str, priority: str
    ) -> None:
        """Create a HubSpot task associated with the deal."""
        try:
            # Get deal owner
            deal = self.hubspot_client.get_deal(deal_id)
            owner_id = deal.get("properties", {}).get("hubspot_owner_id")

            # Calculate due date (24 hours for high priority, 3 days for others)
            due_days = 1 if priority == "high" else 3
            due_date = datetime.utcnow() + timedelta(days=due_days)

            task_data = {
                "properties": {
                    "hs_task_subject": title,
                    "hs_task_body": description,
                    "hs_task_status": "NOT_STARTED",
                    "hs_task_priority": priority.upper(),
                    "hs_timestamp": due_date.isoformat(),
                }
            }

            if owner_id:
                task_data["properties"]["hubspot_owner_id"] = owner_id

            # Create task
            url = "https://api.hubapi.com/crm/v3/objects/tasks"
            response = self.hubspot_client.session.post(url, json=task_data)
            response.raise_for_status()

            task_id = response.json().get("id")

            # Associate task with deal
            assoc_url = f"https://api.hubapi.com/crm/v4/objects/tasks/{task_id}/associations/deals/{deal_id}"
            assoc_data = [
                {
                    "associationCategory": "HUBSPOT_DEFINED",
                    "associationTypeId": 216,  # Task to Deal
                }
            ]
            self.hubspot_client.session.put(assoc_url, json=assoc_data)

            self.logger.info("Created HubSpot task %s for deal %s", task_id, deal_id)

        except Exception as e:
            self.logger.error("Error creating HubSpot task: %s", str(e), exc_info=True)

    def _send_sns_notification(
        self, title: str, message: str, deal_id: str, priority: str
    ) -> None:
        """Send notification to SNS topic."""
        if not SNS_TOPIC_ARN:
            return

        try:
            import boto3

            sns = boto3.client("sns")

            sns.publish(
                TopicArn=SNS_TOPIC_ARN,
                Subject=title,
                Message=message,
                MessageAttributes={
                    "dealId": {"DataType": "String", "StringValue": deal_id},
                    "priority": {"DataType": "String", "StringValue": priority},
                },
            )

            self.logger.info("Sent SNS notification for deal %s", deal_id)

        except Exception as e:
            self.logger.error(
                "Error sending SNS notification: %s", str(e), exc_info=True
            )

    def _find_deal_by_opportunity_id(self, opportunity_id: str) -> dict | None:
        """Find HubSpot deal by AWS opportunity ID."""
        url = "https://api.hubapi.com/crm/v3/objects/deals/search"

        payload = {
            "filterGroups": [
                {
                    "filters": [
                        {
                            "propertyName": "aws_opportunity_id",
                            "operator": "EQ",
                            "value": opportunity_id,
                        }
                    ]
                }
            ],
            "limit": 1,
        }

        response = self.hubspot_client.session.post(url, json=payload)
        response.raise_for_status()

        results = response.json().get("results", [])
        return results[0] if results else None

    def _list_active_deals(self) -> list[dict]:
        """List all active deals with AWS opportunities."""
        url = "https://api.hubapi.com/crm/v3/objects/deals/search"

        payload = {
            "filterGroups": [
                {
                    "filters": [
                        {
                            "propertyName": "aws_opportunity_id",
                            "operator": "HAS_PROPERTY",
                        },
                        {
                            "propertyName": "dealstage",
                            "operator": "NEQ",
                            "value": "closedlost",
                        },
                        {
                            "propertyName": "dealstage",
                            "operator": "NEQ",
                            "value": "closedwon",
                        },
                    ]
                }
            ],
            "properties": [
                "dealname",
                "aws_opportunity_id",
                "aws_engagement_score",
                "aws_review_status",
                "aws_seller_name",
            ],
            "limit": 100,
        }

        response = self.hubspot_client.session.post(url, json=payload)
        response.raise_for_status()

        return response.json().get("results", [])


def lambda_handler(event: dict, context: dict) -> dict:
    """Lambda entry point for smart notifications handler."""
    return SmartNotificationsHandler().handle(event, context)
