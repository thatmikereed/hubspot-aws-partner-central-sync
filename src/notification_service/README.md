# HubSpot User Notification System

## Overview

A comprehensive notification system that sends **HubSpot bell notifications** (via task assignments) and timeline notes for all AWS Partner Central events.

When a task is assigned to a HubSpot user via the Engagements API, they automatically receive a **bell notification** in HubSpot (red badge on bell icon in navigation bar).

---

## Features

### ✅ Notification Types

All notifications create:
1. **HubSpot Task** (triggers 🔔 bell notification)
   - Assigned to deal owner
   - Priority-based (HIGH/MEDIUM/LOW)
   - Due date based on urgency
   - Associated with the deal

2. **HubSpot Note** (appears in timeline)
   - Full context and details
   - Action items
   - Associated with the deal

### ✅ Events Covered

| Event | Priority | Due Time | Description |
|-------|----------|----------|-------------|
| **New Opportunity from AWS** | HIGH | 24 hours | AWS sends co-sell invitation |
| **Opportunity Updated by AWS** | MEDIUM | 3 days | AWS syncs changes back |
| **Engagement Score Changed** | HIGH/MEDIUM/LOW | Variable | Score increases/decreases |
| **Review Status: Approved** | HIGH | 24 hours | AWS approved co-sell |
| **Review Status: Action Required** | HIGH | 48 hours | AWS needs more info |
| **Review Status: Rejected** | MEDIUM | 3 days | Not approved |
| **Submission Confirmed** | MEDIUM | 3 days | Submitted to AWS |
| **AWS Seller Assigned** | HIGH | 24 hours | AWS assigned seller |
| **Resources Available** | LOW | 7 days | New AWS resources synced |
| **Conflict Detected** | HIGH | 12 hours | Data sync conflict |

---

## Architecture

### Components

```
┌─────────────────────────────────────────────────────┐
│                Lambda Handlers                      │
│  (partner_central_to_hubspot, sync_aws_summary,    │
│   submit_opportunity, eventbridge_events, etc.)    │
└───────────────┬─────────────────────────────────────┘
                │
                │ import from notification_service.integration
                │
                ▼
┌─────────────────────────────────────────────────────┐
│          Integration Helpers                        │
│  notify_from_invitation()                          │
│  notify_from_score_change()                        │
│  notify_from_status_change()                       │
│  notify_from_submission()                          │
│  notify_from_seller_assignment()                   │
│  notify_from_resources()                           │
│  notify_from_conflict()                            │
└───────────────┬─────────────────────────────────────┘
                │
                ▼
┌─────────────────────────────────────────────────────┐
│      HubSpotNotificationService                     │
│  notify_new_opportunity()                          │
│  notify_opportunity_updated()                      │
│  notify_engagement_score_change()                  │
│  notify_review_status_change()                     │
│  notify_submission_confirmed()                     │
│  notify_aws_seller_assigned()                      │
│  notify_resources_available()                      │
│  notify_conflict_detected()                        │
└───────────────┬─────────────────────────────────────┘
                │
                ▼
┌─────────────────────────────────────────────────────┐
│              HubSpot API                            │
│  POST /crm/v3/objects/tasks (triggers 🔔)          │
│  POST /crm/v3/objects/notes                        │
│  PUT /crm/v4/objects/.../associations              │
└─────────────────────────────────────────────────────┘
                │
                ▼
┌─────────────────────────────────────────────────────┐
│           HubSpot User Interface                    │
│  🔔 Bell notification (top right)                   │
│  📋 Task in user's task list                        │
│  📝 Note in deal timeline                           │
└─────────────────────────────────────────────────────┘
```

---

## Installation

### 1. Add to Lambda Layers

The notification service is already included in the common code. No additional dependencies needed.

### 2. Update Handler Imports

Add to the top of any handler that should send notifications:

```python
import sys
sys.path.insert(0, "/var/task")
from notification_service.integration import (
    notify_from_invitation,
    notify_from_score_change,
    notify_from_status_change,
    # ... others as needed
)
```

### 3. Call Notification Functions

See [INTEGRATION_EXAMPLES.py](./INTEGRATION_EXAMPLES.py) for complete examples.

---

## Usage Examples

### Example 1: New Opportunity Notification

