# Google Cloud Partners Integration - Implementation Summary

## Overview

This document summarizes the complete implementation of the Google Cloud CRM Partners API integration for HubSpot deal synchronization, added alongside the existing AWS Partner Central integration.

## Architecture

### Integration Flow

```
HubSpot Deal (#GCP tag)
        │
        ├─→ Webhook to /webhook/hubspot/gcp
        │
        ├─→ Create Lead in GCP Partners API
        │   └─→ Store company & contact info
        │
        ├─→ Create Opportunity (linked to Lead)
        │   └─→ Store deal amount, stage, close date, product family
        │
        └─→ Write GCP IDs back to HubSpot deal

GCP Partners API
        │
        ├─→ Scheduled poll (every 15 min)
        │
        ├─→ List opportunities (filter out HubSpot-originated)
        │
        ├─→ Fetch associated Lead for company info
        │
        └─→ Create/update HubSpot deal with #GCP tag
```

### Components

| Component | File Path | Purpose |
|-----------|-----------|---------|
| GCP Client | `src/common/gcp_client.py` | Service account authentication, API client factory |
| GCP Mappers | `src/common/gcp_mappers.py` | Bidirectional field mappings (HubSpot ↔ GCP) |
| HubSpot→GCP Handler | `src/hubspot_to_gcp_partners/handler.py` | Webhook receiver for #GCP deals |
| GCP→HubSpot Handler | `src/gcp_partners_to_hubspot/handler.py` | Scheduled sync from GCP to HubSpot |
| Tests | `tests/test_gcp_mappers.py` | 21 comprehensive unit tests |

## Key Differences from AWS Integration

| Aspect | AWS Partner Central | Google Cloud Partners |
|--------|-------------------|---------------------|
| **Tag** | #AWS | #GCP |
| **Authentication** | IAM role assumption (STS) | Service account key (OAuth2) |
| **API Structure** | Single `CreateOpportunity` call | Two-step: Create Lead → Create Opportunity |
| **Webhook Path** | `/webhook/hubspot` | `/webhook/hubspot/gcp` |
| **Poll Interval** | 5 minutes | 15 minutes |
| **Invitation Model** | Accept pending invitations | Query opportunities directly |
| **Product Types** | AWS services | Google Cloud, Workspace, Chrome, Maps, Apigee |

## Field Mappings

### Lead Creation (Step 1)

| HubSpot Source | GCP Lead Field | Notes |
|----------------|----------------|-------|
| `company.name` | `companyName` | Primary company identifier |
| `company.website` | `companyWebsite` | Auto-prepends https:// if missing |
| `contact.firstname` | `contact.givenName` | From first associated contact |
| `contact.lastname` | `contact.familyName` | From first associated contact |
| `contact.email` | `contact.email` | Required for contact association |
| `contact.phone` | `contact.phone` | E.164 format (+1XXXXXXXXXX) |
| `description` | `notes` | Max 2000 characters |
| `deal.id` | `externalSystemId` | Format: `hubspot-deal-{id}` |

### Opportunity Creation (Step 2)

| HubSpot Source | GCP Opportunity Field | Notes |
|----------------|---------------------|-------|
| Lead name | `lead` | Resource name from step 1 |
| `dealstage` | `salesStage` | QUALIFYING, QUALIFIED, PROPOSAL, NEGOTIATING, CLOSED_WON, CLOSED_LOST |
| `dealstage` | `qualificationState` | UNQUALIFIED, QUALIFIED, DISQUALIFIED |
| `amount` | `dealSize` | Float value (e.g., 150000.0) |
| `closedate` | `closeDate` | Object: {year, month, day} |
| `gcp_product_family` | `productFamily` | GOOGLE_CLOUD_PLATFORM (default), GOOGLE_WORKSPACE, CHROME_ENTERPRISE, GOOGLE_MAPS_PLATFORM, APIGEE |
| `gcp_term_months` | `termMonths` | Subscription term length |
| `description` | `notes` | Max 2000 characters |
| `hs_next_step` | `nextSteps` | Max 500 characters |
| `deal.id` | `externalSystemId` | Format: `hubspot-deal-{id}` |

### Stage Mappings

