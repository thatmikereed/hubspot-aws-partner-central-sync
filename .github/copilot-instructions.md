# GitHub Copilot Instructions

This repository contains a bidirectional integration between **HubSpot CRM** and **AWS Partner Central**, deployed as serverless AWS Lambda functions using AWS SAM.

## Project Overview

The system has two main flows:
1. **HubSpot → AWS Partner Central**: When a HubSpot deal is created with `#AWS` in the title, it automatically creates an opportunity in AWS Partner Central
2. **AWS Partner Central → HubSpot**: Scheduled Lambda polls for pending engagement invitations, accepts them, and creates corresponding HubSpot deals

## Technology Stack

- **Runtime**: Python 3.12 (arm64)
- **Framework**: AWS SAM (Serverless Application Model)
- **Infrastructure**: AWS Lambda, API Gateway, EventBridge, IAM, CloudWatch
- **External APIs**: HubSpot CRM API, AWS Partner Central Selling API
- **Dependencies**: boto3, botocore, requests
- **Testing**: pytest, pytest-mock, moto, responses
- **Code Quality**: black, ruff, mypy

## Build, Test, and Deployment Commands

### Local Development
```bash
# Install dependencies
pip install -r requirements-dev.txt

# Run tests
pytest tests/ -v

# Run specific test file
pytest tests/test_mappers.py -v

# Format code with black
black src/ tests/

# Lint with ruff
ruff check src/ tests/

# Type check with mypy
mypy src/
```

### SAM Build and Deploy
```bash
# Build Lambda functions
sam build

# Deploy with guided setup
sam deploy --guided

# Deploy with parameters
sam deploy --parameter-overrides \
  "PartnerCentralRoleArn=$ROLE_ARN"

# Test locally
sam local invoke HubSpotToPartnerCentralFunction \
  --event tests/fixtures/sample_webhook_event.json
```

### IAM Role Deployment (must be done first)
```bash
aws cloudformation deploy \
  --template-file infra/iam-role.yaml \
  --stack-name hubspot-partner-central-iam \
  --capabilities CAPABILITY_NAMED_IAM
```

## Code Style and Conventions

### Python Style
- Follow PEP 8 conventions
- Use type hints for function signatures
- Maximum line length: 88 characters (black default)
- Use descriptive variable names
- Add docstrings for classes and non-trivial functions

### File Organization
```
src/
├── common/                      # Shared modules (clients, mappers, utilities)
│   ├── aws_client.py           # AWS STS + Partner Central client factory
│   ├── hubspot_client.py       # HubSpot API wrapper
│   ├── mappers.py              # Bidirectional field mapping logic
│   └── solution_matcher.py     # Solution ID matching utilities
├── hubspot_to_partner_central/ # Lambda: HubSpot webhook handler
│   └── handler.py
├── partner_central_to_hubspot/ # Lambda: Invitation sync handler
│   └── handler.py
├── submit_opportunity/         # Lambda: Submit opportunity to AWS
│   └── handler.py
└── sync_aws_summary/           # Lambda: Sync AWS summary data
    └── handler.py
```

### Error Handling
- Use proper exception handling with specific exception types
- Log errors with appropriate context using the logging module
- Return meaningful HTTP status codes from Lambda functions (200, 400, 500)
- Include error details in CloudWatch logs for debugging

### Testing
- Write unit tests for all new functionality
- Use moto for mocking AWS services (boto3 interactions)
- Use responses for mocking HTTP requests (HubSpot API)
- Use pytest fixtures in conftest.py for common test setup
- Aim for high test coverage of business logic

## Security Requirements

### Secrets Management
- **NEVER** hardcode API tokens, credentials, or secrets in source code
- Store sensitive data in AWS SSM Parameter Store (SecureString type)
- Access secrets via environment variables injected by Lambda
- Use `.env.example` as a template but never commit actual `.env` files

### IAM Permissions
- Use the principle of least privilege for IAM policies
- All Partner Central API calls must go through the `HubSpotPartnerCentralServiceRole`
- Lambda execution roles should only have `sts:AssumeRole` permission for the service role
- Include ExternalId in AssumeRole conditions to prevent confused deputy attacks

### API Security
- Verify HubSpot webhook signatures using HMAC-SHA256 when `HUBSPOT_WEBHOOK_SECRET` is configured
- Validate all input data before processing
- Sanitize error messages to avoid leaking sensitive information

