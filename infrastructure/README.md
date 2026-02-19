# Async Event-Driven Architecture - Deployment Guide

This guide provides step-by-step instructions for deploying the async event-driven architecture alongside the existing synchronous handlers.

## Overview

The async architecture consists of:
- **Webhook Receipt Handler**: Fast receiver that enqueues events to SQS (<100ms)
- **SQS FIFO Queues**: Main queue and Dead Letter Queue (DLQ) for failed messages
- **Event Processor**: Consumes events from SQS and routes to appropriate handlers
- **CloudWatch Alarms**: Monitors queue health and Lambda errors
- **Dashboard**: Comprehensive monitoring dashboard

## Prerequisites

1. **AWS CLI** installed and configured with appropriate credentials
2. **AWS SAM CLI** installed (for Lambda deployment)
3. **Python 3.12** for local development/testing
4. **IAM Role**: The `HubSpotPartnerCentralServiceRole` must be deployed first
5. **HubSpot Access Token**: Private app access token with appropriate scopes

## Deployment Steps

### Step 1: Deploy IAM Role (if not already deployed)

The Partner Central service role is required for Lambda functions to access AWS Partner Central API.

```bash
# Deploy the IAM role
aws cloudformation deploy \
  --template-file infra/iam-role.yaml \
  --stack-name hubspot-partner-central-iam \
  --capabilities CAPABILITY_NAMED_IAM

# Get the role ARN (save this for later)
aws cloudformation describe-stacks \
  --stack-name hubspot-partner-central-iam \
  --query 'Stacks[0].Outputs[?OutputKey==`RoleArn`].OutputValue' \
  --output text
```

### Step 2: Deploy Async Architecture Stack

Deploy the core async infrastructure (queues, Lambda functions, alarms).

```bash
# Navigate to the infrastructure directory
cd infrastructure

# Deploy the async architecture stack
sam deploy \
  --template-file async-architecture.yaml \
  --stack-name hubspot-async-sync-production \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides \
    Environment=production \
    HubSpotAccessToken=$HUBSPOT_ACCESS_TOKEN \
    HubSpotWebhookSecret=$HUBSPOT_WEBHOOK_SECRET \
    PartnerCentralRoleArn=$PARTNER_CENTRAL_ROLE_ARN \
    PartnerCentralSolutionId=$PARTNER_CENTRAL_SOLUTION_ID \
    AlarmEmail=your-email@example.com

# Get the webhook URL (save this for HubSpot configuration)
aws cloudformation describe-stacks \
  --stack-name hubspot-async-sync-production \
  --query 'Stacks[0].Outputs[?OutputKey==`AsyncWebhookUrl`].OutputValue' \
  --output text
```

**Parameters:**
- `Environment`: deployment environment (production, staging, development)
- `HubSpotAccessToken`: HubSpot Private App access token
- `HubSpotWebhookSecret`: (Optional) webhook signature verification secret
- `PartnerCentralRoleArn`: ARN of the HubSpotPartnerCentralServiceRole
- `PartnerCentralSolutionId`: (Optional) default solution ID for opportunities
- `AlarmEmail`: (Optional) email address for alarm notifications

### Step 3: Deploy CloudWatch Dashboard (Optional)

Deploy a comprehensive monitoring dashboard for the async architecture.

```bash
# Get resource names from the async stack
QUEUE_NAME=$(aws cloudformation describe-stacks \
  --stack-name hubspot-async-sync-production \
  --query 'Stacks[0].Outputs[?OutputKey==`SyncEventsQueueUrl`].OutputValue' \
  --output text | awk -F'/' '{print $NF}')

DLQ_NAME=$(aws cloudformation describe-stacks \
  --stack-name hubspot-async-sync-production \
  --query 'Stacks[0].Outputs[?OutputKey==`SyncEventsDLQUrl`].OutputValue' \
  --output text | awk -F'/' '{print $NF}')

# Deploy the dashboard
aws cloudformation deploy \
  --template-file monitoring-dashboard.yaml \
  --stack-name hubspot-async-dashboard-production \
  --parameter-overrides \
    Environment=production \
    QueueName=$QUEUE_NAME \
    DLQName=$DLQ_NAME \
    ReceiptFunctionName=webhook-receipt-handler-production \
    ProcessorFunctionName=sync-event-processor-production
```

### Step 4: Configure HubSpot Webhook

Configure HubSpot to send webhook events to the async endpoint.

1. Go to HubSpot Developer Portal: https://app.hubspot.com/
2. Navigate to your app's webhook settings
3. Add a new webhook subscription:
   - **URL**: The `AsyncWebhookUrl` from Step 2
   - **Events**: Select relevant events:
     - `deal.creation`
     - `deal.propertyChange`
     - `company.propertyChange`
     - `contact.propertyChange`
     - `engagement.creation` (for notes)

4. Save the webhook configuration

**Note**: You can run both sync and async webhooks in parallel during migration.

### Step 5: Test with Sample Webhook

Send a test webhook event to verify the deployment.

