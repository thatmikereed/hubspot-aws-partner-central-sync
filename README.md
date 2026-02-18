# HubSpot ↔ AWS Partner Central & Microsoft Partner Center Sync

Bidirectional integration between **HubSpot CRM**, **AWS Partner Central**, and **Microsoft Partner Center**, deployed as serverless AWS Lambda functions.

## Supported Integrations

### AWS Partner Central
```
HubSpot deal created          AWS Partner Central
  (title contains #AWS)  ──►  CreateOpportunity
                                     │
                                     ▼
HubSpot deal updated   ◄──  PC Opportunity ID written back

AWS EngagementInvitation  ──►  AcceptEngagementInvitation
    (PENDING)                         │
                                      ▼
                               HubSpot deal created
                               (with #AWS in title)
```

### Google Cloud Partners
```
HubSpot deal created          GCP Partners API
  (title contains #GCP)  ──►  CreateLead → CreateOpportunity
                                     │
                                     ▼
HubSpot deal updated   ◄──  GCP Opportunity ID written back

GCP Opportunity          ──►  Sync to HubSpot
  (scheduled poll)                   │
                                     ▼
                              HubSpot deal created
                              (with #GCP in title)
```

### Microsoft Partner Center
```
HubSpot deal created          Microsoft Partner Center
  (title contains #Microsoft)  ──►  CreateReferral
                                      │
                                      ▼
HubSpot deal updated   ◄──  Referral ID written back

Microsoft Referral       ──►  Sync to HubSpot
  (scheduled poll)                   │
  (New/Active status)                ▼
                              HubSpot deal created
                              (with #Microsoft in title)
```

> **📖 New to this integration?** See [BUILD.md](./BUILD.md) for complete step-by-step installation instructions.

## Table of Contents

