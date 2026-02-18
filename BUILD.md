# Build and Installation Instructions

This guide provides step-by-step instructions for installing and configuring the HubSpot ↔ AWS Partner Central synchronization integration.

## Table of Contents

- [Prerequisites](#prerequisites)
- [Overview](#overview)
- [Part 1: HubSpot Setup](#part-1-hubspot-setup)
- [Part 2: AWS Setup](#part-2-aws-setup)
- [Part 3: Deploy the Integration](#part-3-deploy-the-integration)
- [Part 4: Configure HubSpot Webhooks](#part-4-configure-hubspot-webhooks)
- [Part 5: Create HubSpot Custom Properties](#part-5-create-hubspot-custom-properties)
- [Part 6: Verification](#part-6-verification)
- [Variable Reference](#variable-reference)
- [Troubleshooting](#troubleshooting)

---

## Prerequisites

Before you begin, ensure you have:

- **HubSpot Account**: An active HubSpot CRM instance with admin access
- **AWS Account**: An active AWS account with administrative privileges
- **AWS Partner Central**: Active enrollment in AWS Partner Central program
- **Local Development Tools**:
  - AWS CLI v2.x or later ([installation guide](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html))
  - AWS SAM CLI v1.100 or later (`pip install aws-sam-cli`)
  - Python 3.12
  - Git

Configure your AWS CLI with credentials that have permission to:
- Create IAM roles and policies
- Deploy CloudFormation stacks
- Create Lambda functions
- Create API Gateway resources
- Create EventBridge rules
- Create SSM parameters

```bash
aws configure
```

---

## Overview

The installation process consists of these main steps:

1. **HubSpot Setup** - Create a Private App and obtain access token
2. **AWS IAM Setup** - Deploy the service role for Partner Central API access
3. **Deploy Integration** - Build and deploy Lambda functions using AWS SAM
4. **Configure Webhooks** - Register the webhook endpoint in HubSpot
5. **Create Custom Properties** - Set up HubSpot custom fields for AWS metadata
6. **Verify** - Test the integration end-to-end

---

## Part 1: HubSpot Setup

### Step 1.1: Create a HubSpot Private App

1. Log in to your HubSpot account
2. Navigate to **Settings** (gear icon in top navigation)
3. In the left sidebar, go to **Integrations** → **Private Apps**
4. Click **Create a private app**
5. In the **Basic Info** tab:
   - **Name**: `AWS Partner Central Sync` (or your preferred name)
   - **Description**: `Bidirectional sync between HubSpot and AWS Partner Central`
6. In the **Scopes** tab, select the following scopes:
   - ✅ `crm.objects.deals.read` - Read deals
   - ✅ `crm.objects.deals.write` - Create and update deals
   - ✅ `crm.objects.companies.read` - Read company data
   - ✅ `crm.objects.contacts.read` - Read contact data
   - ✅ `crm.schemas.deals.write` - Create custom properties on deals
   - ✅ `crm.objects.notes.write` - Add notes to deals (for AWS feedback)
7. Click **Create app**
8. Review and accept the permissions dialog

### Step 1.2: Save Your Access Token

After creating the app, HubSpot will display your access token **once**:

1. Copy the access token (format: `pat-na1-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx`)
2. **Save it securely** - you'll need this as `HUBSPOT_ACCESS_TOKEN` later
3. ⚠️ **Important**: This token will not be shown again. If lost, you'll need to regenerate it.

**Variable to save:**
```
HUBSPOT_ACCESS_TOKEN=pat-na1-your-token-here
```

### Step 1.3: Generate Webhook Secret (Optional but Recommended)

For webhook signature verification:

1. Generate a random secret string (32+ characters recommended):
   ```bash
   python3 -c "import secrets; print(secrets.token_urlsafe(32))"
   ```
2. Save this value as `HUBSPOT_WEBHOOK_SECRET`
3. You'll configure this in HubSpot's webhook settings later

**Variable to save:**
```
HUBSPOT_WEBHOOK_SECRET=your-generated-secret-here
```

---

## Part 2: AWS Setup

### Step 2.1: Clone the Repository

```bash
git clone https://github.com/thatmikereed/hubspot-aws-partner-central-sync.git
cd hubspot-aws-partner-central-sync
```

### Step 2.2: Deploy the IAM Service Role

The Lambda functions assume an IAM role to access AWS Partner Central APIs. This role must be created **before** deploying the Lambda functions.

1. Deploy the IAM role using CloudFormation:

```bash
aws cloudformation deploy \
  --template-file infra/iam-role.yaml \
  --stack-name hubspot-partner-central-iam \
  --capabilities CAPABILITY_NAMED_IAM \
  --region us-east-1
```

2. Capture the role ARN for later use:

```bash
PARTNER_CENTRAL_ROLE_ARN=$(aws cloudformation describe-stacks \
  --stack-name hubspot-partner-central-iam \
  --query "Stacks[0].Outputs[?OutputKey=='RoleArn'].OutputValue" \
  --output text \
  --region us-east-1)

echo "Role ARN: $PARTNER_CENTRAL_ROLE_ARN"
```

**Variable to save:**
```
PARTNER_CENTRAL_ROLE_ARN=arn:aws:iam::123456789012:role/HubSpotPartnerCentralServiceRole
```

> **Note**: The role grants permissions for:
> - Creating and updating Partner Central opportunities
> - Listing and accepting engagement invitations
> - Getting resource snapshots and engagements
> - All operations scoped to Partner Central Selling API

### Step 2.3: Find Your Partner Central Solution ID (Optional)

To submit opportunities to AWS for co-sell, you need your Solution ID:

1. Log in to [AWS Partner Central](https://partnercentral.awspartner.com/)
2. Navigate to **Solutions** → **My Solutions**
3. Find your solution and note the Solution ID (format: `S-0000001`)

Alternatively, use the AWS CLI:
```bash
aws partnercentral-selling list-solutions \
  --catalog "AWS" \
  --region us-east-1
```

**Variable to save (if applicable):**
```
PARTNER_CENTRAL_SOLUTION_ID=S-0000001
```

> **Note**: If you don't have a solution yet, you can leave this blank initially. Opportunities will still be created but cannot be submitted to AWS for review until a solution is associated.

---

## Part 3: Deploy the Integration

### Step 3.1: Build the Lambda Functions

Use AWS SAM to build the Lambda packages:

```bash
sam build
```

Expected output:
```
Build Succeeded

Built Artifacts  : .aws-sam/build
Built Template   : .aws-sam/build/template.yaml
```

### Step 3.2: Deploy with SAM

Deploy the stack using SAM guided deployment:

```bash
sam deploy --guided \
  --parameter-overrides \
    PartnerCentralRoleArn="$PARTNER_CENTRAL_ROLE_ARN"
```

The guided deployment will prompt you for:

| Parameter | Description | Example Value |
|-----------|-------------|---------------|
| **Stack Name** | CloudFormation stack name | `hubspot-partner-central-sync` |
| **AWS Region** | Deployment region | `us-east-1` |
| **HubSpotAccessToken** | Your Private App token from Step 1.2 | `pat-na1-xxx...` |
| **HubSpotWebhookSecret** | Webhook signing secret from Step 1.3 | `your-secret` |
| **PartnerCentralRoleArn** | IAM role ARN from Step 2.2 | `arn:aws:iam::123...:role/...` |
| **PartnerCentralSolutionId** | Your solution ID (optional) | `S-0000001` or leave blank |
| **InvitationPollIntervalMinutes** | How often to check for invitations | `5` (default) |
| **Environment** | Deployment environment | `production` |
| **Confirm changes before deploy** | Review changeset | `Y` |
| **Allow SAM CLI IAM role creation** | Let SAM create execution roles | `Y` |
| **Disable rollback** | Rollback on failure | `N` |
| **Save arguments to config file** | Save for future deploys | `Y` |
| **SAM configuration file** | Config file name | `samconfig.toml` (default) |
| **SAM configuration environment** | Config environment | `default` |

### Step 3.3: Note the Webhook URL

After successful deployment, SAM will output the webhook URL:

```
CloudFormation outputs from deployed stack
---------------------------------------------------------------------------
Outputs
---------------------------------------------------------------------------
Key                 WebhookUrl
Description         HubSpot webhook URL — register this in HubSpot
Value               https://abc123xyz.execute-api.us-east-1.amazonaws.com/production/webhook/hubspot
---------------------------------------------------------------------------
```

**Variable to save:**
```
WEBHOOK_URL=https://your-api-id.execute-api.us-east-1.amazonaws.com/production/webhook/hubspot
```

---

## Part 4: Configure HubSpot Webhooks

### Step 4.1: Register the Webhook Subscription

1. Go back to HubSpot: **Settings** → **Integrations** → **Private Apps**
2. Find your `AWS Partner Central Sync` app and click on it
3. Navigate to the **Webhooks** tab
4. Click **Create subscription**
5. Configure the subscription:
   - **Event type**: `deal.creation`
   - **Target URL**: Paste the `WEBHOOK_URL` from Step 3.3
   - **Authentication**: None (authentication is handled via signature verification)
6. If you generated a webhook secret in Step 1.3:
   - Copy your `HUBSPOT_WEBHOOK_SECRET`
   - In the webhook settings, there's no explicit field for the secret in HubSpot UI
   - The secret is used by your Lambda to verify incoming requests
7. Click **Create subscription**
8. **Enable** the subscription (toggle switch)

### Step 4.2: Test the Webhook (Optional)

HubSpot provides a **Test** button in the webhook UI:

1. Click **Test** next to your subscription
2. Check CloudWatch Logs to verify the Lambda received the test event:
   ```bash
   aws logs tail /aws/lambda/hubspot-to-partner-central-production --follow
   ```

---

## Part 5: Create HubSpot Custom Properties

The integration stores AWS metadata in custom HubSpot deal properties. These must be created once.

### Option A: Automatic Creation (Recommended)

The easiest way is to let the Lambda create properties automatically on first run. The Lambda includes error handling that gracefully handles existing properties.

Create a test deal with `#AWS` in the title, and the Lambda will attempt to create properties.

### Option B: Manual Creation via Python Script

If you prefer to create properties before first use:

1. Install dependencies:
   ```bash
   pip install requests
   ```

2. Run the property creation script:
   ```python
   import os
   from src.common.hubspot_client import HubSpotClient
   
   os.environ["HUBSPOT_ACCESS_TOKEN"] = "pat-na1-your-token-here"
   client = HubSpotClient()
   created = client.create_custom_properties()
   print(f"Created properties: {created}")
   ```

### Option C: Manual Creation via HubSpot UI

To create properties manually in HubSpot:

1. Go to **Settings** → **Properties**
2. Select object type: **Deals**
3. Click **Create property** for each of the following:

| Property Name | Label | Type | Description |
|---------------|-------|------|-------------|
| `aws_opportunity_id` | AWS Opportunity ID | Single-line text | Partner Central Opportunity ID (e.g., O1234567) |
| `aws_opportunity_arn` | AWS Opportunity ARN | Single-line text | Full ARN of the opportunity |
| `aws_opportunity_title` | AWS Opportunity Title | Single-line text | Canonical opportunity title in Partner Central |
| `aws_review_status` | AWS Review Status | Single-line text | Review status from AWS (Pending/Approved/Rejected) |
| `aws_sync_status` | AWS Sync Status | Single-line text | Sync status (synced/error/blocked) |
| `aws_invitation_id` | AWS Invitation ID | Single-line text | Engagement invitation ID (for AWS-originated deals) |
| `aws_industry` | AWS Industry Override | Single-line text | Override industry for Partner Central |
| `aws_delivery_models` | AWS Delivery Models | Single-line text | Comma-separated delivery models |
| `aws_primary_needs` | AWS Primary Needs | Single-line text | Comma-separated primary needs from AWS |
| `aws_use_case` | AWS Customer Use Case | Single-line text | Customer use case for Partner Central |
| `aws_expected_spend` | AWS Expected Monthly Spend (USD) | Number | Expected monthly AWS spend |
| `aws_psm_name` | AWS PSM Name | Single-line text | AWS Partner Solutions Manager name |
| `aws_psm_email` | AWS PSM Email | Email | AWS PSM email |
| `aws_psm_phone` | AWS PSM Phone | Phone number | AWS PSM phone |

All properties should be added to the **Deal Information** property group.

---

## Part 6: Verification

### Step 6.1: Test HubSpot → Partner Central Flow

1. Create a new deal in HubSpot:
   - **Deal Name**: `Test AWS Migration #AWS` (must contain `#AWS`)
   - **Amount**: `50000`
   - **Close Date**: Future date
   - **Deal Stage**: Any stage
   - **Description**: `Test opportunity for AWS Partner Central sync`

2. Wait 1-2 seconds for the webhook to fire

3. Verify in HubSpot:
   - Refresh the deal page
   - Check that `AWS Opportunity ID` property is populated (e.g., `O-1234567`)
   - Check that `AWS Sync Status` is `synced`

4. Verify in AWS Partner Central:
   - Log in to [Partner Central](https://partnercentral.awspartner.com/)
   - Navigate to **Opportunities** → **My Opportunities**
   - Find your newly created opportunity
   - Confirm the title, amount, and close date match your HubSpot deal

5. Check CloudWatch Logs for any errors:
   ```bash
   aws logs tail /aws/lambda/hubspot-to-partner-central-production --follow
   ```

### Step 6.2: Test Partner Central → HubSpot Flow

This flow is triggered when AWS sends you an engagement invitation.

**Note**: You cannot easily test this without a real invitation from AWS. To trigger manually:

1. AWS Partner Central will send engagement invitations asynchronously
2. The Lambda runs every 5 minutes (configurable) to check for new invitations
3. When an invitation is found, it's automatically accepted and a HubSpot deal is created

To monitor:
```bash
aws logs tail /aws/lambda/partner-central-to-hubspot-production --follow
```

Look for log entries like:
```
Found 1 pending invitation(s)
Accepting invitation: ei-1234567
Created HubSpot deal: 12345678 for invitation ei-1234567
```

### Step 6.3: Verify Custom Properties

Check that your test deal has the AWS custom properties visible:

1. Open the deal in HubSpot
2. Scroll down to the **Deal Information** section
3. Verify you see all AWS-related fields:
   - AWS Opportunity ID
   - AWS Opportunity ARN
   - AWS Review Status
   - AWS Sync Status
   - (and others if populated)

---

## Variable Reference

Here's a complete list of all variables needed during installation:

### HubSpot Variables

| Variable | Required | Where to Find | Example |
|----------|----------|---------------|---------|
| `HUBSPOT_ACCESS_TOKEN` | ✅ Yes | HubSpot → Settings → Integrations → Private Apps → Your App → Auth tab | `pat-na1-abc123...` |
| `HUBSPOT_WEBHOOK_SECRET` | ⚠️ Recommended | Generate yourself (see Step 1.3) | `random-secret-32-chars` |

### AWS Variables

| Variable | Required | Where to Find | Example |
|----------|----------|---------------|---------|
| `PARTNER_CENTRAL_ROLE_ARN` | ✅ Yes | Output from IAM role deployment (Step 2.2) | `arn:aws:iam::123456789012:role/HubSpotPartnerCentralServiceRole` |
| `PARTNER_CENTRAL_SOLUTION_ID` | ⚠️ Optional | AWS Partner Central → Solutions → My Solutions | `S-0000001` |
| `AWS_REGION` | ✅ Yes | AWS region for deployment | `us-east-1` |

### Deployment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `STACK_NAME` | ✅ Yes | `hubspot-partner-central-sync` | CloudFormation stack name |
| `ENVIRONMENT` | ✅ Yes | `production` | Deployment environment (production/staging/development) |
| `INVITATION_POLL_INTERVAL_MINUTES` | No | `5` | How often to check for Partner Central invitations (1-60 minutes) |

### Generated During Deployment

| Variable | When Generated | Example |
|----------|----------------|---------|
| `WEBHOOK_URL` | After SAM deploy | `https://abc123.execute-api.us-east-1.amazonaws.com/production/webhook/hubspot` |

---

## Troubleshooting

### Common Issues

#### Issue: "Unable to assume role"

**Error:**
```
An error occurred (AccessDenied) when calling the AssumeRole operation
```

**Solution:**
- Verify the IAM role was deployed successfully:
  ```bash
  aws iam get-role --role-name HubSpotPartnerCentralServiceRole
  ```
- Ensure the `PARTNER_CENTRAL_ROLE_ARN` parameter is correct
- Check that the role's trust policy allows your Lambda execution role to assume it

#### Issue: Webhook not receiving events

**Symptoms:**
- Creating deals with `#AWS` doesn't trigger sync
- No logs in CloudWatch

**Solution:**
- Verify webhook subscription is **enabled** in HubSpot
- Check the webhook URL matches the SAM output exactly
- Test the webhook endpoint manually:
  ```bash
  curl -X POST \
    https://your-api.execute-api.us-east-1.amazonaws.com/production/webhook/hubspot \
    -H "Content-Type: application/json" \
    -d '{"test": true}'
  ```
- Check API Gateway logs:
  ```bash
  aws logs tail /aws/apigateway/hubspot-partner-central-webhook-production --follow
  ```

#### Issue: "Property does not exist" error

**Error:**
```
Property 'aws_opportunity_id' does not exist
```

**Solution:**
- Create custom properties following [Part 5](#part-5-create-hubspot-custom-properties)
- Verify property names match exactly (case-sensitive)
- Check property visibility settings in HubSpot

#### Issue: Deals not syncing from Partner Central

**Symptoms:**
- AWS invitations not appearing as HubSpot deals

**Solution:**
- Check the Lambda is running on schedule:
  ```bash
  aws events list-rules --name-prefix "partner-central-invitation-poll"
  ```
- Verify the EventBridge rule is enabled
- Check CloudWatch logs for errors:
  ```bash
  aws logs tail /aws/lambda/partner-central-to-hubspot-production --follow
  ```
- Confirm you have pending invitations in Partner Central:
  ```bash
  aws partnercentral-selling list-engagement-invitations \
    --catalog "AWS" \
    --participant-type SENDER \
    --region us-east-1
  ```

#### Issue: "Invalid signature" webhook errors

**Error:**
```
Webhook signature verification failed
```

**Solution:**
- If you're using `HUBSPOT_WEBHOOK_SECRET`, ensure it's correctly set in the SAM deployment
- The secret must match between your Lambda environment variable and HubSpot webhook configuration
- To disable signature verification temporarily (not recommended for production):
  - Remove the `HUBSPOT_WEBHOOK_SECRET` parameter from SAM deploy
  - Redeploy: `sam deploy`

#### Issue: Lambda timeout errors

**Error:**
```
Task timed out after 60.00 seconds
```

**Solution:**
- The default timeout is 60 seconds, which should be sufficient
- If you're processing many deals/invitations:
  - Edit `template.yaml` → `Globals.Function.Timeout` → increase to 120 or 300
  - Redeploy: `sam build && sam deploy`

### Getting Help

If you continue to experience issues:

1. **Check CloudWatch Logs** - Most issues are logged with detailed error messages:
   ```bash
   # HubSpot webhook handler logs
   aws logs tail /aws/lambda/hubspot-to-partner-central-production --follow
   
   # Partner Central sync logs
   aws logs tail /aws/lambda/partner-central-to-hubspot-production --follow
   ```

2. **Enable Debug Logging**:
   - Edit the Lambda environment variable `LOG_LEVEL` from `INFO` to `DEBUG`
   - Via AWS Console: Lambda → Functions → [Function Name] → Configuration → Environment variables

3. **Verify IAM Permissions**:
   - Lambda execution role has `sts:AssumeRole` permission
   - Service role has Partner Central API permissions

4. **Check AWS Service Health**:
   - [AWS Service Health Dashboard](https://health.aws.amazon.com/health/status)
   - Partner Central API status

5. **Review Security Policies**:
   - Ensure no VPC or security group restrictions are blocking API calls
   - Verify Lambda has internet access (if in VPC, requires NAT Gateway)

### Logs to Check

| Log Group | What to Look For |
|-----------|------------------|
| `/aws/lambda/hubspot-to-partner-central-production` | Deal creation webhook processing, Partner Central API calls |
| `/aws/lambda/partner-central-to-hubspot-production` | Invitation polling, HubSpot deal creation |
| `/aws/apigateway/hubspot-partner-central-webhook-production` | Webhook endpoint access logs, HTTP errors |

---

## Next Steps

After successful installation:

1. **Test thoroughly** - Create several test deals to ensure proper sync
2. **Configure additional features** - See [README.md](./README.md) for advanced features:
   - Bidirectional property sync
   - Smart notifications
   - Resource sync
   - Solution management API
3. **Monitor regularly** - Set up CloudWatch alarms for Lambda errors
4. **Review security** - See [SECURITY.md](./SECURITY.md) for best practices
5. **Train your team** - Document the `#AWS` tag convention for your sales team
6. **Plan for updates** - Subscribe to GitHub releases for updates and bug fixes

---

## Uninstallation

To completely remove the integration:

1. **Delete the SAM stack**:
   ```bash
   aws cloudformation delete-stack --stack-name hubspot-partner-central-sync
   ```

2. **Delete the IAM role stack**:
   ```bash
   aws cloudformation delete-stack --stack-name hubspot-partner-central-iam
   ```

3. **Remove HubSpot webhook**:
   - Go to HubSpot → Settings → Integrations → Private Apps → Your App → Webhooks
   - Delete the webhook subscription

4. **Optional - Remove custom properties**:
   - Go to HubSpot → Settings → Properties → Deals
   - Delete AWS-related custom properties (this will remove data from existing deals)

5. **Optional - Remove the Private App**:
   - Go to HubSpot → Settings → Integrations → Private Apps
   - Delete the `AWS Partner Central Sync` app

---

## Additional Resources

- **Repository**: [github.com/thatmikereed/hubspot-aws-partner-central-sync](https://github.com/thatmikereed/hubspot-aws-partner-central-sync)
- **README**: [README.md](./README.md) - Architecture and features
- **API Guide**: [API-GUIDE.md](./API-GUIDE.md) - API documentation
- **Security**: [SECURITY.md](./SECURITY.md) - Security best practices
- **HubSpot API Docs**: [developers.hubspot.com/docs/api/overview](https://developers.hubspot.com/docs/api/overview)
- **AWS Partner Central API**: [docs.aws.amazon.com/partner-central/latest/selling-api](https://docs.aws.amazon.com/partner-central/latest/selling-api/Welcome.html)
- **AWS SAM Documentation**: [docs.aws.amazon.com/serverless-application-model](https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/)

---

**Installation Complete!** 🎉

Your HubSpot ↔ AWS Partner Central integration is now active. Any new HubSpot deal with `#AWS` in the title will automatically sync to Partner Central, and AWS engagement invitations will create deals in HubSpot.
