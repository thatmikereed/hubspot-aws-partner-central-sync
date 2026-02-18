"""
Integration Examples: How to Add Notifications to Existing Handlers

These are example code snippets showing how to integrate the notification service
into existing Lambda handlers. Copy these patterns into your handlers.

IMPORTANT: Add `sys.path.insert(0, "/var/task")` at the top of handlers to import notification_service
"""

# ============================================================================
# Example 1: partner_central_to_hubspot/handler.py
# Send notification when new AWS opportunity is created from invitation
# ============================================================================

"""
At top of handler.py, add:
"""
import sys
sys.path.insert(0, "/var/task")
from notification_service.integration import notify_from_invitation

"""
In the main handler function, after creating the deal, add:
"""
def lambda_handler(event: dict, context) -> dict:
    # ... existing code to accept invitation and create deal ...
    
    deal = hubspot.create_deal(hs_properties)
    deal_id = deal["id"]
    
    # 🔔 SEND NOTIFICATION - New opportunity from AWS
    try:
        notify_from_invitation(
            hubspot_client=hubspot,
            deal_id=deal_id,
            opportunity_id=opportunity_id,
            deal=deal
        )
        logger.info("✅ Sent new opportunity notification for deal %s", deal_id)
    except Exception as e:
        logger.warning("⚠️ Failed to send notification: %s", e)
        # Don't fail the entire handler if notification fails
    
    # ... rest of handler ...


# ============================================================================
# Example 2: sync_aws_summary/handler.py
# Send notifications for engagement score changes and status changes
# ============================================================================

"""
At top of handler.py, add:
"""
import sys
sys.path.insert(0, "/var/task")
from notification_service.integration import (
    notify_from_score_change,
    notify_from_status_change
)

"""
In _sync_aws_summary function, after syncing data, add:
"""
def _sync_aws_summary(deal_id: str, opportunity_id: str, hubspot, pc_client) -> dict:
    # ... existing code to fetch AWS summary ...
    
    engagement_score = insights.get("EngagementScore")
    review_status = lifecycle.get("ReviewStatus", "")
    
    # Get current values from HubSpot
    deal, _, _ = hubspot.get_deal_with_associations(deal_id)
    props = deal.get("properties", {})
    old_score = props.get("aws_engagement_score")
    old_status = props.get("aws_review_status", "")
    
    # Update HubSpot with new values
    updates = {
        "aws_engagement_score": str(engagement_score) if engagement_score else None,
        "aws_review_status": review_status,
        # ... other updates ...
    }
    hubspot.update_deal(deal_id, updates)
    
    # 🔔 SEND NOTIFICATIONS
    
    # Notification 1: Engagement score changed significantly
    if engagement_score and old_score:
        try:
            old_score_int = int(old_score)
            delta = engagement_score - old_score_int
            
            # Only notify if change is ±10 or more
            if abs(delta) >= 10:
                notify_from_score_change(
                    hubspot_client=hubspot,
                    deal_id=deal_id,
                    opportunity_id=opportunity_id,
                    deal=deal,
                    old_score=old_score_int,
                    new_score=engagement_score
                )
                logger.info("✅ Sent engagement score notification: %d → %d", 
                           old_score_int, engagement_score)
        except (ValueError, TypeError) as e:
            logger.warning("Could not parse scores: %s", e)
    
    # Notification 2: Review status changed
    if review_status and old_status and review_status != old_status:
        try:
            feedback = lifecycle.get("NextSteps")  # AWS may provide feedback here
            notify_from_status_change(
                hubspot_client=hubspot,
                deal_id=deal_id,
                opportunity_id=opportunity_id,
                deal=deal,
                old_status=old_status,
                new_status=review_status,
                feedback=feedback
            )
            logger.info("✅ Sent review status notification: %s → %s", 
                       old_status, review_status)
        except Exception as e:
            logger.warning("⚠️ Failed to send status notification: %s", e)


# ============================================================================
# Example 3: submit_opportunity/handler.py
# Send notification when opportunity is successfully submitted to AWS
# ============================================================================

"""
At top of handler.py, add:
"""
import sys
sys.path.insert(0, "/var/task")
from notification_service.integration import notify_from_submission

"""
In _submit_opportunity function, after successful submission, add:
"""
def _submit_opportunity(deal_id, involvement_type, visibility, hubspot, pc_client):
    # ... existing code to submit opportunity ...
    
    # Update HubSpot with success
    hubspot.update_deal(deal_id, {
        "aws_review_status": "Submitted",
        "aws_submission_date": submission_date,
        "aws_involvement_type": involvement_type,
        "aws_visibility": visibility,
    })
    
    # 🔔 SEND NOTIFICATION - Submission confirmed
    try:
        deal, _, _ = hubspot.get_deal_with_associations(deal_id)
        notify_from_submission(
            hubspot_client=hubspot,
            deal_id=deal_id,
            opportunity_id=opportunity_id,
            deal=deal,
            involvement_type=involvement_type
        )
        logger.info("✅ Sent submission confirmation notification for deal %s", deal_id)
    except Exception as e:
        logger.warning("⚠️ Failed to send notification: %s", e)
    
    # ... rest of function ...


# ============================================================================
# Example 4: eventbridge_events/handler.py
# Send notification when AWS updates opportunity (reverse sync)
# ============================================================================

"""
At top of handler.py, add:
"""
import sys
sys.path.insert(0, "/var/task")
from notification_service.integration import notify_from_sync