- [Architecture](#architecture)
- [Prerequisites](#prerequisites)
- [Deployment](#deployment)
- [How It Works](#how-it-works)
- [Advanced Features](#advanced-features)
- [Feature Details](#feature-details)
- [Configuration](#configuration)
- [Local Development](#local-development)
- [Project Structure](#project-structure)
- [Security Notes](#security-notes)
- [Extending the Integration](#extending-the-integration)

---

## Architecture

### AWS Partner Central Integration

| Component | Purpose |
|-----------|---------|
| **`HubSpotToPartnerCentralFunction`** | API Gateway webhook receiver. Triggered by HubSpot on `deal.creation`. Filters for `#AWS` in the deal name, then calls `CreateOpportunity` on Partner Central. Writes the PC Opportunity ID back to the HubSpot deal. |
| **`PartnerCentralToHubSpotFunction`** | EventBridge-scheduled Lambda (default: every 5 minutes). Lists `PENDING` EngagementInvitations, accepts each one, fetches the opportunity details, and creates a corresponding HubSpot deal. |
| **`HubSpotPartnerCentralServiceRole`** | IAM role assumed by both Lambdas for all Partner Central API calls. Defined in `infra/iam-role.yaml`. |

### Google Cloud Partners Integration

| Component | Purpose |
|-----------|---------|
| **`HubSpotToGcpPartnersFunction`** | API Gateway webhook receiver at `/webhook/hubspot/gcp`. Triggered by HubSpot on `deal.creation`. Filters for `#GCP` in the deal name, creates a Lead and then an Opportunity in GCP Partners API. Writes the GCP Opportunity ID back to HubSpot. |
| **`GcpPartnersToHubSpotFunction`** | EventBridge-scheduled Lambda (default: every 15 minutes). Lists opportunities from GCP Partners API and syncs them to HubSpot deals. Skips opportunities that originated from HubSpot to avoid circular sync. |
### Microsoft Partner Center Integration

| Component | Purpose |
|-----------|---------|
| **`HubSpotToMicrosoftFunction`** | API Gateway webhook receiver. Triggered by HubSpot on `deal.creation`. Filters for `#Microsoft` in the deal name, then calls `CreateReferral` on Microsoft Partner Center. Writes the Referral ID back to the HubSpot deal. |
| **`MicrosoftToHubSpotFunction`** | EventBridge-scheduled Lambda (default: every 15 minutes). Lists New and Active referrals from Microsoft Partner Center and syncs them to HubSpot deals. |

### Shared Components

| Component | Purpose |
|-----------|---------|
| **API Gateway** | Regional REST API that exposes webhook endpoints to HubSpot for AWS, GCP, and Microsoft integrations. |
| **SSM Parameter Store** | Stores the HubSpot access token, GCP service account key, and Microsoft access token as SecureStrings. |
| **CloudWatch Logs** | Stores Lambda execution logs with 30-day retention for debugging and auditing. |

---

## Prerequisites

- **AWS CLI** ≥ 2.x configured with an account that has permission to create IAM roles and Lambda functions
- **AWS SAM CLI** ≥ 1.100 (`pip install aws-sam-cli`)
- **Python** 3.12
- A **HubSpot Private App** with scopes: `crm.objects.deals.read`, `crm.objects.deals.write`, `crm.schemas.deals.write`, `crm.objects.contacts.read`, `crm.objects.contacts.write`

### For AWS Partner Central Integration
- An active **AWS Partner Central** enrollment
- AWS Partner Central Solution ID (optional, for automatic solution association)

### For Google Cloud Partners Integration
- An active **Google Cloud Partner** account
- Google Cloud Partner ID from the Partners Portal
- A GCP service account with Cloud CRM Partners API permissions
- Service account JSON key file

### For Microsoft Partner Center Integration
- An active **Microsoft Partner Center** account
- Azure AD App Registration with Partner Center API permissions
- Azure AD access token with Referral Admin or Referral User role
- Admin consent granted for the application

---

## Deployment

### Step 1 — Create the IAM Role

Deploy the IAM role **first** (it must exist before the Lambdas can assume it):

```bash
aws cloudformation deploy \
  --template-file infra/iam-role.yaml \
  --stack-name hubspot-partner-central-iam \
  --capabilities CAPABILITY_NAMED_IAM

# Capture the role ARN for the next step
ROLE_ARN=$(aws cloudformation describe-stacks \
  --stack-name hubspot-partner-central-iam \
  --query "Stacks[0].Outputs[?OutputKey=='RoleArn'].OutputValue" \
  --output text)

echo "Role ARN: $ROLE_ARN"
```

### Step 2 — Build and Deploy the Lambda Functions

```bash
# Build
sam build

# Deploy (interactive — prompts for secrets)
sam deploy --guided \
  --parameter-overrides \
    "PartnerCentralRoleArn=$ROLE_ARN"
```

You will be prompted for:
- `HubSpotAccessToken` — your HubSpot Private App token (`pat-na1-...`)
- `HubSpotWebhookSecret` — optional but strongly recommended
- `GcpPartnerId` — your Google Cloud Partner ID (leave blank if not using GCP integration)
- `GcpServiceAccountKey` — Base64-encoded GCP service account JSON key (leave blank if not using GCP integration)

#### Optional: Deploy with GCP Parameters

If you want to enable both AWS and GCP integrations:

```bash
# Encode your GCP service account key
GCP_KEY_BASE64=$(base64 -w 0 path/to/gcp-service-account-key.json)

sam deploy --guided \
  --parameter-overrides \
    "PartnerCentralRoleArn=$ROLE_ARN" \
    "GcpPartnerId=12345" \
    "GcpServiceAccountKey=$GCP_KEY_BASE64"
```

### Step 3 — Register HubSpot Webhooks

#### For AWS Partner Central Integration

1. Copy the **WebhookUrl** from the SAM deploy output.
2. In HubSpot: **Settings → Integrations → Private Apps → Your App → Webhooks**
3. Create a subscription:
   - **Event type**: `deal.creation`
   - **Target URL**: the WebhookUrl from Step 2 (e.g., `https://xyz.execute-api.us-east-1.amazonaws.com/production/webhook/hubspot`)
4. Enable the subscription.

#### For Google Cloud Partners Integration

1. Copy the **GcpWebhookUrl** from the SAM deploy output.
2. In HubSpot: **Settings → Integrations → Private Apps → Your App → Webhooks**
3. Create a subscription:
   - **Event type**: `deal.creation`
   - **Target URL**: the GcpWebhookUrl from Step 2 (e.g., `https://xyz.execute-api.us-east-1.amazonaws.com/production/webhook/hubspot/gcp`)
4. Enable the subscription.

> **Note**: You can have both webhooks active simultaneously. The AWS webhook will process deals with `#AWS` tag, and the GCP webhook will process deals with `#GCP` tag.

### Step 4 — Create HubSpot Custom Properties (one-time)

The integration stores cloud partner metadata on HubSpot deals. Run this once:

```python
from src.common.hubspot_client import HubSpotClient
import os

os.environ["HUBSPOT_ACCESS_TOKEN"] = "pat-na1-your-token"
client = HubSpotClient()

# This will create properties for both AWS and GCP integrations
# Properties with names like:
# - aws_opportunity_id, aws_opportunity_title, aws_sync_status, aws_review_status
# - gcp_opportunity_id, gcp_opportunity_name, gcp_sync_status, gcp_lead_name, gcp_product_family
created = client.create_custom_properties()
print(f"Created properties: {created}")
```

Or trigger it manually — the Lambda will handle errors gracefully if properties already exist.

### Enabling Microsoft Partner Center Integration (Optional)

If you want to sync with Microsoft Partner Center in addition to AWS:

1. **Set up Azure AD App Registration:**
   - Go to Azure Portal → Azure Active Directory → App registrations
   - Create a new app registration
   - Add API permissions: Partner Center API (delegated permissions)
   - Grant admin consent for your organization
   - Generate a client secret

2. **Obtain Access Token:**
   - Use OAuth 2.0 authorization code flow or device code flow
   - Get an access token with Partner Center API scope
   - The token must have Referral Admin or Referral User role

3. **Update SAM deployment:**
   ```bash
   sam deploy \
     --parameter-overrides \
       "PartnerCentralRoleArn=$ROLE_ARN" \
       "MicrosoftAccessToken=your-microsoft-token"
   ```

4. **Register Microsoft webhook in HubSpot:**
   - Copy the **MicrosoftWebhookUrl** from SAM output
   - In HubSpot: Settings → Integrations → Private Apps → Webhooks
   - Create subscription for `deal.creation` pointing to MicrosoftWebhookUrl

5. **Create Microsoft-specific HubSpot properties:**
   ```python
   from src.common.microsoft_mappers import get_hubspot_custom_properties_for_microsoft
   from src.common.hubspot_client import HubSpotClient
   
   client = HubSpotClient()
   props = get_hubspot_custom_properties_for_microsoft()
   for prop in props:
       try:
           client.session.post(
               f"{client.base_url}/crm/v3/properties/deals",
               json=prop
           )
       except Exception as e:
           print(f"Property {prop['name']} may already exist: {e}")
   ```

---

## How It Works

### AWS Integration

#### HubSpot → AWS Partner Central

1. A sales rep creates a HubSpot deal with **`#AWS`** anywhere in the deal name (e.g., `"BigCorp Cloud Migration #AWS"`).
2. HubSpot fires a `deal.creation` webhook to the API Gateway endpoint.
3. The Lambda fetches the full deal, confirms the `#AWS` tag, and maps the fields:

| HubSpot Field | Partner Central Field |
|---|---|
| `dealname` | `Project.Title` |
| `description` | `Project.CustomerBusinessProblem` |
| `dealstage` | `LifeCycle.Stage` |
| `closedate` | `LifeCycle.TargetCloseDate` |
| `amount` | `Project.ExpectedCustomerSpend[0].Amount` |
| `deal.id` | `ClientToken` (idempotency key) |

4. The Partner Central opportunity ID is written back to the HubSpot deal's `aws_opportunity_id` property.
5. Subsequent duplicate events are detected and skipped via the `aws_opportunity_id` check.

#### AWS Partner Central → HubSpot
### HubSpot → Microsoft Partner Center

1. A sales rep creates a HubSpot deal with **`#Microsoft`** anywhere in the deal name (e.g., `"Contoso Azure Migration #Microsoft"`).
2. HubSpot fires a `deal.creation` webhook to the Microsoft webhook endpoint.
3. The Lambda maps HubSpot fields to Microsoft Partner Center referral format:

| HubSpot Field | Microsoft Partner Center Field |
|---|---|
| `dealname` | `name` |
| `description` | `details.notes` |
| `dealstage` | `status` + `substatus` |
| `closedate` | `details.closeDate` |
| `amount` | `details.dealValue` |
| `deal.id` | `externalReferenceId` |

4. The Microsoft referral ID is written back to `microsoft_referral_id` custom property.
5. Status tracking via `microsoft_status`, `microsoft_substatus`, and `microsoft_sync_status` properties.

#### Microsoft Partner Center → HubSpot

1. EventBridge triggers the sync Lambda every 15 minutes (configurable).
2. The Lambda calls Microsoft Partner Center API to list referrals with status "New" or "Active".
3. For each referral:
   - Searches HubSpot for an existing deal with the matching `microsoft_referral_id`.
   - If not found, creates a new HubSpot deal (deal name automatically includes `#Microsoft`).
   - If found and status changed, updates the deal stage to reflect current Microsoft status.
   - Writes `microsoft_referral_id`, `microsoft_status`, `microsoft_substatus`, and `microsoft_sync_status` to the deal.
   - Skips closed referrals to prevent reopening completed deals.

### AWS Partner Central → HubSpot

1. EventBridge triggers the sync Lambda every 5 minutes (configurable).
2. The Lambda assumes `HubSpotPartnerCentralServiceRole` and calls `ListEngagementInvitations` for all `PENDING` invitations.
3. For each invitation:
   - Calls `GetEngagementInvitation` to extract the linked opportunity ID.
   - Calls `AcceptEngagementInvitation`.
   - Calls `GetOpportunity` to fetch full details.
   - Creates a HubSpot deal with the opportunity data (deal name automatically includes `#AWS`).
   - Stores the `aws_invitation_id` and `aws_opportunity_id` on the deal to prevent reprocessing.

### Google Cloud Partners Integration

#### HubSpot → GCP Partners

1. A sales rep creates a HubSpot deal with **`#GCP`** anywhere in the deal name (e.g., `"Enterprise Workspace Migration #GCP"`).
2. HubSpot fires a `deal.creation` webhook to the GCP-specific API Gateway endpoint (`/webhook/hubspot/gcp`).
3. The Lambda fetches the full deal, confirms the `#GCP` tag, and performs a two-step creation:
   
   **Step 1: Create Lead**
   | HubSpot Field | GCP Lead Field |
   |---|---|
   | `company.name` | `companyName` |
   | `company.website` | `companyWebsite` |
   | `contact.firstname/lastname` | `contact.givenName/familyName` |
   | `contact.email` | `contact.email` |
   | `description` | `notes` |
   | `deal.id` | `externalSystemId` |

   **Step 2: Create Opportunity (linked to Lead)**
   | HubSpot Field | GCP Opportunity Field |
   |---|---|
   | `dealstage` | `salesStage` (QUALIFYING, QUALIFIED, PROPOSAL, etc.) |
   | `amount` | `dealSize` |
   | `closedate` | `closeDate` (year/month/day object) |
   | `gcp_product_family` | `productFamily` (GOOGLE_CLOUD_PLATFORM, GOOGLE_WORKSPACE, etc.) |
   | `description` | `notes` |
   | `hs_next_step` | `nextSteps` |

4. The GCP opportunity ID and name are written back to the HubSpot deal's `gcp_opportunity_id` and `gcp_opportunity_name` properties.
5. Subsequent duplicate events are detected and skipped via the `gcp_opportunity_id` check.

#### GCP Partners → HubSpot

1. EventBridge triggers the sync Lambda every 15 minutes (configurable).
2. The Lambda calls GCP Partners API `list_opportunities` to fetch all opportunities.
3. For each opportunity:
   - Skips opportunities with `externalSystemId` starting with `hubspot-deal-` (originated from HubSpot).
   - Fetches the associated Lead for company information.
   - Creates or updates a HubSpot deal with the opportunity data (deal name automatically includes `#GCP`).
   - Stores the `gcp_opportunity_id` and `gcp_opportunity_name` on the deal.
   - Associates contacts from the Lead if available.

---

## IAM Role — `HubSpotPartnerCentralServiceRole`

The role is defined in `infra/iam-role.yaml` and grants the following permissions:

```
partnercentral-selling:CreateOpportunity
partnercentral-selling:UpdateOpportunity
partnercentral-selling:GetOpportunity
partnercentral-selling:ListOpportunities
partnercentral-selling:ListEngagementInvitations
partnercentral-selling:GetEngagementInvitation
partnercentral-selling:AcceptEngagementInvitation
partnercentral-selling:RejectEngagementInvitation
partnercentral-selling:GetResourceSnapshot
partnercentral-selling:ListEngagements
```

The Lambda execution roles are granted only `sts:AssumeRole` on this single role — all Partner Central permissions flow through it.



## Local Development

```bash
# Install dependencies
pip install -r requirements-dev.txt

# Run tests
pytest tests/ -v

# Run a single Lambda locally with SAM
sam local invoke HubSpotToPartnerCentralFunction \
  --event tests/fixtures/sample_webhook_event.json \
  --env-vars .env.json
```

---

## Project Structure

```
.
├── infra/
│   └── iam-role.yaml                        # IAM role CloudFormation template (deploy first)
├── src/
│   ├── common/
│   │   ├── aws_client.py                    # Role assumption + Partner Central client factory
│   │   ├── microsoft_client.py              # Microsoft Partner Center API client
│   │   ├── hubspot_client.py                # HubSpot CRM API wrapper
│   │   ├── mappers.py                       # AWS Partner Central field mapping
│   │   └── microsoft_mappers.py             # Microsoft Partner Center field mapping
│   ├── hubspot_to_partner_central/
│   │   └── handler.py                       # Lambda: HubSpot webhook → AWS PC CreateOpportunity
│   ├── partner_central_to_hubspot/
│   │   └── handler.py                       # Lambda: scheduled AWS PC invitation poll → HubSpot
│   ├── hubspot_to_microsoft/
│   │   └── handler.py                       # Lambda: HubSpot webhook → Microsoft PC CreateReferral
│   └── microsoft_to_hubspot/
│       └── handler.py                       # Lambda: scheduled Microsoft PC referral poll → HubSpot
├── tests/
│   ├── conftest.py
│   ├── test_hubspot_to_partner_central.py
│   ├── test_partner_central_to_hubspot.py
│   ├── test_mappers.py
│   ├── test_microsoft_client.py
│   └── test_microsoft_mappers.py
├── template.yaml                            # AWS SAM / CloudFormation template
├── samconfig.toml                       # SAM deployment configuration
├── requirements.txt                     # Lambda runtime dependencies
├── requirements-dev.txt                 # Development / test dependencies
└── .env.example                         # Environment variable reference
```

---

## Security Notes

### AWS Partner Central
- The HubSpot access token is stored in **SSM Parameter Store** (SecureString) and injected as an environment variable — never hardcoded.
- Webhook requests are verified using HubSpot's HMAC-SHA256 signature scheme when `HUBSPOT_WEBHOOK_SECRET` is set.
- The `HubSpotPartnerCentralServiceRole` uses an `ExternalId` condition (`HubSpotPartnerCentralIntegration`) to prevent confused-deputy attacks.
- All Lambda functions log to CloudWatch with a 30-day retention policy. Sensitive data is redacted from logs.
- No Partner Central credentials or tokens are stored — access is always via short-lived STS credentials from the assumed role.

### Microsoft Partner Center
- The Microsoft access token is stored in **SSM Parameter Store** (SecureString) and injected as an environment variable.
- Microsoft Partner Center API uses Azure AD OAuth 2.0 authentication with delegated permissions.
- Access tokens should be rotated regularly and should use the minimum required permissions (Referral User or Referral Admin).
- API calls use HTTPS with TLS 1.2+ for encryption in transit.

### General
- All external inputs are validated and sanitized to prevent injection attacks.
- Idempotency is enforced using unique identifiers (deal IDs, opportunity IDs, referral IDs) to prevent duplicate operations.

**For detailed security information and best practices, see [SECURITY.md](./SECURITY.md).**

---

## Advanced Features

This integration includes comprehensive bidirectional sync capabilities:

### Core Features

1. **Bidirectional Deal Sync** — Real-time sync of HubSpot deal property changes (stage, close date, amount, description) to Partner Central
2. **Contact & Company Sync** — Automatic sync of contact and company updates to all associated opportunities
3. **Opportunity Submission** — Submit opportunities to AWS for co-sell review with status tracking
4. **AWS Summary Sync** — Sync engagement scores, review status, AWS feedback, and next steps
5. **Multi-Solution Association** — Intelligent solution matching and automatic association based on deal characteristics

### Real-Time Event Processing

6. **EventBridge Integration** — Instant processing of Partner Central events (invitations, opportunity updates)
7. **Smart Notifications** — Intelligent alerts for critical events (engagement score changes ±15 points, review status changes, AWS seller assignments)
8. **Reverse Sync** — Partner Central changes flow back to HubSpot within seconds

### Resource & Team Management

9. **Resource Snapshot Sync** — Auto-sync AWS resources (whitepapers, case studies, solution briefs) to HubSpot deals
10. **Engagement Lifecycle** — Track engagement status, team members, and milestones
11. **Team Assignment** — Automatic sync of deal owner changes to Partner Central

### Developer Tools

12. **Solution Management API** — REST endpoints to list, search, and browse Partner Central solutions dynamically
13. **Conflict Detection** — Detect and resolve simultaneous updates with configurable strategies
14. **Audit Trail** — Permanent audit logging in DynamoDB with 7-year retention for compliance

---

## Feature Details

### Bidirectional Sync
Changes flow automatically in both directions:
- HubSpot → Partner Central: Deal creation, property updates, contact/company changes, team assignments
- Partner Central → HubSpot: Engagement invitations, opportunity updates, review status, engagement scores, AWS resources

### Smart Notifications
Intelligent alerting system that creates HubSpot tasks and notes for:
- **Engagement Score Changes**: Notifies when score increases/decreases by ±15 points (configurable)
- **Review Status Updates**: Alerts on Approved, Action Required, or Rejected status
- **AWS Seller Assignment**: Notifies when AWS assigns or changes the seller
- **Notifications include**: Clear action items, due dates (24hr for high priority), and contextual information

### Solution Management
Dynamic solution discovery and association:
- **Auto-matching**: Intelligently ranks solutions by relevance based on use case, industry, keywords, and categories
- **REST API**: Search and browse all Partner Central solutions programmatically
- **Manual Override**: Specify custom solution IDs via HubSpot properties when needed

### Resource Sync
Automatic sharing of AWS resources:
- Syncs whitepapers, case studies, reference architectures, presentations, training materials
- Creates formatted HubSpot notes with links and descriptions
- Runs every 4 hours (configurable) to capture new resources
- Tracks synced resources to prevent duplicates

### Audit & Compliance
Enterprise-grade tracking and conflict resolution:
- **Permanent Audit Trail**: DynamoDB storage with 7-year retention
- **Conflict Detection**: Identifies simultaneous updates in both systems
- **Resolution Strategies**: Last-write-wins, HubSpot-wins, Partner-Central-wins, or manual resolution
- **Compliance Reports**: Full change history for regulatory requirements

---

## Configuration

### Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `HUBSPOT_ACCESS_TOKEN` | ✅ | — | HubSpot Private App token |
| `HUBSPOT_WEBHOOK_SECRET` | ⚠️ | — | Webhook HMAC signing secret (strongly recommended) |
| `PARTNER_CENTRAL_ROLE_ARN` | ⚠️ | Auto-constructed | Full ARN of the service role |
| `AWS_REGION` | ✅ | — | AWS region for Partner Central API calls |
| `ENVIRONMENT` | ✅ | development | `production` \| `staging` \| `development` |
| `ENGAGEMENT_SCORE_THRESHOLD` | — | 15 | Notification threshold for score changes |
| `HIGH_ENGAGEMENT_SCORE` | — | 80 | High priority threshold |
| `NOTIFICATION_SNS_TOPIC_ARN` | — | — | Optional SNS topic for external notifications |

### Scheduled Tasks

| Task | Default Interval | Configurable | Purpose |
|---|---|---|---|
| Invitation Poll | 5 minutes | ✅ | Check for new engagement invitations |
| AWS Summary Sync | 60 minutes | ✅ | Fetch engagement scores and AWS feedback |
| Resource Sync | 4 hours | ✅ | Sync AWS-provided resources |
| Smart Notifications | 30 minutes | ✅ | Check for critical events |
| Engagement Lifecycle | 30 minutes | ✅ | Sync engagement status and team |

---

## Extending the Integration

**Add more fields**: Edit `src/common/mappers.py` — both mapping functions are documented and straightforward to extend.

**Change sync intervals**: Update the respective parameter in `template.yaml` and redeploy.

**Add custom notifications**: Extend `src/smart_notifications/handler.py` to add custom notification rules and thresholds.

**Integrate with Slack/Email**: Configure an SNS topic ARN to send notifications to external systems (Slack webhooks, email, etc.).

**Custom solutions matching**: Modify `src/common/solution_matcher.py` to adjust ranking algorithm or add custom matching rules.
