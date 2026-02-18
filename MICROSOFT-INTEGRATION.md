# Microsoft Partner Center Integration Summary

This document provides a comprehensive overview of the Microsoft Partner Center integration added to this repository.

## Overview

The Microsoft Partner Center integration enables bidirectional synchronization between HubSpot CRM and Microsoft Partner Center's Referrals API. This mirrors the existing AWS Partner Central integration but is tailored to Microsoft's referral system.

## Architecture

### Components

1. **HubSpotToMicrosoftFunction** (Lambda)
   - Triggered by HubSpot webhooks for deal creation and updates
   - Filters deals containing `#Microsoft` tag
   - Creates/updates referrals in Microsoft Partner Center
   - Writes referral ID back to HubSpot

2. **MicrosoftToHubSpotFunction** (Lambda)
   - Scheduled execution (default: every 15 minutes)
   - Polls Microsoft Partner Center for New and Active referrals
   - Creates new HubSpot deals or updates existing ones
   - Maintains synchronization of referral status changes

3. **MicrosoftPartnerCenterClient** (Common Module)
   - HTTP client for Microsoft Partner Center Referrals API
   - Handles authentication with Azure AD tokens
   - Supports CRUD operations on referrals
   - Implements eTag-based optimistic concurrency

4. **Microsoft Mappers** (Common Module)
   - Bidirectional field mapping between HubSpot and Microsoft
   - Status/substatus to deal stage conversion
   - Qualification level mapping
   - Customer profile and address normalization

## Data Flow

### HubSpot → Microsoft Partner Center

```
1. Sales rep creates deal with "#Microsoft" in title
2. HubSpot fires webhook to API Gateway
3. Lambda handler receives webhook
4. Verify webhook signature (if configured)
5. Extract deal, company, and contact information
6. Map HubSpot fields to Microsoft referral format
7. Call Microsoft Partner Center API CreateReferral
8. Write referral ID and status back to HubSpot deal
9. Return success response
```

### Microsoft Partner Center → HubSpot

```
1. EventBridge triggers Lambda on schedule
2. Call Microsoft API to list New and Active referrals
3. For each referral:
   a. Search HubSpot for existing deal with referral ID
   b. If not found: create new deal with #Microsoft tag
   c. If found and status changed: update deal stage
   d. Write sync status to HubSpot
4. Return summary of created/updated deals
```

## Field Mappings

### HubSpot → Microsoft

| HubSpot Field | Microsoft Partner Center Field | Notes |
|---------------|-------------------------------|-------|
| dealname | name | Core referral title |
| description | details.notes | Limited to 500 chars |
| dealstage | status + substatus | Mapped via enum table |
| amount | details.dealValue | Converted to float |
| closedate | details.closeDate | ISO 8601 date format |
| deal.id | externalReferenceId | For correlation |
| company.name | customerProfile.name | Customer org name |
| company.address | customerProfile.address | Full address object |
| contacts[0] | customerProfile.team[0] | Primary contact |

### Microsoft → HubSpot

| Microsoft Partner Center Field | HubSpot Field | Notes |
|--------------------------------|---------------|-------|
| name | dealname | Appends #Microsoft tag |
| details.notes | description | Full text |
| status + substatus | dealstage | Reverse mapping |
| details.dealValue | amount | As string |
| details.closeDate | closedate | Unix timestamp (ms) |
| id | microsoft_referral_id | Custom property |
| status | microsoft_status | Custom property |
| substatus | microsoft_substatus | Custom property |

## Status Mappings

### HubSpot Stage → Microsoft Status/Substatus

| HubSpot dealstage | Microsoft status | Microsoft substatus |
|-------------------|------------------|---------------------|
| appointmentscheduled | New | Pending |
| qualifiedtobuy | Active | Accepted |
| presentationscheduled | Active | Engaged |
| decisionmakerboughtin | Active | Engaged |
| contractsent | Active | Engaged |
| closedwon | Closed | Won |
| closedlost | Closed | Lost |

### Microsoft Status → HubSpot Stage

| Microsoft status | Microsoft substatus | HubSpot dealstage |
|------------------|---------------------|-------------------|
| New | Pending | appointmentscheduled |
| New | Received | appointmentscheduled |
| Active | Accepted | qualifiedtobuy |
| Active | Engaged | presentationscheduled |
| Closed | Won | closedwon |
| Closed | Lost | closedlost |
| Closed | Declined | closedlost |
| Closed | Expired | closedlost |