"""
In _handle_opportunity_updated function, after syncing updates, add:
"""
def _handle_opportunity_updated(detail: dict, hubspot, pc_client):
    # ... existing code to fetch and sync opportunity ...
    
    # Track what changed
    changes = {}
    
    if "Stage" in lifecycle:
        changes["Stage"] = lifecycle["Stage"]
    if "ReviewStatus" in lifecycle:
        changes["Review Status"] = lifecycle["ReviewStatus"]
    # Add other fields that changed...
    
    # Update HubSpot
    hubspot.update_deal(deal_id, updates)
    
    # 🔔 SEND NOTIFICATION - AWS updated opportunity
    if changes:
        try:
            deal, _, _ = hubspot.get_deal_with_associations(deal_id)
            notify_from_sync(
                hubspot_client=hubspot,
                deal_id=deal_id,
                opportunity_id=opportunity_id,
                deal=deal,
                changes=changes
            )
            logger.info("✅ Sent opportunity updated notification for deal %s", deal_id)
        except Exception as e:
            logger.warning("⚠️ Failed to send notification: %s", e)


# ============================================================================
# Example 5: team_sync/handler.py
# Send notification when AWS assigns seller
# ============================================================================

"""
At top of handler.py, add:
"""
import sys
sys.path.insert(0, "/var/task")
from notification_service.integration import notify_from_seller_assignment

"""
In _sync_team_for_opportunity function, after syncing team, add:
"""
def _sync_team_for_opportunity(opportunity_id, hubspot, pc_client):
    # ... existing code to sync team members ...
    
    # Find the primary AWS seller (usually first team member)
    team_members = opportunity.get("OpportunityTeam", [])
    
    if team_members and synced_count > 0:
        primary_seller = team_members[0]
        seller_name = f"{primary_seller.get('FirstName', '')} {primary_seller.get('LastName', '')}".strip()
        seller_email = primary_seller.get("Email")
        
        # 🔔 SEND NOTIFICATION - AWS seller assigned
        if seller_name:
            try:
                deal, _, _ = hubspot.get_deal_with_associations(deal_id)
                notify_from_seller_assignment(
                    hubspot_client=hubspot,
                    deal_id=deal_id,
                    opportunity_id=opportunity_id,
                    deal=deal,
                    seller_name=seller_name,
                    seller_email=seller_email
                )
                logger.info("✅ Sent seller assignment notification: %s", seller_name)
            except Exception as e:
                logger.warning("⚠️ Failed to send notification: %s", e)


# ============================================================================
# Example 6: resource_snapshot_sync/handler.py
# Send notification when new AWS resources are synced
# ============================================================================

"""
At top of handler.py, add:
"""
import sys
sys.path.insert(0, "/var/task")
from notification_service.integration import notify_from_resources

"""
After syncing resources, add:
"""
def _sync_resources_for_deal(deal_id, opportunity_id, hubspot, pc_client):
    # ... existing code to fetch and sync resources ...
    
    new_resources_count = len(new_resources)
    
    if new_resources_count > 0:
        # ... existing code to create notes ...
        
        # 🔔 SEND NOTIFICATION - New resources available
        try:
            deal, _, _ = hubspot.get_deal_with_associations(deal_id)
            resource_types = list(set([r.get("Type") for r in new_resources if r.get("Type")]))
            
            notify_from_resources(
                hubspot_client=hubspot,
                deal_id=deal_id,
                opportunity_id=opportunity_id,
                deal=deal,
                resource_count=new_resources_count,
                resource_types=resource_types
            )
            logger.info("✅ Sent resources notification: %d new resources", new_resources_count)
        except Exception as e:
            logger.warning("⚠️ Failed to send notification: %s", e)


# ============================================================================
# Example 7: conflict_detector/handler.py
# Send notification when sync conflicts are detected
# ============================================================================

"""
At top of handler.py, add:
"""
import sys
sys.path.insert(0, "/var/task")
from notification_service.integration import notify_from_conflict

"""
When conflicts are detected, add:
"""
def _detect_conflicts(deal_id, opportunity_id, hubspot, pc_client):
    # ... existing code to detect conflicts ...
    
    conflicts = []
    if stage_mismatch:
        conflicts.append("Stage mismatch between HubSpot and Partner Central")
    if amount_differs:
        conflicts.append("Deal amount differs by more than 10%")
    # ... other conflicts ...
    
    if conflicts:
        # 🔔 SEND NOTIFICATION - Conflicts detected
        try:
            deal, _, _ = hubspot.get_deal_with_associations(deal_id)
            notify_from_conflict(
                hubspot_client=hubspot,
                deal_id=deal_id,
                opportunity_id=opportunity_id,
                deal=deal,
                conflicts=conflicts
            )
            logger.info("✅ Sent conflict notification: %d conflicts", len(conflicts))
        except Exception as e:
            logger.warning("⚠️ Failed to send notification: %s", e)


# ============================================================================
# TESTING NOTIFICATIONS
# ============================================================================

"""
To test notifications manually:

1. Create a test Lambda or Python script:
"""
from common.hubspot_client import HubSpotClient
from notification_service.notification_service import HubSpotNotificationService

hubspot = HubSpotClient()
service = HubSpotNotificationService(hubspot)

# Test notification
service.notify_new_opportunity(
    deal_id="TEST_DEAL_ID",
    opportunity_id="O1234567",
    deal_name="Test Opportunity",
    deal_owner_id="YOUR_HUBSPOT_USER_ID"
)

"""
2. Check HubSpot:
   - Click the bell icon (top right)
   - You should see the notification
   - Click the deal to see associated task and note

3. Verify:
   - ✅ Bell notification appears
   - ✅ Task created with correct priority
   - ✅ Note added to deal timeline
   - ✅ Task associated with deal
   - ✅ Due date set correctly
"""
