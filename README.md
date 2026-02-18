# HubSpot ↔ AWS Partner Central Sync

Bidirectional integration between **HubSpot CRM** and **AWS Partner Central**, deployed as serverless AWS Lambda functions.

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

---

## Architecture

| Component | Purpose |
|-----------|---------|
| **`HubSpotToPartnerCentralFunction`** | API Gateway webhook receiver. Triggered by HubSpot on `deal.creation`. Filters for `#AWS` in the deal name, then calls `CreateOpportunity` on Partner Central. Writes the PC Opportunity ID back to the HubSpot deal. |
| **`PartnerCentralToHubSpotFunction`** | EventBridge-scheduled Lambda (default: every 5 minutes). Lists `PENDING` EngagementInvitations, accepts each one, fetches the opportunity details, and creates a corresponding HubSpot deal. |
| **`HubSpotPartnerCentralServiceRole`** | IAM role assumed by both Lambdas for all Partner Central API calls. Defined in `infra/iam-role.yaml`. |
| **API Gateway** | Regional REST API that exposes the webhook endpoint to HubSpot. |
| **SSM Parameter Store** | Stores the HubSpot access token as a SecureString. |

---

## Prerequisites

- **AWS CLI** ≥ 2.x configured with an account that has permission to create IAM roles and Lambda functions
- **AWS SAM CLI** ≥ 1.100 (`pip install aws-sam-cli`)
- **Python** 3.12
- A **HubSpot Private App** with scopes: `crm.objects.deals.read`, `crm.objects.deals.write`, `crm.schemas.deals.write`
- An active **AWS Partner Central** enrollment

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

### Step 3 — Register the HubSpot Webhook

1. Copy the **WebhookUrl** from the SAM deploy output.
2. In HubSpot: **Settings → Integrations → Private Apps → Your App → Webhooks**
3. Create a subscription:
   - **Event type**: `deal.creation`
   - **Target URL**: the WebhookUrl from Step 2
4. Enable the subscription.

### Step 4 — Create HubSpot Custom Properties (one-time)

The integration stores AWS metadata on HubSpot deals. Run this once:

```python
from src.common.hubspot_client import HubSpotClient
import os

os.environ["HUBSPOT_ACCESS_TOKEN"] = "pat-na1-your-token"
client = HubSpotClient()
created = client.create_custom_properties()
print(f"Created properties: {created}")
```

Or trigger it manually — the Lambda will handle errors gracefully if properties already exist.

---

## How It Works

### HubSpot → Partner Central

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

### Partner Central → HubSpot

1. EventBridge triggers the sync Lambda every 5 minutes (configurable).
2. The Lambda assumes `HubSpotPartnerCentralServiceRole` and calls `ListEngagementInvitations` for all `PENDING` invitations.
3. For each invitation:
   - Calls `GetEngagementInvitation` to extract the linked opportunity ID.
   - Calls `AcceptEngagementInvitation`.
   - Calls `GetOpportunity` to fetch full details.
   - Creates a HubSpot deal with the opportunity data (deal name automatically includes `#AWS`).
   - Stores the `aws_invitation_id` and `aws_opportunity_id` on the deal to prevent reprocessing.

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

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `HUBSPOT_ACCESS_TOKEN` | ✅ | HubSpot Private App token |
| `HUBSPOT_WEBHOOK_SECRET` | ⚠️ Recommended | Webhook HMAC signing secret |
| `PARTNER_CENTRAL_ROLE_ARN` | ⚠️ Recommended | Full ARN of the service role. Auto-constructed from account ID if blank. |
| `AWS_REGION` | ✅ | AWS region for Partner Central API calls |
| `ENVIRONMENT` | ✅ | `production` \| `staging` \| `development` |

Copy `.env.example` to `.env` for local development.

---

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
│   └── iam-role.yaml                    # IAM role CloudFormation template (deploy first)
├── src/
│   ├── common/
│   │   ├── aws_client.py                # Role assumption + Partner Central client factory
│   │   ├── hubspot_client.py            # HubSpot CRM API wrapper
│   │   └── mappers.py                   # Bidirectional field mapping
│   ├── hubspot_to_partner_central/
│   │   └── handler.py                   # Lambda: HubSpot webhook → PC CreateOpportunity
│   └── partner_central_to_hubspot/
│       └── handler.py                   # Lambda: scheduled PC invitation poll → HubSpot deal
├── tests/
│   ├── conftest.py
│   ├── test_hubspot_to_partner_central.py
│   ├── test_partner_central_to_hubspot.py
│   └── test_mappers.py
├── template.yaml                        # AWS SAM / CloudFormation template
├── samconfig.toml                       # SAM deployment configuration
├── requirements.txt                     # Lambda runtime dependencies
├── requirements-dev.txt                 # Development / test dependencies
└── .env.example                         # Environment variable reference
```

---

## Security Notes

- The HubSpot access token is stored in **SSM Parameter Store** (SecureString) and injected as an environment variable — never hardcoded.
- Webhook requests are verified using HubSpot's HMAC-SHA256 signature scheme when `HUBSPOT_WEBHOOK_SECRET` is set.
- The `HubSpotPartnerCentralServiceRole` uses an `ExternalId` condition (`HubSpotPartnerCentralIntegration`) to prevent confused-deputy attacks.
- All Lambda functions log to CloudWatch with a 30-day retention policy. Sensitive data is redacted from logs.
- No Partner Central credentials or tokens are stored — access is always via short-lived STS credentials from the assumed role.
- All external inputs are validated and sanitized to prevent injection attacks.

**For detailed security information and best practices, see [SECURITY.md](./SECURITY.md).**

---

## Additional Features

This integration includes advanced features for enhanced bidirectional sync. See **[FEATURES.md](FEATURES.md)** for the original 5 advanced features and **[NEW-FEATURES.md](NEW-FEATURES.md)** for the 4 newest additions:

### New Bidirectional Features (See NEW-FEATURES.md)

1. **HubSpot Deal Update Sync** — Real-time sync of HubSpot deal property changes to Partner Central
2. **Solution Management API** — List, search, and browse Partner Central solutions via REST API
3. **Engagement Resource Snapshot Sync** — Auto-sync AWS resources (whitepapers, case studies) to HubSpot deals
4. **Smart Notification System** — Intelligent alerts for critical Partner Central events (engagement scores, review status, seller assignments)

### Original Advanced Features (See FEATURES.md)

1. **Opportunity Submission to AWS** — Submit opportunities for co-sell review
2. **AWS Opportunity Summary Sync** — Sync engagement scores and AWS feedback
3. **EventBridge Real-Time Event Handling** — Instant invitation acceptance and updates
4. **Reverse Sync** — Partner Central → HubSpot updates
5. **Multi-Solution Auto-Association** — Intelligent solution matching and linking

---

## Extending the Integration

**Add more fields**: Edit `src/common/mappers.py` — both mapping functions are documented and straightforward to extend.

**Change the poll interval**: Update `InvitationPollIntervalMinutes` in `samconfig.toml` and redeploy.

**Add custom notifications**: Extend `src/smart_notifications/handler.py` to add custom notification rules.

**Integrate with Slack/Email**: Configure the SNS topic ARN in `template-new-features.yaml` to send notifications externally.
