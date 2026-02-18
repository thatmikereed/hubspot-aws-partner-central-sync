"""
Notification Integration Helpers

Easy-to-use functions for sending notifications from Lambda handlers.
Import and call these functions to trigger user notifications.
"""

import logging
from typing import Optional

from .notification_service import HubSpotNotificationService

logger = logging.getLogger()


def send_notification(
    hubspot_client,
    notification_func: str,
    **kwargs
):
    """
    Generic notification sender.
    
    Usage:
        from notification_service.integration import send_notification
        
        send_notification(
            hubspot_client,
            "notify_new_opportunity",
            deal_id="123",
            opportunity_id="O123",
            deal_name="Acme Corp",
            deal_owner_id="456"
        )
    """
    try:
        service = HubSpotNotificationService(hubspot_client)
        func = getattr(service, notification_func)
        return func(**kwargs)
    except Exception as exc:
        logger.warning("Failed to send notification %s: %s", notification_func, exc)
        return {"status": "failed", "error": str(exc)}


def notify_from_invitation(hubspot_client, deal_id: str, opportunity_id: str, deal: dict):
    """
    Convenience function for new opportunity from AWS invitation.
    
    Usage in partner_central_to_hubspot handler:
        from notification_service.integration import notify_from_invitation
        notify_from_invitation(hubspot, deal_id, opportunity_id, deal)
    """
    props = deal.get("properties", {})
    return send_notification(
        hubspot_client,
        "notify_new_opportunity",
        deal_id=deal_id,
        opportunity_id=opportunity_id,
        deal_name=props.get("dealname", "Unknown"),
        deal_owner_id=props.get("hubspot_owner_id", ""),
        invitation_sender=props.get("aws_invitation_sender")
    )


def notify_from_sync(hubspot_client, deal_id: str, opportunity_id: str, deal: dict, changes: dict):
    """
    Convenience function for opportunity updates from AWS.
    
    Usage in eventbridge_events handler:
        from notification_service.integration import notify_from_sync
        notify_from_sync(hubspot, deal_id, opportunity_id, deal, {"Stage": "Technical Validation"})
    """
    props = deal.get("properties", {})
    return send_notification(
        hubspot_client,
        "notify_opportunity_updated",
        deal_id=deal_id,
        opportunity_id=opportunity_id,
        deal_name=props.get("dealname", "Unknown"),
        deal_owner_id=props.get("hubspot_owner_id", ""),
        changes=changes
    )


def notify_from_score_change(
    hubspot_client,
    deal_id: str,
    opportunity_id: str,
    deal: dict,
    old_score: int,
    new_score: int
):
    """
    Convenience function for engagement score changes.
    
    Usage in sync_aws_summary handler:
        from notification_service.integration import notify_from_score_change
        notify_from_score_change(hubspot, deal_id, opp_id, deal, 70, 85)
    """
    props = deal.get("properties", {})
    return send_notification(
        hubspot_client,
        "notify_engagement_score_change",
        deal_id=deal_id,
        opportunity_id=opportunity_id,
        deal_name=props.get("dealname", "Unknown"),
        deal_owner_id=props.get("hubspot_owner_id", ""),
        old_score=old_score,
        new_score=new_score,
        delta=new_score - old_score
    )


def notify_from_status_change(
    hubspot_client,
    deal_id: str,
    opportunity_id: str,
    deal: dict,
    old_status: str,
    new_status: str,
    feedback: Optional[str] = None
):
    """
    Convenience function for review status changes.
    
    Usage in eventbridge_events handler:
        from notification_service.integration import notify_from_status_change
        notify_from_status_change(hubspot, deal_id, opp_id, deal, "Submitted", "Approved")
    """
    props = deal.get("properties", {})
    return send_notification(
        hubspot_client,
        "notify_review_status_change",
        deal_id=deal_id,
        opportunity_id=opportunity_id,
        deal_name=props.get("dealname", "Unknown"),
        deal_owner_id=props.get("hubspot_owner_id", ""),
        old_status=old_status,
        new_status=new_status,
        feedback=feedback
    )


def notify_from_submission(
    hubspot_client,
    deal_id: str,
    opportunity_id: str,
    deal: dict,
    involvement_type: str
):
    """
    Convenience function for opportunity submission confirmation.
    
    Usage in submit_opportunity handler:
        from notification_service.integration import notify_from_submission
        notify_from_submission(hubspot, deal_id, opp_id, deal, "Co-Sell")
    """
    props = deal.get("properties", {})
    return send_notification(
        hubspot_client,
        "notify_submission_confirmed",
        deal_id=deal_id,
        opportunity_id=opportunity_id,
        deal_name=props.get("dealname", "Unknown"),
        deal_owner_id=props.get("hubspot_owner_id", ""),
        involvement_type=involvement_type
    )


def notify_from_seller_assignment(
    hubspot_client,
    deal_id: str,
    opportunity_id: str,
    deal: dict,
    seller_name: str,
    seller_email: Optional[str] = None
):
    """
    Convenience function for AWS seller assignments.
    
    Usage in team_sync handler:
        from notification_service.integration import notify_from_seller_assignment
        notify_from_seller_assignment(hubspot, deal_id, opp_id, deal, "John Smith", "john@aws.amazon.com")
    """
    props = deal.get("properties", {})
    return send_notification(
        hubspot_client,
        "notify_aws_seller_assigned",
        deal_id=deal_id,
        opportunity_id=opportunity_id,
        deal_name=props.get("dealname", "Unknown"),
        deal_owner_id=props.get("hubspot_owner_id", ""),
        seller_name=seller_name,
        seller_email=seller_email
    )


def notify_from_resources(
    hubspot_client,
    deal_id: str,
    opportunity_id: str,
    deal: dict,
    resource_count: int,
    resource_types: list[str]
):
    """
    Convenience function for new AWS resources.
    
    Usage in resource_snapshot_sync handler:
        from notification_service.integration import notify_from_resources
        notify_from_resources(hubspot, deal_id, opp_id, deal, 3, ["Case Study", "Whitepaper"])
    """
    props = deal.get("properties", {})
    return send_notification(
        hubspot_client,
        "notify_resources_available",
        deal_id=deal_id,
        opportunity_id=opportunity_id,
        deal_name=props.get("dealname", "Unknown"),
        deal_owner_id=props.get("hubspot_owner_id", ""),
        resource_count=resource_count,
        resource_types=resource_types
    )


def notify_from_conflict(
    hubspot_client,
    deal_id: str,
    opportunity_id: str,
    deal: dict,
    conflicts: list[str]
):
    """
    Convenience function for sync conflicts.
    
    Usage in conflict_detector handler:
        from notification_service.integration import notify_from_conflict
        notify_from_conflict(hubspot, deal_id, opp_id, deal, ["Stage mismatch", "Amount differs"])
    """
    props = deal.get("properties", {})
    return send_notification(
        hubspot_client,
        "notify_conflict_detected",
        deal_id=deal_id,
        opportunity_id=opportunity_id,
        deal_name=props.get("dealname", "Unknown"),
        deal_owner_id=props.get("hubspot_owner_id", ""),
        conflicts=conflicts
    )