## HubSpot Custom Properties

The integration uses the following custom properties on HubSpot deals:

1. **microsoft_referral_id** (text)
   - The unique ID from Microsoft Partner Center
   - Used for idempotency and duplicate detection

2. **microsoft_sync_status** (enumeration)
   - Values: not_synced, synced, error
   - Indicates current sync state

3. **microsoft_status** (text)
   - Current Microsoft status (New, Active, Closed)
   - Updated on every sync

4. **microsoft_substatus** (text)
   - Current Microsoft substatus (Pending, Accepted, etc.)
   - Provides detailed status information

5. **customer_name** (text)
   - Customer organization name from Microsoft
   - Useful when company record not linked

## Authentication

### Microsoft Partner Center API

The integration uses **Azure AD OAuth 2.0** with delegated permissions:

1. **Setup Requirements:**
   - Azure AD App Registration
   - Partner Center API permissions (delegated)
   - Referral Admin or Referral User role
   - Valid access token with appropriate scope

2. **Token Management:**
   - Tokens stored in SSM Parameter Store (SecureString)
   - Passed as environment variable to Lambda
   - Should be rotated regularly
   - No refresh token handling (manual rotation required)

3. **API Security:**
   - HTTPS/TLS 1.2+ for all requests
   - Bearer token authentication
   - eTag concurrency control for updates

## Error Handling

### Idempotency

- **HubSpot → Microsoft**: Checks for existing `microsoft_referral_id` on deal
- **Microsoft → HubSpot**: Searches for deal with matching `microsoft_referral_id`
- Prevents duplicate referral creation
- Updates are safe to retry

### Update Conflicts

- **Closed Referrals**: Updates blocked with warning added as HubSpot note
- **eTag Mismatches**: Fetch fresh referral and retry update
- **API Errors**: Logged to CloudWatch, sync status set to "error"

### Rate Limiting

- Microsoft API has rate limits (not publicly documented)
- Scheduled sync runs every 15 minutes by default
- Batch size limited to 100 referrals per sync
- Implements retry with exponential backoff

## Testing

### Unit Tests

**test_microsoft_client.py** (13 tests)
- Client initialization with/without token
- CRUD operations (create, get, update, list)
- Error handling (API errors, not found, eTag conflicts)
- Query parameter construction
- Session management

**test_microsoft_mappers.py** (11 tests)
- Deal to referral conversion (minimal and full)
- Referral to deal conversion
- Status/substatus mappings
- Update payload generation
- Custom property definitions
- Closed referral blocking

### Test Coverage

- All core functionality covered
- Mock HTTP responses using `responses` library
- Parametric tests for mapping tables
- Edge cases (missing fields, invalid data)

### Running Tests

```bash
# Run all Microsoft tests
pytest tests/test_microsoft_*.py -v

# Run with coverage
pytest tests/test_microsoft_*.py --cov=src/common/microsoft_client --cov=src/common/microsoft_mappers
```

## Deployment

### Prerequisites

1. Azure AD App Registration configured
2. Microsoft Partner Center API access
3. Valid Azure AD access token
4. HubSpot Private App with deal permissions

### SAM Deployment

```bash
# Build
sam build

# Deploy with Microsoft integration
sam deploy \
  --parameter-overrides \
    "MicrosoftAccessToken=your-token-here" \
    "MicrosoftPollIntervalMinutes=15"
```

### Environment Variables

The Lambda functions receive these environment variables:

- `MICROSOFT_ACCESS_TOKEN`: Azure AD bearer token
- `HUBSPOT_ACCESS_TOKEN`: HubSpot Private App token
- `HUBSPOT_WEBHOOK_SECRET`: Webhook signature verification
- `ENVIRONMENT`: Deployment environment (production, staging, development)
- `LOG_LEVEL`: Logging verbosity (INFO, DEBUG)

### Post-Deployment

1. Copy `MicrosoftWebhookUrl` from SAM outputs
2. Register webhook in HubSpot for deal.creation events
3. Create Microsoft custom properties in HubSpot
4. Test with a deal containing "#Microsoft" tag

## Monitoring

### CloudWatch Logs

- **HubSpotToMicrosoftFunction**: `/aws/lambda/hubspot-to-microsoft-{Environment}`
- **MicrosoftToHubSpotFunction**: `/aws/lambda/microsoft-to-hubspot-{Environment}`
- Retention: 30 days
- Log level configurable via `LOG_LEVEL` environment variable

