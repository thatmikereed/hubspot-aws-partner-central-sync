# Security Best Practices and Considerations

This document outlines the security measures implemented in this integration and best practices for deployment and operation.

## 🔒 Security Features Implemented

### 1. Confused Deputy Attack Prevention

**Protection Implemented**: The IAM role trust policy requires an `ExternalId` condition (`"HubSpotPartnerCentralIntegration"`) and all `AssumeRole` calls include this ExternalId to prevent unauthorized cross-account access.

**Why it matters**: Without this protection, a malicious actor who knows the role ARN could potentially trick your Lambda functions into assuming the `HubSpotPartnerCentralServiceRole` on their behalf.

**Implementation**: `src/common/aws_client.py`, `infra/iam-role.yaml`

**Reference**: [AWS Confused Deputy Prevention](https://docs.aws.amazon.com/IAM/latest/UserGuide/confused-deputy.html)

### 2. Webhook Signature Verification

**Protection Implemented**: All incoming HubSpot webhooks are verified using HMAC-SHA256 signatures before processing. Signature verification happens **before** parsing the request body. Requests with missing or invalid signatures are rejected with HTTP 400.

**Why it matters**: Without signature verification, attackers could send forged webhook requests to create or modify opportunities, or exploit JSON parsing vulnerabilities.

**Configuration**: The `HUBSPOT_WEBHOOK_SECRET` environment variable must be configured in production (strongly recommended).

**Implementation**: `src/hubspot_to_partner_central/handler.py`

### 3. Sensitive Data Redaction in Logs

**Protection Implemented**: All HTTP headers containing credentials are redacted before logging to CloudWatch. Redacted headers include: `Authorization`, `X-HubSpot-Signature`, `X-HubSpot-Signature-V3`, `Cookie`, `X-Api-Key`. Large request bodies are truncated to prevent log flooding.

**Why it matters**: CloudWatch logs could expose sensitive credentials if the entire event is logged, accessible to anyone with log read permissions.

**Implementation**: `src/hubspot_to_partner_central/handler.py`

### 4. Input Validation and Sanitization

**Protection Implemented**: All external inputs are validated and sanitized before use. This includes:
- ID validation (numeric for HubSpot, alphanumeric for Partner Central)
- Maximum length enforcement (titles: 255 chars, descriptions: 10,000 chars, emails: 254 chars, URLs: 2048 chars)
- Email and URL format validation with regex patterns
- Monetary amount validation with reasonable bounds (0 to 1 trillion USD)
- Control character stripping while preserving newlines/tabs

**Why it matters**: Unvalidated inputs could lead to injection attacks, denial of service, or data corruption.

**Implementation**: `src/common/validators.py` (centralized validation module)

### 5. Secure Credential Management

**Secure Storage**: HubSpot access token is stored in AWS Systems Manager Parameter Store as a SecureString. No credentials are hardcoded in code. Environment variables use CloudFormation's `NoEcho: true` to prevent exposure in console.

**Temporary Credentials**: All AWS API calls use temporary credentials from STS AssumeRole that expire after 1 hour. No long-term AWS credentials are stored.

**Why it matters**: Hardcoded or exposed credentials in code or logs create security vulnerabilities.

**Implementation**: `template.yaml`, Parameter Store integration

### 6. Least Privilege IAM Permissions

**Implementation**: Lambda execution roles only have permission to assume the service role. The service role explicitly lists only required Partner Central actions. No wildcard permissions on sensitive actions.

**Why it matters**: Overly broad IAM permissions increase the blast radius of a security incident.

**Implementation**: `infra/iam-role.yaml`, `template.yaml`

### 7. Dependency Security

**Status**: ✅ All dependencies checked against GitHub Advisory Database with no known vulnerabilities

**Dependencies**:
- `boto3>=1.34.0` - ✅ No vulnerabilities
- `botocore>=1.34.0` - ✅ No vulnerabilities  
- `requests>=2.31.0` - ✅ No vulnerabilities

**Maintenance**: Regularly update dependencies and re-check for vulnerabilities using `pip install --upgrade boto3 botocore requests`

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

