# Security Best Practices and Considerations

This document outlines the security measures implemented in this integration and best practices for deployment and operation.

## 🔒 Security Features Implemented

### 1. Confused Deputy Attack Prevention

**Issue**: Without proper protection, a malicious actor could trick your Lambda functions into assuming the `HubSpotPartnerCentralServiceRole` on their behalf.

**Mitigation**: 
- The IAM role trust policy requires an `ExternalId` condition: `"HubSpotPartnerCentralIntegration"`
- All `AssumeRole` calls in the code include this ExternalId
- This prevents unauthorized cross-account access even if an attacker knows the role ARN

**Reference**: `src/common/aws_client.py` line 18, `infra/iam-role.yaml` line 43

### 2. Webhook Signature Verification

**Issue**: Without signature verification, attackers could send forged webhook requests to create or modify opportunities.

**Mitigation**:
- All incoming HubSpot webhooks are verified using HMAC-SHA256 signatures
- Signature verification happens **before** parsing the request body
- Requests with missing or invalid signatures are rejected with HTTP 400
- The `HUBSPOT_WEBHOOK_SECRET` environment variable must be configured in production

**Reference**: `src/hubspot_to_partner_central/handler.py` lines 293-322

**Configuration**:
```yaml
# In template.yaml
Parameters:
  HubSpotWebhookSecret:
    Type: String
    NoEcho: true
    Description: HubSpot webhook signing secret (REQUIRED for production)
```

### 3. Sensitive Data Redaction in Logs

**Issue**: CloudWatch logs could expose sensitive credentials if the entire event is logged.

**Mitigation**:
- All HTTP headers containing credentials are redacted before logging
- Redacted headers: `Authorization`, `X-HubSpot-Signature`, `X-HubSpot-Signature-V3`, `Cookie`, `X-Api-Key`
- Large request bodies are truncated to prevent log flooding
- The redaction function creates a safe copy without modifying the original event

**Reference**: `src/hubspot_to_partner_central/handler.py` lines 49-76

### 4. Input Validation and Sanitization

**Issue**: Unvalidated inputs could lead to injection attacks, DoS, or data corruption.

**Mitigation**:
- All external IDs (HubSpot, Partner Central) are validated against expected patterns
- String fields have maximum length limits to prevent DoS
- Email addresses and URLs are validated with regex patterns
- Monetary amounts are validated and bounded
- Control characters are stripped from text fields
- All validation is centralized in `src/common/validators.py`

**Reference**: `src/common/validators.py`, `src/hubspot_to_partner_central/handler.py` line 122

### 5. Secure Credential Management

**Issue**: Hardcoded or exposed credentials in code or logs.

**Mitigation**:
- HubSpot access token is stored in AWS Systems Manager Parameter Store as a SecureString
- No credentials are hardcoded in the code
- Environment variables use CloudFormation's `NoEcho: true` to prevent exposure in console
- Temporary AWS credentials from STS AssumeRole expire after 1 hour
- No long-term AWS credentials are stored

**Reference**: `template.yaml` lines 13-16, 159-164

### 6. Least Privilege IAM Permissions

**Issue**: Overly broad IAM permissions increase the blast radius of a security incident.

**Mitigation**:
- Lambda execution roles only have permission to assume the service role
- The service role uses scoped-down permissions despite using `AWSPartnerCentralOpportunityManagement`
- Inline policy explicitly lists only required Partner Central actions
- No wildcard permissions on sensitive actions

**Reference**: `infra/iam-role.yaml` lines 50-80, `template.yaml` lines 101-106

### 7. Dependency Security

**Status**: ✅ No known vulnerabilities

All dependencies have been checked against the GitHub Advisory Database:
- `boto3>=1.34.0` - ✅ No vulnerabilities
- `botocore>=1.34.0` - ✅ No vulnerabilities  
- `requests>=2.31.0` - ✅ No vulnerabilities