```bash
# Get the webhook URL
WEBHOOK_URL=$(aws cloudformation describe-stacks \
  --stack-name hubspot-async-sync-production \
  --query 'Stacks[0].Outputs[?OutputKey==`AsyncWebhookUrl`].OutputValue' \
  --output text)

# Send a test event
curl -X POST $WEBHOOK_URL \
  -H "Content-Type: application/json" \
  -d '[{
    "subscriptionType": "deal.creation",
    "objectId": "12345",
    "propertyName": "dealname",
    "propertyValue": "Test Deal #AWS"
  }]'

# Expected response: {"message": "Webhook received", "enqueued": 1, ...}
```

### Step 6: Monitor the Dashboard

Access the CloudWatch dashboard to monitor the async architecture.

```bash
# Get dashboard URL
aws cloudformation describe-stacks \
  --stack-name hubspot-async-dashboard-production \
  --query 'Stacks[0].Outputs[?OutputKey==`DashboardUrl`].OutputValue' \
  --output text
```

The dashboard shows:
- Queue depth and age
- Lambda invocations and errors
- Processing latency
- Recent error logs
- Error rates

## Verification Checklist

After deployment, verify the following:

- [ ] Stack deployed successfully without errors
- [ ] SQS queues created (main queue and DLQ)
- [ ] Lambda functions deployed and active
- [ ] CloudWatch alarms created and enabled
- [ ] SNS topic created (if alarm email provided)
- [ ] API Gateway endpoint accessible
- [ ] HubSpot webhook configured
- [ ] Test webhook succeeds
- [ ] Message appears in SQS queue
- [ ] Event processor processes the message
- [ ] No errors in CloudWatch Logs
- [ ] Dashboard displays metrics

## Monitoring and Alerts

### CloudWatch Alarms

The deployment creates the following alarms:

1. **DLQ Messages**: Alerts when messages appear in DLQ (failed after 3 retries)
2. **Queue Age**: Alerts when oldest message is > 5 minutes old
3. **Receipt Handler Errors**: Alerts when error rate > 5%
4. **Processor Errors**: Alerts when error rate > 5%

All alarms send notifications to the SNS topic (if email provided).

### Key Metrics to Monitor

- **Queue Depth**: Should stay near zero during normal operation
- **Queue Age**: Should be < 1 minute typically
- **DLQ Messages**: Should be zero (any messages indicate failures)
- **Lambda Duration**: Receipt handler should be < 100ms
- **Error Rate**: Should be < 1% for both functions

## Troubleshooting

### No messages in queue after webhook

1. Check API Gateway logs for incoming requests
2. Verify HubSpot webhook configuration
3. Check receipt handler CloudWatch logs for errors
4. Verify SQS permissions on Lambda role

### Messages stuck in queue (not processing)

1. Check event processor CloudWatch logs for errors
2. Verify Partner Central role ARN is correct
3. Check SQS visibility timeout (should match Lambda timeout)
4. Look for throttling errors in CloudWatch metrics

### Messages in DLQ

1. Check processor logs for error details
2. Common causes:
   - Invalid event format
   - Partner Central API errors
   - Missing required fields
   - Timeout (increase Lambda timeout if needed)

### High error rate

1. Review CloudWatch Logs Insights query:
   ```
   fields @timestamp, @message
   | filter @message like /ERROR|Exception/
   | sort @timestamp desc
   | limit 20
   ```

2. Check for:
   - API rate limiting
   - Network issues
   - Invalid credentials
   - Missing environment variables

## Rollback Procedure

If issues arise, you can rollback the deployment:

```bash
# Delete the dashboard stack (optional)
aws cloudformation delete-stack \
  --stack-name hubspot-async-dashboard-production

# Delete the async architecture stack
aws cloudformation delete-stack \
  --stack-name hubspot-async-sync-production

# Wait for deletion to complete
aws cloudformation wait stack-delete-complete \
  --stack-name hubspot-async-sync-production

# Reconfigure HubSpot webhook to point to the old endpoint
```

**Note**: Deleting the stack will delete the SQS queues and any messages in them. If you need to preserve messages, manually copy them from the queue first.

## Cost Estimation

Estimated monthly costs for the async architecture (us-east-1 region):

- **SQS**: ~$0.40 per 1M requests
- **Lambda (Receipt)**: ~$0.20 per 1M requests (128MB, 50ms avg)
- **Lambda (Processor)**: ~$1.00 per 1M requests (512MB, 2s avg)
- **CloudWatch Logs**: ~$0.50/GB ingested
- **CloudWatch Alarms**: $0.10 per alarm per month

**Total**: ~$2-5 per month for typical workload (100K events/month)

## Support and Resources

- **Architecture Diagram**: See `docs/MIGRATION.md`
- **Event Schema**: See `src/common/events.py`
- **AWS Documentation**:
  - [SQS FIFO Queues](https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/FIFO-queues.html)
  - [Lambda with SQS](https://docs.aws.amazon.com/lambda/latest/dg/with-sqs.html)
  - [CloudWatch Alarms](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/AlarmThatSendsEmail.html)

## Next Steps

After successful deployment:

1. Review the [Migration Guide](../docs/MIGRATION.md) for phased rollout strategy
2. Configure traffic splitting between sync and async endpoints
3. Monitor metrics and adjust alarms as needed
4. Gradually increase traffic to async endpoint
5. Decommission old sync handlers after full migration