### Dependencies
- Keep dependencies up to date to address security vulnerabilities
- Run security scans before merging changes
- Review dependency changes carefully

## Field Mapping Guidelines

When modifying `src/common/mappers.py`:
- Maintain bidirectional consistency between HubSpot ↔ Partner Central mappings
- Add comments explaining any complex transformations
- Update both `hubspot_deal_to_opportunity()` and `opportunity_to_hubspot_deal()` for new fields
- Include default values for optional fields
- Handle missing or null values gracefully

### Key Field Mappings
| HubSpot Field | Partner Central Field |
|---------------|----------------------|
| dealname | Project.Title |
| description | Project.CustomerBusinessProblem |
| dealstage | LifeCycle.Stage |
| closedate | LifeCycle.TargetCloseDate |
| amount | Project.ExpectedCustomerSpend[0].Amount |
| aws_opportunity_id | Identifier (stored back) |
| aws_invitation_id | Invitation ID (stored back) |

## Lambda Function Guidelines

### Handler Signature
- All Lambda handlers must accept `(event, context)` parameters
- Return a dict with `statusCode` and `body` for API Gateway integrations
- Use structured logging with context from the Lambda context object

### Environment Variables
Always access these environment variables:
- `HUBSPOT_ACCESS_TOKEN` - Required for HubSpot API calls
- `HUBSPOT_WEBHOOK_SECRET` - Optional but recommended for webhook verification
- `PARTNER_CENTRAL_ROLE_ARN` - ARN of the service role to assume
- `PARTNER_CENTRAL_SOLUTION_ID` - Solution ID for new opportunities
- `ENVIRONMENT` - deployment environment (production, staging, development)
- `LOG_LEVEL` - logging level (INFO, DEBUG, etc.)

### Best Practices
- Keep handlers thin - delegate to service classes in common/
- Use connection pooling and reuse clients across invocations
- Set appropriate timeouts (default: 60 seconds)
- Include idempotency tokens where applicable (e.g., deal ID as ClientToken)
- Handle rate limiting and implement retry logic for external APIs

## CloudFormation/SAM Template

When modifying `template.yaml`:
- Maintain parameter validation (MinValue, MaxValue, AllowedValues)
- Use !Sub for dynamic resource names with environment suffix
- Keep log retention at 30 days for CloudWatch log groups
- Set reasonable defaults for all parameters
- Document parameters with clear descriptions

## Git Workflow

### Commits
- Write clear, descriptive commit messages
- Keep commits focused on a single logical change
- Reference issue numbers when applicable

### Pull Requests
- Ensure all tests pass before requesting review
- Run linters and fix any issues
- Update documentation if changing functionality
- Include test coverage for new features

## Common Development Tasks

### Adding a New HubSpot Custom Property
1. Add the property definition in `hubspot_client.py`
2. Update the mapping logic in `mappers.py`
3. Add tests for the new field mapping
4. Document the field in README.md

### Adding a New Lambda Function
1. Create a new directory under `src/`
2. Add handler.py with lambda_handler function
3. Update template.yaml with new AWS::Serverless::Function resource
4. Add the CommonLayer as a dependency
5. Configure appropriate IAM policies
6. Add unit tests in tests/

### Debugging Lambda Locally
```bash
# Set up .env file with required variables
cp .env.example .env
# Edit .env with actual values

# Invoke locally with SAM
sam local invoke FunctionName \
  --event tests/fixtures/test_event.json \
  --env-vars .env.json
```

## External Dependencies

- **HubSpot CRM API**: [HubSpot API Docs](https://developers.hubspot.com/docs/api/overview)
- **AWS Partner Central API**: [Partner Central API Reference](https://docs.aws.amazon.com/partner-central/latest/selling-api/Welcome.html)
- **AWS SAM**: [SAM Developer Guide](https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/)

## Important Notes

- The IAM role (`HubSpotPartnerCentralServiceRole`) must be deployed **before** the Lambda functions
- HubSpot webhook subscriptions must be manually configured in the HubSpot UI
- Custom HubSpot properties are created automatically by the integration but can be created manually using the helper script
- All Partner Central API calls are made via assumed role credentials (no hardcoded access keys)
- The integration uses idempotency tokens to prevent duplicate opportunity creation