| HubSpot Stage | GCP Sales Stage |
|---------------|----------------|
| appointmentscheduled | QUALIFYING |
| qualifiedtobuy | QUALIFIED |
| presentationscheduled | QUALIFIED |
| decisionmakerboughtin | PROPOSAL |
| contractsent | NEGOTIATING |
| closedwon | CLOSED_WON |
| closedlost | CLOSED_LOST |

## Configuration

### Required Environment Variables

```bash
# Core HubSpot
HUBSPOT_ACCESS_TOKEN=pat-na1-...
HUBSPOT_WEBHOOK_SECRET=secret-key-here

# GCP Integration
GCP_PARTNER_ID=12345
GOOGLE_APPLICATION_CREDENTIALS=/tmp/gcp-service-account.json

# Infrastructure
ENVIRONMENT=production
LOG_LEVEL=INFO
```

### CloudFormation Parameters

```yaml
GcpPartnerId:
  Type: String
  Description: Google Cloud Partner ID from Partners Portal

GcpServiceAccountKey:
  Type: String
  NoEcho: true
  Description: Base64-encoded service account JSON key

GcpPollIntervalMinutes:
  Type: Number
  Default: 15
  MinValue: 5
  MaxValue: 60
```

### SSM Parameters

```
/hubspot-pc-sync/{environment}/gcp-service-account-key
  - Type: SecureString
  - Value: Base64-encoded JSON key
  - Usage: Decoded and written to /tmp at runtime
```

## Security Implementation

### Secrets Management
- ✅ Service account key stored in SSM Parameter Store as SecureString
- ✅ Base64 encoding for safe CloudFormation transport
- ✅ No hardcoded credentials in source code
- ✅ Temporary credentials written to /tmp only during Lambda execution
- ✅ Lambda has minimal IAM permissions (SSM GetParameter only)

### API Authentication
- ✅ OAuth2 service account flow via google-auth library
- ✅ Scoped to `https://www.googleapis.com/auth/cloud-platform`
- ✅ Short-lived tokens (auto-refreshed by client library)

### Data Protection
- ✅ HubSpot webhook signature verification (HMAC-SHA256)
- ✅ Confidential deals can be marked via `gcp_is_confidential` property
- ✅ CloudWatch logs retain for 30 days only

### CodeQL Results
- **0 vulnerabilities found** in GCP integration code
- All security best practices followed
- No sensitive data exposure risks

## Testing

### Test Coverage

```
tests/test_gcp_mappers.py (21 tests)
├── Lead Creation
│   ├── test_hubspot_deal_to_gcp_lead_minimal
│   ├── test_hubspot_deal_to_gcp_lead_full
│   └── test_hubspot_deal_to_gcp_lead_website_sanitization
├── Opportunity Creation
│   ├── test_hubspot_deal_to_gcp_opportunity_minimal
│   ├── test_hubspot_deal_to_gcp_opportunity_full
│   ├── test_hubspot_deal_to_gcp_opportunity_stage_mapping
│   └── test_hubspot_deal_to_gcp_opportunity_product_family_mapping
├── Reverse Mapping (GCP → HubSpot)
│   ├── test_gcp_opportunity_to_hubspot_deal
│   ├── test_gcp_opportunity_to_hubspot_deal_stage_mapping
│   └── test_gcp_opportunity_to_hubspot_deal_adds_gcp_tag
├── Update Payloads
│   ├── test_hubspot_deal_to_gcp_opportunity_update_stage
│   ├── test_hubspot_deal_to_gcp_opportunity_update_amount
│   └── test_hubspot_deal_to_gcp_opportunity_update_description
└── Helper Functions
    ├── test_sanitize_website
    ├── test_sanitize_phone
    ├── test_parse_close_date
    ├── test_gcp_date_to_hubspot_iso
    ├── test_map_product_family
    ├── test_map_qualification_state
    ├── test_stage_mappings_are_bijective
    └── test_product_family_enum_values
```

### Test Results
- ✅ **21/21 GCP tests passing**
- ✅ **49/49 AWS tests passing** (no regression)
- ✅ **Total: 70/70 tests passing**

### Code Quality Metrics
- ✅ Black formatter: 4 files reformatted
- ✅ Ruff linter: All checks passed
- ✅ Mypy type checking: All type hints valid
- ✅ CodeQL security: 0 vulnerabilities

## Deployment

### Prerequisites
1. Google Cloud Partner account with Partner ID
2. GCP service account with Cloud CRM Partners API permissions
3. Service account JSON key file
4. HubSpot Private App with appropriate scopes

