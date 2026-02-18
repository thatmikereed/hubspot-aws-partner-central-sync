"""
HubSpot User Notification Service

Creates HubSpot tasks (which trigger bell notifications) and notes for AWS Partner Central events.
When a task is assigned to a user via the Engagements API, they receive a bell notification in HubSpot.

Notification Types:
- Bell notifications (via task assignment)
- Activity feed notes (via note engagements)
- Email notifications (via task reminders)

Events that trigger notifications:
- New AWS opportunities (invitations)
- AWS syncing changes back (stage, status, score)
- Engagement score changes
- Review status changes (Approved, Action Required, Rejected)
- Opportunity submission confirmations
- AWS seller assignments
- Resource availability
- Conflicts detected
"""

import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, Literal
from enum import Enum

logger = logging.getLogger()
logger.setLevel(logging.INFO)


class NotificationPriority(str, Enum):
    """Notification priority levels"""
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class NotificationType(str, Enum):
    """Types of AWS Partner Central notifications"""
    NEW_OPPORTUNITY = "new_opportunity"
    OPPORTUNITY_UPDATED = "opportunity_updated"
    ENGAGEMENT_SCORE_CHANGE = "engagement_score_change"
    REVIEW_STATUS_CHANGE = "review_status_change"
    SUBMISSION_CONFIRMED = "submission_confirmed"
    AWS_SELLER_ASSIGNED = "aws_seller_assigned"
    RESOURCES_AVAILABLE = "resources_available"
    CONFLICT_DETECTED = "conflict_detected"
    ACTION_REQUIRED = "action_required"