**Maintenance**: Regularly update dependencies and re-check for vulnerabilities:
```bash
pip install --upgrade boto3 botocore requests
```

---

## 🚨 Production Deployment Checklist

Before deploying to production, ensure:

- [ ] `HUBSPOT_WEBHOOK_SECRET` is configured (not empty)
- [ ] HubSpot access token has minimum required scopes only
- [ ] CloudWatch log retention is set to appropriate period (currently 30 days)
- [ ] IAM role trust policy is reviewed for correct account ID
- [ ] API Gateway has appropriate rate limiting configured
- [ ] All Lambda environment variables use `NoEcho: true` in CloudFormation
- [ ] VPC configuration is considered if accessing internal resources

---

## 🔐 Additional Security Recommendations

### 1. Enable AWS CloudTrail

CloudTrail logs all Partner Central API calls made via the service role, providing an audit trail.

```bash
aws cloudtrail create-trail \
  --name hubspot-pc-audit \
  --s3-bucket-name your-audit-bucket
```

### 2. Configure API Gateway Rate Limiting

Prevent abuse by configuring throttling on the webhook endpoint:

```yaml
# In template.yaml under WebhookApi
UsagePlan:
  Type: AWS::ApiGateway::UsagePlan
  Properties:
    Throttle:
      RateLimit: 10      # requests per second
      BurstLimit: 20     # concurrent requests
```

### 3. Enable AWS WAF (Optional)

For additional protection against common web exploits:

```yaml
# Add to template.yaml
WebhookApiWAF:
  Type: AWS::WAFv2::WebACLAssociation
  Properties:
    ResourceArn: !Sub "arn:aws:apigateway:${AWS::Region}::/restapis/${WebhookApi}/stages/${Environment}"
    WebACLArn: !Ref WebACL
```

### 4. Rotate Credentials Regularly

**HubSpot Access Token**:
- Rotate quarterly or after any suspected compromise
- Update in SSM Parameter Store: `/hubspot-pc-sync/${Environment}/hubspot-access-token`

**Webhook Secret**:
- Rotate semi-annually
- Update both in HubSpot and the CloudFormation parameter

### 5. Monitor for Anomalies

Set up CloudWatch alarms for:
- Sudden spike in Lambda invocations (possible abuse)
- High error rates (possible attack or misconfiguration)
- Failed signature verifications (possible forgery attempts)
- AssumeRole failures (possible unauthorized access attempts)

Example alarm:
```yaml
WebhookErrorAlarm:
  Type: AWS::CloudWatch::Alarm
  Properties:
    MetricName: Errors
    Namespace: AWS/Lambda
    Statistic: Sum
    Period: 300
    EvaluationPeriods: 1
    Threshold: 10
    ComparisonOperator: GreaterThanThreshold
```

### 6. Implement IP Allowlisting (Optional)

If HubSpot's IP ranges are stable, configure API Gateway resource policies to only accept requests from known IPs.

### 7. Enable AWS GuardDuty

GuardDuty can detect unusual API activity patterns that might indicate a compromised credential.

---

## 🐛 Reporting Security Issues

If you discover a security vulnerability:

1. **DO NOT** open a public GitHub issue
2. Email the repository owner directly with details
3. Include steps to reproduce and potential impact
4. Allow reasonable time for a fix before public disclosure

---

## 📚 Security References

- [AWS Security Best Practices](https://aws.amazon.com/architecture/security-identity-compliance/)
- [HubSpot Webhook Security](https://developers.hubspot.com/docs/api/webhooks/security)
- [OWASP Top 10](https://owasp.org/www-project-top-ten/)
- [AWS Partner Central API Security](https://docs.aws.amazon.com/partner-central/latest/APIReference/Welcome.html)

---

## 🔄 Security Review History

| Date | Reviewer | Changes |
|------|----------|---------|
| 2026-02-18 | GitHub Copilot | Initial security audit and fixes |