### Steps

1. **Encode Service Account Key**
   ```bash
   GCP_KEY_BASE64=$(base64 -w 0 path/to/service-account-key.json)
   ```

2. **Build Lambda Functions**
   ```bash
   sam build
   ```

3. **Deploy with Parameters**
   ```bash
   sam deploy --guided \
     --parameter-overrides \
       "GcpPartnerId=12345" \
       "GcpServiceAccountKey=$GCP_KEY_BASE64" \
       "GcpPollIntervalMinutes=15"
   ```

4. **Configure HubSpot Webhook**
   - Event: `deal.creation`
   - Target URL: Output `GcpWebhookUrl` from deployment
   - Format: `https://xyz.execute-api.us-east-1.amazonaws.com/production/webhook/hubspot/gcp`

5. **Create Custom Properties** (one-time)
   ```python
   from src.common.hubspot_client import HubSpotClient
   client = HubSpotClient()
   client.create_custom_properties()
   ```

## Monitoring

### CloudWatch Log Groups
```
/aws/lambda/hubspot-to-gcp-partners-{environment}
/aws/lambda/gcp-partners-to-hubspot-{environment}
```

### Key Metrics to Monitor
- Webhook invocation count
- Lead/opportunity creation success rate
- Scheduled sync execution frequency
- API error rates (GCP Partners API)
- Deal sync latency (webhook to GCP write)

### Common Error Patterns

| Error | Cause | Resolution |
|-------|-------|-----------|
| "GCP_PARTNER_ID not set" | Missing environment variable | Set GcpPartnerId parameter |
| "Cannot find credentials" | SSM parameter not found | Verify GcpServiceAccountKey is set |
| "Invalid service account" | Wrong key or permissions | Check service account has Cloud CRM Partners API access |
| "Lead already exists" | Duplicate externalSystemId | Expected - idempotency working |
| "Opportunity not found" | Circular sync prevention | Expected - skipping HubSpot-originated opportunities |

## Limitations and Known Issues

### Current Limitations
1. **No Invitation Flow**: GCP doesn't have an invitation model like AWS - opportunities are directly queried
2. **Single Contact**: Only the first HubSpot contact is mapped to the GCP lead
3. **Manual Product Family**: Product family must be set via custom HubSpot property or defaults to GOOGLE_CLOUD_PLATFORM
4. **Polling-Only Inbound**: No real-time webhooks from GCP to HubSpot (uses scheduled polling)

### Future Enhancements
- [ ] Support multiple contacts per lead
- [ ] Intelligent product family detection based on deal description
- [ ] Real-time sync via GCP Pub/Sub (if API supports it)
- [ ] Bulk import of existing GCP opportunities
- [ ] Advanced conflict resolution for simultaneous updates
- [ ] Custom field mapping configuration

## Maintenance

### Regular Tasks
- Monitor CloudWatch logs for errors
- Review SSM parameter expiration (service account keys should be rotated)
- Update GCP scopes if API adds new features
- Keep google-api-python-client library up to date

### Troubleshooting Commands

```bash
# View recent logs for HubSpot→GCP function
aws logs tail /aws/lambda/hubspot-to-gcp-partners-production --follow

# View recent logs for GCP→HubSpot function
aws logs tail /aws/lambda/gcp-partners-to-hubspot-production --follow

# Manually invoke scheduled sync
aws lambda invoke \
  --function-name gcp-partners-to-hubspot-production \
  --payload '{}' \
  response.json

# Check service account key in SSM
aws ssm get-parameter \
  --name /hubspot-pc-sync/production/gcp-service-account-key \
  --with-decryption
```

## References

- [Google Cloud CRM Partners API Documentation](https://developers.google.com/cloud-crm-partners)
- [Cloud CRM Partners API Reference](https://developers.google.com/cloud-crm-partners/reference/rest)
- [HubSpot CRM API Documentation](https://developers.hubspot.com/docs/api/crm/deals)
- [AWS SAM Documentation](https://docs.aws.amazon.com/serverless-application-model/)
- [Google Auth Library for Python](https://google-auth.readthedocs.io/)

## Contributors

- Implementation Date: February 2026
- Integration Pattern: Based on AWS Partner Central integration
- Technology Stack: Python 3.12, AWS Lambda, Google Cloud Partners API v1