### Key Metrics to Monitor

1. **Invocation Count**: Number of webhook/scheduled invocations
2. **Error Rate**: Failed invocations or API errors
3. **Duration**: Lambda execution time
4. **Sync Success Rate**: Created vs. failed deals/referrals
5. **API Latency**: Microsoft Partner Center API response times

### Alerts

Recommended CloudWatch Alarms:

- Lambda error rate > 5%
- Lambda duration > 45 seconds (approaching timeout)
- No successful invocations in 30 minutes (for scheduled function)

## Limitations

1. **Token Management**: Manual token rotation required (no automatic refresh)
2. **Batch Size**: Limited to 100 referrals per sync run
3. **Webhook Verification**: Uses same HubSpot secret as AWS integration
4. **No Co-Sell**: Integration defaults to "Independent" referral type
5. **Contact Mapping**: Only maps first 5 contacts to referral team
6. **Read-Only Sync**: Cannot update referrals originated in Microsoft (status changes only)

## Future Enhancements

1. **Automatic Token Refresh**: Implement OAuth flow with refresh tokens
2. **Co-Sell Support**: Add solution area and partner role mappings
3. **Webhook Subscriptions**: Subscribe to Microsoft Partner Center webhooks
4. **Enrichment**: Add solution matching like AWS integration
5. **Conflict Resolution**: Advanced merge strategies for concurrent updates
6. **Bulk Operations**: Support for bulk import/export
7. **Custom Field Mapping**: Configurable field mappings via parameter store

## Comparison: AWS vs. Microsoft Integration

| Feature | AWS Partner Central | Microsoft Partner Center |
|---------|-------------------|-------------------------|
| **Trigger Tag** | #AWS | #Microsoft |
| **Authentication** | STS AssumeRole | Azure AD OAuth 2.0 |
| **API Style** | AWS SDK (boto3) | REST API (requests) |
| **Invitation Model** | Accept pending invitations | Poll active referrals |
| **Solution Association** | Yes, with auto-matching | No (future enhancement) |
| **Update Concurrency** | Optimistic (via ReviewStatus) | Optimistic (via eTag) |
| **Immutable Fields** | Project.Title after submission | None enforced |
| **Sync Frequency** | 5 minutes (invitations) | 15 minutes (referrals) |
| **Custom Properties** | 12 properties | 5 properties |
| **Test Coverage** | Extensive (moto + responses) | Comprehensive (responses) |

## Support and Troubleshooting

### Common Issues

1. **"Microsoft access token is required"**
   - Solution: Ensure `MicrosoftAccessToken` parameter is set during deployment

2. **"401 Unauthorized" from Microsoft API**
   - Solution: Token expired or invalid. Regenerate and redeploy.

3. **Deals not syncing to Microsoft**
   - Check: Does deal name contain "#Microsoft" (case-insensitive)?
   - Check: Does deal have existing `microsoft_referral_id`?
   - Review Lambda logs for errors

4. **Microsoft referrals not appearing in HubSpot**
   - Check: Lambda invocation successful?
   - Check: Referrals in "New" or "Active" status?
   - Check: Search HubSpot for deals with `microsoft_referral_id`

### Debug Mode

Enable debug logging:

```bash
sam deploy --parameter-overrides "LogLevel=DEBUG"
```

This provides detailed request/response logging for troubleshooting.

## Security Considerations

1. **Token Storage**: Always use SSM Parameter Store SecureString
2. **Least Privilege**: Grant only Referral User role if Referral Admin not required
3. **Network Security**: Lambda functions in default VPC (internet egress required)
4. **Webhook Verification**: Always configure `HUBSPOT_WEBHOOK_SECRET`
5. **Input Validation**: All external inputs sanitized before use
6. **Audit Logging**: All API calls logged to CloudWatch

## Contributing

When extending the Microsoft Partner Center integration:

1. Follow existing patterns from AWS integration
2. Add comprehensive unit tests (target: >90% coverage)
3. Update field mappings in `microsoft_mappers.py`
4. Document new custom properties
5. Update this summary document

## References

- [Microsoft Partner Center Referrals API Documentation](https://learn.microsoft.com/en-us/partner-center/developer/referrals-api-introduction)
- [HubSpot CRM API Documentation](https://developers.hubspot.com/docs/api/crm/deals)
- [AWS SAM Documentation](https://docs.aws.amazon.com/serverless-application-model/)
- [Repository README](./README.md)