```python
# In partner_central_to_hubspot/handler.py
from notification_service.integration import notify_from_invitation

def lambda_handler(event, context):
    # ... create deal from AWS invitation ...
    
    # Send notification
    notify_from_invitation(
        hubspot_client=hubspot,
        deal_id=deal_id,
        opportunity_id=opportunity_id,
        deal=deal
    )
```

**Result:**
- ✅ Bell notification appears for deal owner
- ✅ HIGH priority task created (due in 24 hours)
- ✅ Note added to deal timeline
- ✅ Task includes action items

### Example 2: Engagement Score Change

```python
# In sync_aws_summary/handler.py
from notification_service.integration import notify_from_score_change

# After fetching new score
if abs(new_score - old_score) >= 10:
    notify_from_score_change(
        hubspot_client=hubspot,
        deal_id=deal_id,
        opportunity_id=opportunity_id,
        deal=deal,
        old_score=old_score,
        new_score=new_score
    )
```

**Result:**
- ✅ Bell notification
- ✅ Priority based on score level
- ✅ Action items specific to score change
- ✅ Due date based on urgency

### Example 3: Review Status Change

```python
# In eventbridge_events/handler.py
from notification_service.integration import notify_from_status_change

notify_from_status_change(
    hubspot_client=hubspot,
    deal_id=deal_id,
    opportunity_id=opportunity_id,
    deal=deal,
    old_status="Submitted",
    new_status="Approved",
    feedback=None  # Optional AWS feedback
)
```

**Result:**
- ✅ HIGH priority bell notification
- ✅ Celebration message for Approved status
- ✅ Urgent action items for Action Required
- ✅ Review guidance for Rejected

---

## Notification Details

### Priority Levels

| Priority | Due Time | Use Cases |
|----------|----------|-----------|
| **HIGH** | 12-24 hours | New opportunities, approvals, conflicts |
| **MEDIUM** | 2-3 days | Updates, submissions, rejections |
| **LOW** | 7 days | Resources, positive signals |

### Bell Notification Behavior

- **When task is created via API:** User receives bell notification immediately
- **Bell icon shows:** Red badge with unread count
- **Clicking bell:** Shows notification list
- **Clicking notification:** Opens the task details
- **Task is associated:** With the relevant deal

### Note Format

All notes include:
- Emoji icon for visual identification
- Clear title
- Detailed context
- Actionable next steps
- Relevant data and links

---

## Configuration

### Environment Variables

None required - uses existing HubSpot client configuration.

### Notification Thresholds

Configurable in `notification_service.py`:

```python
# Engagement score change threshold
SCORE_CHANGE_THRESHOLD = 10  # Notify on ±10 point changes

# High engagement score threshold
HIGH_ENGAGEMENT_SCORE = 80  # Scores 80+ are high priority

# Task due date hours by priority
DUE_HOURS = {
    "HIGH": 24,
    "MEDIUM": 72,
    "LOW": 168
}
```

---

## Testing

### Manual Testing

1. **Create a test opportunity:**
```python
from common.hubspot_client import HubSpotClient
from notification_service.notification_service import HubSpotNotificationService

hubspot = HubSpotClient()
service = HubSpotNotificationService(hubspot)

service.notify_new_opportunity(
    deal_id="YOUR_TEST_DEAL_ID",
    opportunity_id="O1234567",
    deal_name="Test Opportunity",
    deal_owner_id="YOUR_HUBSPOT_USER_ID"
)
```

2. **Check HubSpot:**
   - Click bell icon (top right)
   - Verify notification appears
   - Click to open task
   - Check deal timeline for note

3. **Verify:**
   - ✅ Bell notification received
   - ✅ Task created with priority
   - ✅ Due date set correctly
   - ✅ Task associated with deal
   - ✅ Note in timeline

### Integration Testing

Run existing Lambda function with test events to verify notifications are sent.

### Unit Tests

See `tests/test_notification_service.py` for comprehensive test coverage.

---

## Troubleshooting

### Bell Notification Not Appearing

**Possible causes:**
1. **Deal owner not set:** Task must be assigned to a valid HubSpot user ID
2. **User notifications disabled:** Check user's notification settings in HubSpot
3. **API permissions:** Verify private app has `tasks` write permission
4. **User email limit:** HubSpot limits 1,000 email notifications/day per user