class HubSpotNotificationService:
    """Service for creating user notifications in HubSpot"""
    
    def __init__(self, hubspot_client):
        self.hubspot = hubspot_client
        
    def notify_new_opportunity(
        self,
        deal_id: str,
        opportunity_id: str,
        deal_name: str,
        deal_owner_id: str,
        invitation_sender: Optional[str] = None
    ):
        """
        Notify when a new AWS opportunity is created from an invitation.
        
        Creates:
        - HIGH priority task (due in 24 hours)
        - Note with invitation details
        """
        title = f"🆕 New AWS Co-Sell Opportunity: {deal_name}"
        
        body = (
            f"A new AWS co-sell opportunity has been created from an AWS invitation.\n\n"
            f"**Opportunity:** {deal_name}\n"
            f"**AWS Opportunity ID:** {opportunity_id}\n"
        )
        
        if invitation_sender:
            body += f"**Invited by:** {invitation_sender}\n"
        
        body += (
            f"\n**Next Steps:**\n"
            f"1. Review opportunity details in Partner Central\n"
            f"2. Update deal information in HubSpot\n"
            f"3. Coordinate with AWS team within 24 hours\n"
            f"4. Schedule joint discovery call\n"
        )
        
        self._create_notification(
            deal_id=deal_id,
            owner_id=deal_owner_id,
            title=title,
            body=body,
            priority=NotificationPriority.HIGH,
            notification_type=NotificationType.NEW_OPPORTUNITY,
            due_hours=24
        )
        
        logger.info("Created new opportunity notification for deal %s", deal_id)
        
    def notify_opportunity_updated(
        self,
        deal_id: str,
        opportunity_id: str,
        deal_name: str,
        deal_owner_id: str,
        changes: dict
    ):
        """
        Notify when AWS updates an opportunity (reverse sync from PC → HubSpot).
        
        Creates:
        - MEDIUM priority task (due in 3 days)
        - Note listing what changed
        """
        title = f"🔄 AWS Updated Opportunity: {deal_name}"
        
        change_list = "\n".join([f"- **{k}:** {v}" for k, v in changes.items()])
        
        body = (
            f"AWS has made updates to this opportunity in Partner Central.\n\n"
            f"**Changes:**\n{change_list}\n\n"
            f"**Next Steps:**\n"
            f"1. Review changes in HubSpot deal\n"
            f"2. Sync internal team on AWS updates\n"
            f"3. Update sales strategy if needed\n"
        )
        
        self._create_notification(
            deal_id=deal_id,
            owner_id=deal_owner_id,
            title=title,
            body=body,
            priority=NotificationPriority.MEDIUM,
            notification_type=NotificationType.OPPORTUNITY_UPDATED,
            due_hours=72
        )
        
        logger.info("Created opportunity updated notification for deal %s", deal_id)
        
    def notify_engagement_score_change(
        self,
        deal_id: str,
        opportunity_id: str,
        deal_name: str,
        deal_owner_id: str,
        old_score: int,
        new_score: int,
        delta: int
    ):
        """
        Notify when AWS engagement score changes significantly.
        
        High score (80+) increase → HIGH priority (urgent action)
        Score decrease → MEDIUM priority (review needed)
        Other increases → LOW priority (positive signal)
        """
        direction = "increased" if delta > 0 else "decreased"
        emoji = "🎯" if delta > 0 else "⚠️"
        
        title = f"{emoji} AWS Engagement Score {direction.title()}: {deal_name}"
        
        body = (
            f"AWS engagement score has changed significantly.\n\n"
            f"**Previous Score:** {old_score}/100\n"
            f"**New Score:** {new_score}/100\n"
            f"**Change:** {delta:+d} points\n\n"
        )
        
        # Determine priority and actions based on score
        if new_score >= 80 and delta > 0:
            priority = NotificationPriority.HIGH
            due_hours = 24
            body += (
                f"**🚀 HIGH PRIORITY: AWS is very interested in this deal!**\n\n"
                f"**Immediate Actions:**\n"
                f"1. Accelerate sales cycle - schedule AWS joint call ASAP\n"
                f"2. Coordinate with AWS seller on strategy\n"
                f"3. Prepare for AWS co-sell engagement\n"
                f"4. Update executive stakeholders\n"
            )
        elif delta < 0:
            priority = NotificationPriority.MEDIUM
            due_hours = 48
            body += (
                f"**⚠️ Score decreased - review required**\n\n"
                f"**Next Steps:**\n"
                f"1. Contact AWS seller for feedback\n"
                f"2. Review opportunity details for accuracy\n"
                f"3. Address any AWS concerns\n"
                f"4. Update opportunity if needed\n"
            )
        else:
            priority = NotificationPriority.LOW
            due_hours = 72
            body += (
                f"**👍 Positive signal from AWS**\n\n"
                f"**Next Steps:**\n"
                f"1. Continue current approach\n"
                f"2. Keep AWS updated on progress\n"
                f"3. Prepare for potential escalation\n"
            )
        
        self._create_notification(
            deal_id=deal_id,
            owner_id=deal_owner_id,
            title=title,
            body=body,
            priority=priority,
            notification_type=NotificationType.ENGAGEMENT_SCORE_CHANGE,
            due_hours=due_hours
        )
        
        logger.info("Created engagement score change notification for deal %s: %d → %d", 
                   deal_id, old_score, new_score)
        
    def notify_review_status_change(
        self,
        deal_id: str,
        opportunity_id: str,
        deal_name: str,
        deal_owner_id: str,
        old_status: str,
        new_status: str,
        feedback: Optional[str] = None
    ):
        """
        Notify when AWS review status changes.
        
        Approved → HIGH priority (coordinate engagement)
        Action Required → HIGH priority (urgent updates needed)
        Rejected → MEDIUM priority (review rejection reason)
        """
        status_emoji = {
            "Approved": "✅",
            "Action Required": "⚠️",
            "Rejected": "❌",
            "Submitted": "📤",
            "In Review": "👀"
        }
        
        emoji = status_emoji.get(new_status, "📋")
        title = f"{emoji} AWS Review Status: {new_status} - {deal_name}"
        
        body = (
            f"AWS has updated the review status for this opportunity.\n\n"
            f"**Previous Status:** {old_status}\n"
            f"**New Status:** {new_status}\n\n"
        )
        
        if feedback:
            body += f"**AWS Feedback:**\n{feedback}\n\n"
        
        # Set priority and actions based on new status
        if new_status == "Approved":
            priority = NotificationPriority.HIGH
            due_hours = 24
            body += (
                f"**🎉 APPROVED: AWS has approved this co-sell opportunity!**\n\n"
                f"**Immediate Actions:**\n"
                f"1. Celebrate with the team! 🎊\n"
                f"2. Coordinate with assigned AWS seller within 24 hours\n"
                f"3. Schedule joint customer calls\n"
                f"4. Develop co-sell strategy and timeline\n"
                f"5. Leverage AWS resources and support\n"
            )
        elif new_status == "Action Required":
            priority = NotificationPriority.HIGH
            due_hours = 48
            body += (
                f"**⚠️ ACTION REQUIRED: AWS needs additional information**\n\n"
                f"**Urgent Actions:**\n"
                f"1. Review AWS feedback above\n"
                f"2. Update opportunity in Partner Central within 48 hours\n"
                f"3. Provide requested information\n"
                f"4. Re-submit for AWS review\n"
            )
        elif new_status == "Rejected":
            priority = NotificationPriority.MEDIUM
            due_hours = 72
            body += (
                f"**❌ REJECTED: Opportunity not approved for co-sell**\n\n"
                f"**Next Steps:**\n"
                f"1. Review rejection reason carefully\n"
                f"2. Determine if resubmission is appropriate\n"
                f"3. Update opportunity details if needed\n"
                f"4. Consider alternative AWS engagement paths\n"
            )
        elif new_status == "In Review":
            priority = NotificationPriority.LOW
            due_hours = 168  # 7 days
            body += (
                f"**👀 IN REVIEW: AWS is evaluating this opportunity**\n\n"
                f"**What to expect:**\n"
                f"1. AWS typically reviews within 1-2 business days\n"
                f"2. You'll be notified of status changes\n"
                f"3. Prepare for potential AWS questions\n"
            )
        else:
            priority = NotificationPriority.MEDIUM
            due_hours = 72
            body += f"**Next Steps:** Review new status and adjust strategy accordingly.\n"
        
        self._create_notification(
            deal_id=deal_id,
            owner_id=deal_owner_id,
            title=title,
            body=body,
            priority=priority,
            notification_type=NotificationType.REVIEW_STATUS_CHANGE,
            due_hours=due_hours
        )
        
        logger.info("Created review status change notification for deal %s: %s → %s", 
                   deal_id, old_status, new_status)
        
    def notify_submission_confirmed(
        self,
        deal_id: str,
        opportunity_id: str,
        deal_name: str,
        deal_owner_id: str,
        involvement_type: str
    ):
        """
        Notify when opportunity is successfully submitted to AWS.
        
        Creates:
        - MEDIUM priority task (due in 3 days)
        - Note with submission confirmation
        """
        title = f"📤 Opportunity Submitted to AWS: {deal_name}"
        
        body = (
            f"Your opportunity has been successfully submitted to AWS Partner Central.\n\n"
            f"**Opportunity:** {deal_name}\n"
            f"**AWS Opportunity ID:** {opportunity_id}\n"
            f"**Involvement Type:** {involvement_type}\n\n"
            f"**What happens next:**\n"
            f"1. AWS will review within 1-2 business days\n"
            f"2. You'll receive notification of approval or feedback\n"
            f"3. If approved, an AWS seller will be assigned\n\n"
            f"**While you wait:**\n"
            f"1. Prepare joint value proposition\n"
            f"2. Identify key customer stakeholders\n"
            f"3. Review AWS solutions and resources\n"
            f"4. Keep deal information updated\n"
        )
        
        self._create_notification(
            deal_id=deal_id,
            owner_id=deal_owner_id,
            title=title,
            body=body,
            priority=NotificationPriority.MEDIUM,
            notification_type=NotificationType.SUBMISSION_CONFIRMED,
            due_hours=72
        )
        
        logger.info("Created submission confirmed notification for deal %s", deal_id)
        
    def notify_aws_seller_assigned(
        self,
        deal_id: str,
        opportunity_id: str,
        deal_name: str,
        deal_owner_id: str,
        seller_name: str,
        seller_email: Optional[str] = None
    ):
        """
        Notify when AWS assigns a seller to the opportunity.
        
        Creates:
        - HIGH priority task (due in 24 hours)
        - Note with seller contact info
        """
        title = f"👤 AWS Seller Assigned: {seller_name} - {deal_name}"
        
        body = (
            f"AWS has assigned a seller to work with you on this co-sell opportunity!\n\n"
            f"**AWS Seller:** {seller_name}\n"
        )
        
        if seller_email:
            body += f"**Email:** {seller_email}\n"
        
        body += (
            f"\n**Immediate Actions (within 24 hours):**\n"
            f"1. Send introductory email to AWS seller\n"
            f"2. Schedule alignment call\n"
            f"3. Share customer background and status\n"
            f"4. Discuss co-sell strategy and timeline\n"
            f"5. Identify joint next steps\n\n"
            f"**Email Template:**\n"
            f"Hi {seller_name},\n\n"
            f"Great to connect! I'm excited to work together on {deal_name}. "
            f"Let's schedule a call this week to align on strategy...\n"
        )
        
        self._create_notification(
            deal_id=deal_id,
            owner_id=deal_owner_id,
            title=title,
            body=body,
            priority=NotificationPriority.HIGH,
            notification_type=NotificationType.AWS_SELLER_ASSIGNED,
            due_hours=24
        )
        
        logger.info("Created AWS seller assigned notification for deal %s: %s", 
                   deal_id, seller_name)
        
    def notify_resources_available(
        self,
        deal_id: str,
        opportunity_id: str,
        deal_name: str,
        deal_owner_id: str,
        resource_count: int,
        resource_types: list[str]
    ):
        """
        Notify when new AWS resources are available for the opportunity.
        
        Creates:
        - LOW priority task (due in 7 days)
        - Note listing available resources
        """
        title = f"📚 {resource_count} New AWS Resources Available: {deal_name}"
        
        resource_list = ", ".join(resource_types) if resource_types else "Various"
        
        body = (
            f"AWS has made {resource_count} new resources available for this opportunity.\n\n"
            f"**Resource Types:** {resource_list}\n\n"
            f"**Next Steps:**\n"
            f"1. Review resources in deal notes below\n"
            f"2. Share relevant materials with customer\n"
            f"3. Leverage in sales presentations\n"
            f"4. Coordinate with AWS on resource usage\n\n"
            f"Resources have been added as notes on this deal with links.\n"
        )
        
        self._create_notification(
            deal_id=deal_id,
            owner_id=deal_owner_id,
            title=title,
            body=body,
            priority=NotificationPriority.LOW,
            notification_type=NotificationType.RESOURCES_AVAILABLE,
            due_hours=168  # 7 days
        )
        
        logger.info("Created resources available notification for deal %s: %d resources", 
                   deal_id, resource_count)
        
    def notify_conflict_detected(
        self,
        deal_id: str,
        opportunity_id: str,
        deal_name: str,
        deal_owner_id: str,
        conflicts: list[str]
    ):
        """
        Notify when sync conflicts are detected between HubSpot and Partner Central.
        
        Creates:
        - HIGH priority task (due in 12 hours)
        - Note listing conflicts
        """
        title = f"⚠️ Sync Conflict Detected: {deal_name}"
        
        conflict_list = "\n".join([f"- {c}" for c in conflicts])
        
        body = (
            f"Conflicting data detected between HubSpot and AWS Partner Central.\n\n"
            f"**Conflicts:**\n{conflict_list}\n\n"
            f"**URGENT: Resolve within 12 hours to maintain sync**\n\n"
            f"**Actions:**\n"
            f"1. Review conflicting fields in both systems\n"
            f"2. Determine correct values\n"
            f"3. Update the source system\n"
            f"4. Verify sync resumes\n\n"
            f"**Note:** Unresolved conflicts may prevent proper co-sell coordination with AWS.\n"
        )
        
        self._create_notification(
            deal_id=deal_id,
            owner_id=deal_owner_id,
            title=title,
            body=body,
            priority=NotificationPriority.HIGH,
            notification_type=NotificationType.CONFLICT_DETECTED,
            due_hours=12
        )
        
        logger.info("Created conflict detected notification for deal %s: %d conflicts", 
                   deal_id, len(conflicts))
        
    def _create_notification(
        self,
        deal_id: str,
        owner_id: str,
        title: str,
        body: str,
        priority: NotificationPriority,
        notification_type: NotificationType,
        due_hours: int = 24
    ):
        """
        Create a HubSpot task (triggers bell notification) and note.
        
        Tasks assigned via API trigger bell notifications for the assigned user.
        """
        try:
            # Calculate due date
            due_date = datetime.now(timezone.utc) + timedelta(hours=due_hours)
            due_date_ms = int(due_date.timestamp() * 1000)
            
            # Create task (this triggers bell notification)
            task_body = {
                "properties": {
                    "hs_task_subject": title,
                    "hs_task_body": body,
                    "hs_task_status": "NOT_STARTED",
                    "hs_task_priority": priority.value,
                    "hs_timestamp": due_date_ms,
                    "hubspot_owner_id": owner_id,
                    "hs_task_type": "AWS_PARTNER_CENTRAL"
                }
            }
            
            # Create task via Engagements API
            response = self.hubspot.session.post(
                "https://api.hubapi.com/crm/v3/objects/tasks",
                json=task_body
            )
            response.raise_for_status()
            task = response.json()
            task_id = task["id"]
            
            logger.info("Created task %s for deal %s (triggers bell notification)", task_id, deal_id)
            
            # Associate task with deal
            assoc_url = f"https://api.hubapi.com/crm/v4/objects/tasks/{task_id}/associations/deals/{deal_id}"
            assoc_response = self.hubspot.session.put(
                assoc_url,
                json=[{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": 216}]
            )
            
            if assoc_response.status_code not in (200, 204):
                logger.warning("Could not associate task %s with deal %s", task_id, deal_id)
            
            # Also create a note for context (appears in activity feed)
            note_body = {
                "properties": {
                    "hs_note_body": f"**{title}**\n\n{body}",
                    "hs_timestamp": int(datetime.now(timezone.utc).timestamp() * 1000)
                }
            }
            
            note_response = self.hubspot.session.post(
                "https://api.hubapi.com/crm/v3/objects/notes",
                json=note_body
            )
            
            if note_response.status_code in (200, 201):
                note = note_response.json()
                note_id = note["id"]
                
                # Associate note with deal
                note_assoc_url = f"https://api.hubapi.com/crm/v4/objects/notes/{note_id}/associations/deals/{deal_id}"
                self.hubspot.session.put(
                    note_assoc_url,
                    json=[{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": 214}]
                )
                
                logger.info("Created note %s for deal %s", note_id, deal_id)
            
            return {
                "taskId": task_id,
                "type": notification_type.value,
                "priority": priority.value,
                "status": "created"
            }
            
        except Exception as exc:
            logger.exception("Failed to create notification for deal %s: %s", deal_id, exc)
            return {"status": "failed", "error": str(exc)}