**Solution:**
```python
# Verify deal owner ID exists
deal = hubspot.get_deal(deal_id)
owner_id = deal.get("properties", {}).get("hubspot_owner_id")
if not owner_id:
    logger.error("Deal %s has no owner - cannot send notification", deal_id)
```

### Task Created But No Notification

**Cause:** HubSpot only sends bell notifications when:
- User has notifications enabled
- Task is assigned to them (not created by them)
- They're not currently viewing the task

**Solution:** Tasks created via API should trigger notifications. If not, check user's notification preferences in HubSpot Settings.

### Notification Sent Multiple Times

**Cause:** Lambda retries on failure, causing duplicate notifications.

**Solution:** Add idempotency check:
```python
# Check if notification already sent
existing_tasks = hubspot.search_tasks_for_deal(
    deal_id,
    subject_contains="AWS Co-Sell Opportunity"
)
if existing_tasks:
    logger.info("Notification already sent")
    return
```

---

## API Reference

### Integration Functions

All functions are in `notification_service/integration.py`:

#### `notify_from_invitation(hubspot_client, deal_id, opportunity_id, deal)`
Send notification for new AWS opportunity from invitation.

#### `notify_from_sync(hubspot_client, deal_id, opportunity_id, deal, changes)`
Send notification when AWS syncs changes back to HubSpot.

#### `notify_from_score_change(hubspot_client, deal_id, opportunity_id, deal, old_score, new_score)`
Send notification for engagement score changes.

#### `notify_from_status_change(hubspot_client, deal_id, opportunity_id, deal, old_status, new_status, feedback=None)`
Send notification for review status changes.

#### `notify_from_submission(hubspot_client, deal_id, opportunity_id, deal, involvement_type)`
Send notification when opportunity is submitted to AWS.

#### `notify_from_seller_assignment(hubspot_client, deal_id, opportunity_id, deal, seller_name, seller_email=None)`
Send notification when AWS assigns a seller.

#### `notify_from_resources(hubspot_client, deal_id, opportunity_id, deal, resource_count, resource_types)`
Send notification when new AWS resources are available.

#### `notify_from_conflict(hubspot_client, deal_id, opportunity_id, deal, conflicts)`
Send notification when sync conflicts are detected.

---

## Best Practices

### 1. Non-Blocking Notifications

Always wrap notification calls in try/except to prevent failures:

```python
try:
    notify_from_invitation(hubspot, deal_id, opportunity_id, deal)
    logger.info("✅ Notification sent")
except Exception as e:
    logger.warning("⚠️ Notification failed: %s", e)
    # Continue processing - don't fail the entire handler
```

### 2. Conditional Notifications

Only send notifications for significant events:

```python
# Only notify if score change is ±10 or more
if abs(new_score - old_score) >= 10:
    notify_from_score_change(...)
```

### 3. Clear Action Items

Notifications should always include specific next steps:
- ✅ "Schedule joint call within 24 hours"
- ❌ "Review opportunity"

### 4. Priority Discipline

Use priority levels correctly:
- **HIGH:** Requires action within 24 hours
- **MEDIUM:** Review within 2-3 days
- **LOW:** Informational, 7+ days

### 5. Emoji Usage

Use emojis consistently for visual identification:
- 🆕 New
- 🔄 Updated
- 🎯 Score increase
- ⚠️ Warning/decrease
- ✅ Approved
- ❌ Rejected
- 📤 Submitted
- 👤 Person
- 📚 Resources

---

## Performance

### Impact

- **API calls per notification:** 4-5 (create task, associate task, create note, associate note)
- **Latency:** ~200-500ms per notification
- **Rate limits:** HubSpot API rate limits apply (100 requests/10 seconds)

### Optimization

Notifications are fire-and-forget:
- Don't wait for response
- Log errors but don't fail handler
- Batch where possible

---

## Roadmap

### Planned Enhancements

- [ ] Notification preferences per user
- [ ] Digest mode (daily summary email)
- [ ] Slack integration via SNS
- [ ] Custom notification templates
- [ ] Notification history dashboard
- [ ] A/B testing for notification content
- [ ] ML-based priority optimization

---

## Support

For issues or questions:
1. Check [INTEGRATION_EXAMPLES.py](./INTEGRATION_EXAMPLES.py)
2. Review logs in CloudWatch
3. Test manually with `notification_service.py`
4. Open GitHub issue with details

---

## License

MIT License - See repository LICENSE file.
