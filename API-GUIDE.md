# API Guide

**HubSpot ↔ AWS Partner Central Integration**

This document provides comprehensive API documentation for all integration points, webhooks, and APIs used in the HubSpot-AWS Partner Central bidirectional sync system.

---

## Table of Contents

1. [Overview](#overview)
2. [HubSpot APIs](#hubspot-apis)
3. [AWS Partner Central APIs](#aws-partner-central-apis)
4. [Internal REST APIs](#internal-rest-apis)
5. [Webhook Integration](#webhook-integration)
6. [Authentication & Security](#authentication--security)
7. [Field Mappings](#field-mappings)
8. [Error Handling](#error-handling)
9. [Rate Limits & Best Practices](#rate-limits--best-practices)

---

## Overview

This integration provides bidirectional synchronization between HubSpot CRM and AWS Partner Central through a serverless architecture built on AWS Lambda and API Gateway.

### Integration Architecture

```
┌─────────────────┐          ┌──────────────────┐          ┌─────────────────────┐
│                 │          │                  │          │                     │
│    HubSpot      │◄────────►│  API Gateway +   │◄────────►│  AWS Partner        │
│      CRM        │  Webhooks│  Lambda Functions│    IAM   │   Central API       │
│                 │          │                  │ AssumeRole│                     │
└─────────────────┘          └──────────────────┘          └─────────────────────┘
        │                            │
        │                            │
        ▼                            ▼
  Webhook Events              EventBridge
  - deal.creation              - Schedule Triggers
  - deal.propertyChange        - Real-time Events
```

### Key Integration Points

| Component | Direction | Purpose |
|-----------|-----------|---------|
| **HubSpot Webhooks** | HubSpot → Lambda | Real-time deal creation and updates |
| **HubSpot REST API** | Lambda → HubSpot | Create/update deals, search, add notes |
| **Partner Central API** | Lambda → AWS | Create/update opportunities, list invitations |
| **EventBridge** | AWS → Lambda | Real-time Partner Central events |
| **API Gateway** | External → Lambda | REST endpoints for solution management |

---

## HubSpot APIs

The integration uses the HubSpot CRM API v3 for all operations. Authentication is via Private App access tokens with Bearer token authentication.

### Base URL

```
https://api.hubapi.com
```

### Authentication

All HubSpot API requests include the following header:

```http
Authorization: Bearer {HUBSPOT_ACCESS_TOKEN}
Content-Type: application/json
```

### Required Scopes

The HubSpot Private App must have the following OAuth scopes:

- `crm.objects.deals.read` - Read deal data
- `crm.objects.deals.write` - Create and update deals
- `crm.objects.companies.read` - Read company data for opportunities
- `crm.objects.contacts.read` - Read contact data for opportunities
- `crm.schemas.deals.write` - Create custom properties
- `crm.objects.notes.write` - Add notes to deals

---

### HubSpot API Endpoints Used

#### 1. Get Deal

**Endpoint:** `GET /crm/v3/objects/deals/{dealId}`

**Purpose:** Fetch full deal details with all properties

**Query Parameters:**
```
properties: dealname,amount,closedate,dealstage,pipeline,description,
           aws_opportunity_id,aws_opportunity_arn,aws_review_status,
           aws_sync_status,aws_invitation_id,aws_engagement_score
```

**Response Example:**
```json
{
  "id": "12345",
  "properties": {
    "dealname": "Acme Corp Cloud Migration #AWS",
    "amount": "500000",
    "closedate": "2025-12-31",
    "dealstage": "presentationscheduled",
    "aws_opportunity_id": "O1234567890",
    "aws_review_status": "Approved"
  },
  "createdAt": "2025-01-15T10:30:00Z",
  "updatedAt": "2025-02-01T14:20:00Z"
}
```

**Used by:**
- `hubspot_to_partner_central/handler.py`
- `hubspot_deal_update_sync/handler.py`
- `submit_opportunity/handler.py`

---

#### 2. Create Deal

**Endpoint:** `POST /crm/v3/objects/deals`

**Purpose:** Create a new deal (from Partner Central invitations)

**Request Body:**
```json
{
  "properties": {
    "dealname": "AWS Opportunity - Customer Name #AWS",
    "amount": "250000",
    "closedate": "2025-09-30",
    "dealstage": "qualifiedtobuy",
    "description": "Customer business problem description",
    "aws_opportunity_id": "O0987654321",
    "aws_invitation_id": "inv-abc123",
    "aws_review_status": "Pending Submission"
  }
}
```

**Response Example:**
```json
{
  "id": "67890",
  "properties": { ... },
  "createdAt": "2025-02-18T18:30:00Z"
}
```

**Used by:**
- `partner_central_to_hubspot/handler.py`
- `eventbridge_events/handler.py`

---

#### 3. Update Deal

**Endpoint:** `PATCH /crm/v3/objects/deals/{dealId}`

**Purpose:** Update existing deal properties

**Request Body:**
```json
{
  "properties": {
    "aws_opportunity_id": "O1234567890",
    "aws_sync_status": "synced",
    "aws_review_status": "Approved",
    "aws_engagement_score": "85"
  }
}
```

**Used by:**
- `hubspot_to_partner_central/handler.py`
- `sync_aws_summary/handler.py`
- `smart_notifications/handler.py`

---

#### 4. Search Deals

**Endpoint:** `POST /crm/v3/objects/deals/search`

**Purpose:** Find deals by AWS IDs or other criteria

**Request Body Example (Search by AWS Opportunity ID):**
```json
{
  "filterGroups": [
    {
      "filters": [
        {
          "propertyName": "aws_opportunity_id",
          "operator": "EQ",
          "value": "O1234567890"
        }
      ]
    }
  ],
  "properties": ["dealname", "aws_opportunity_id"],
  "limit": 1
}
```

**Request Body Example (Search by Review Status):**
```json
{
  "filterGroups": [
    {
      "filters": [
        {
          "propertyName": "aws_opportunity_id",
          "operator": "HAS_PROPERTY"
        },
        {
          "propertyName": "aws_review_status",
          "operator": "IN",
          "values": ["Approved", "Action Required"]
        }
      ]
    }
  ],
  "properties": ["dealname", "aws_opportunity_id", "aws_engagement_score"],
  "limit": 100
}
```

**Used by:**
- `partner_central_to_hubspot/handler.py` - Deduplication
- `sync_aws_summary/handler.py` - Find eligible deals
- `smart_notifications/handler.py` - Monitor status changes

---

#### 5. Get Company

**Endpoint:** `GET /crm/v3/objects/companies/{companyId}`

**Purpose:** Fetch company data for Partner Central Customer payload

**Query Parameters:**
```
properties: name,domain,website,industry,country,city,state,zip,
           address,phone,numberofemployees,annualrevenue
```

**Used by:**
- `hubspot_to_partner_central/handler.py`

---

#### 6. Get Contact

**Endpoint:** `GET /crm/v3/objects/contacts/{contactId}`

**Purpose:** Fetch contact data for Partner Central Customer.Contacts payload

**Query Parameters:**
```
properties: firstname,lastname,email,phone,mobilephone,jobtitle
```

**Used by:**
- `hubspot_to_partner_central/handler.py`

---

#### 7. Get Associations

**Endpoint:** `POST /crm/v3/associations/{fromObjectType}/{toObjectType}/batch/read`

**Purpose:** Get associated companies and contacts for a deal

**Request Body:**
```json
{
  "inputs": [
    {"id": "12345"}
  ]
}
```

**Response Example:**
```json
{
  "results": [
    {
      "from": {"id": "12345"},
      "to": [
        {"id": "98765", "type": "deal_to_company"},
        {"id": "54321", "type": "deal_to_contact"}
      ]
    }
  ]
}
```

**Used by:**
- `hubspot_client.py:_get_association_ids()`

---

#### 8. Create Note

**Endpoint:** `POST /crm/v3/objects/notes`

**Purpose:** Add notes to deals (AWS updates, warnings, notifications)

**Request Body:**
```json
{
  "properties": {
    "hs_note_body": "✅ Submitted to AWS Partner Central\n\nInvolvement Type: Co-Sell\nVisibility: Full",
    "hs_timestamp": "2025-02-18T18:30:00Z"
  }
}
```

**Associate Note with Deal:**

**Endpoint:** `PUT /crm/v3/objects/notes/{noteId}/associations/deals/{dealId}/note_to_deal`

**Used by:**
- `submit_opportunity/handler.py` - Submission status
- `hubspot_to_partner_central/handler.py` - Title immutability warnings
- `sync_aws_summary/handler.py` - Engagement score changes
- `smart_notifications/handler.py` - Critical event notifications
- `resource_snapshot_sync/handler.py` - AWS resource links

---

#### 9. Create Custom Properties

**Endpoint:** `POST /crm/v3/properties/deals`

**Purpose:** Create custom deal properties for AWS sync metadata

**Request Body Example:**
```json
{
  "name": "aws_opportunity_id",
  "label": "AWS Opportunity ID",
  "type": "string",
  "fieldType": "text",
  "groupName": "dealinformation",
  "description": "The AWS Partner Central Opportunity ID"
}
```

**Custom Properties Created:**

| Property Name | Type | Description |
|---------------|------|-------------|
| `aws_opportunity_id` | string | Partner Central Opportunity ID |
| `aws_opportunity_arn` | string | Partner Central Opportunity ARN |
| `aws_opportunity_title` | string | Canonical PC title (immutable) |
| `aws_review_status` | string | Lifecycle.ReviewStatus |
| `aws_sync_status` | string | Sync status (synced/error/blocked) |
| `aws_invitation_id` | string | Engagement Invitation ID |
| `aws_engagement_score` | number | AWS interest score (0-100) |
| `aws_submission_date` | datetime | When submitted to AWS |
| `aws_involvement_type` | string | Co-Sell or For Visibility Only |
| `aws_visibility` | string | Full or Limited |
| `aws_seller_name` | string | Assigned AWS seller |
| `aws_next_steps` | textarea | AWS recommended actions |
| `aws_solution_ids` | string | Comma-separated solution IDs |
| `aws_industry` | string | Industry override for PC |
| `aws_delivery_models` | string | Delivery models override |
| `aws_primary_needs` | string | Primary needs from AWS |
| `aws_use_case` | string | Customer use case |
| `aws_expected_spend` | number | Expected monthly AWS spend |

**Used by:**
- `hubspot_client.py:create_custom_properties()`

---

## AWS Partner Central APIs

The integration uses the AWS Partner Central Selling API. All API calls are made via assumed IAM role credentials (no permanent access keys).

### Base Service

```
partnercentral-selling
```

### Authentication

All Partner Central API calls use temporary STS credentials obtained by assuming the `HubSpotPartnerCentralServiceRole`:

```python
sts_client = boto3.client("sts")
credentials = sts_client.assume_role(
    RoleArn="arn:aws:iam::{account}:role/HubSpotPartnerCentralServiceRole",
    RoleSessionName="HubSpotPartnerCentralSession",
    DurationSeconds=3600,
    ExternalId="HubSpotPartnerCentralIntegration"  # Security measure
)

pc_client = boto3.client(
    "partnercentral-selling",
    region_name="us-east-1",
    aws_access_key_id=credentials["AccessKeyId"],
    aws_secret_access_key=credentials["SecretAccessKey"],
    aws_session_token=credentials["SessionToken"]
)
```

### Catalog Parameter

All Partner Central API calls include:
```python
Catalog="AWS"
```

---

### Partner Central API Operations

#### 1. CreateOpportunity

**Purpose:** Create a new opportunity in Partner Central from a HubSpot deal

**Request Parameters:**
```python
{
    "Catalog": "AWS",
    "ClientToken": "deal-12345",  # Idempotency key (deal ID)
    "Origin": "Partner Referral",
    "OpportunityType": "Net New Business",
    "NationalSecurity": "No",
    "PartnerOpportunityIdentifier": "HUBSPOT-12345",
    "PrimaryNeedsFromAws": ["Co-Sell - Architectural Validation"],
    "Customer": {
        "Account": {
            "CompanyName": "Acme Corporation",
            "Industry": "Software and Internet",
            "WebsiteUrl": "https://acme.com",
            "Address": {
                "CountryCode": "US",
                "City": "San Francisco",
                "StateOrRegion": "CA",
                "PostalCode": "94105"
            }
        },
        "Contacts": [
            {
                "FirstName": "John",
                "LastName": "Doe",
                "Email": "john.doe@acme.com",
                "Phone": "+1-415-555-0100",
                "Title": "CTO"
            }
        ]
    },
    "LifeCycle": {
        "Stage": "Technical Validation",
        "TargetCloseDate": "2025-12-31",
        "NextSteps": "Schedule technical validation call"
    },
    "Project": {
        "Title": "Acme Corp Cloud Migration",
        "CustomerBusinessProblem": "Customer needs to migrate 500TB...",
        "DeliveryModels": ["SaaS or PaaS"],
        "ExpectedCustomerSpend": [
            {
                "Amount": "500000",
                "CurrencyCode": "USD",
                "Frequency": "Monthly",
                "TargetCompany": "AWS"
            }
        ]
    }
}
```

**Response:**
```python
{
    "Id": "O1234567890",
    "Arn": "arn:aws:partnercentral-selling:us-east-1:...",
    "LastModifiedDate": datetime(2025, 2, 18, 18, 30, 0)
}
```

**Used by:**
- `hubspot_to_partner_central/handler.py`

---

#### 2. UpdateOpportunity

**Purpose:** Sync HubSpot deal changes to Partner Central

**Request Parameters:**
```python
{
    "Catalog": "AWS",
    "Identifier": "O1234567890",
    "LastModifiedDate": datetime(2025, 2, 18, 12, 0, 0),  # Optimistic locking
    "LifeCycle": {
        "Stage": "Business Validation",
        "TargetCloseDate": "2025-11-30"
    },
    "Project": {
        "ExpectedCustomerSpend": [
            {
                "Amount": "750000",
                "CurrencyCode": "USD",
                "Frequency": "Monthly"
            }
        ]
    }
}
```

**Notes:**
- `Project.Title` is IMMUTABLE after opportunity is submitted
- Updates are BLOCKED when `ReviewStatus` is "Submitted" or "In Review"
- `LastModifiedDate` prevents concurrent modification conflicts

**Used by:**
- `hubspot_to_partner_central/handler.py` (deal.propertyChange webhook)
- `hubspot_deal_update_sync/handler.py`

---

#### 3. GetOpportunity

**Purpose:** Fetch full opportunity details

**Request Parameters:**
```python
{
    "Catalog": "AWS",
    "Identifier": "O1234567890"
}
```

**Response Example:**
```python
{
    "Id": "O1234567890",
    "Arn": "arn:aws:partnercentral-selling:us-east-1:...",
    "Customer": { ... },
    "LifeCycle": {
        "Stage": "Technical Validation",
        "ReviewStatus": "Approved",
        "TargetCloseDate": "2025-12-31"
    },
    "Project": {
        "Title": "Acme Corp Cloud Migration",
        "CustomerBusinessProblem": "...",
        "DeliveryModels": ["SaaS or PaaS"]
    },
    "LastModifiedDate": datetime(2025, 2, 18, 18, 30, 0)
}
```

**Used by:**
- `partner_central_to_hubspot/handler.py`
- `hubspot_to_partner_central/handler.py` (before updates)
- `submit_opportunity/handler.py` (validation)

---

#### 4. ListEngagementInvitations

**Purpose:** Poll for pending AWS engagement invitations

**Request Parameters:**
```python
{
    "Catalog": "AWS",
    "ParticipantType": "RECEIVER",
    "PayloadType": ["OpportunityInvitation"],
    "MaxResults": 50,
    "Sort": {
        "SortBy": "InvitationDate",
        "SortOrder": "DESCENDING"
    },
    "NextToken": "..."  # Pagination
}
```

**Response Example:**
```python
{
    "EngagementInvitationSummaries": [
        {
            "Id": "inv-abc123",
            "Arn": "arn:aws:partnercentral-selling:us-east-1:...",
            "Status": "PENDING",
            "InvitationDate": datetime(2025, 2, 18, 10, 0, 0),
            "PayloadType": "OpportunityInvitation"
        }
    ],
    "NextToken": "..."
}
```

**Used by:**
- `partner_central_to_hubspot/handler.py` (scheduled polling)

---

#### 5. GetEngagementInvitation

**Purpose:** Fetch full invitation details including opportunity ID

**Request Parameters:**
```python
{
    "Catalog": "AWS",
    "Identifier": "inv-abc123"
}
```

**Response Example:**
```python
{
    "Id": "inv-abc123",
    "Arn": "arn:aws:partnercentral-selling:us-east-1:...",
    "Status": "PENDING",
    "Payload": {
        "OpportunityInvitation": {
            "Customer": { ... },
            "Project": { ... }
        }
    },
    "InvitationDate": datetime(2025, 2, 18, 10, 0, 0)
}
```

**Used by:**
- `partner_central_to_hubspot/handler.py`

---

#### 6. StartEngagementByAcceptingInvitationTask

**Purpose:** Accept an AWS engagement invitation and start engagement

**Request Parameters:**
```python
{
    "Catalog": "AWS",
    "Identifier": "inv-abc123",
    "ClientToken": "hs-accept-uuid-12345"
}
```

**Response Example:**
```python
{
    "TaskId": "task-xyz789",
    "TaskStatus": "IN_PROGRESS",
    "OpportunityId": "O0987654321"
}
```

**Notes:**
- This is an ASYNC operation that may complete immediately or require polling
- Returns both TaskId and OpportunityId
- Replaces the non-existent `AcceptEngagementInvitation` API

**Polling for Task Completion:**

**API:** `GetEngagementByAcceptingInvitationTask`

**Request Parameters:**
```python
{
    "Catalog": "AWS",
    "TaskIdentifier": "task-xyz789"
}
```

**Response Example:**
```python
{
    "TaskId": "task-xyz789",
    "TaskStatus": "COMPLETE",
    "OpportunityId": "O0987654321",
    "Message": "Invitation accepted successfully"
}
```

**Used by:**
- `partner_central_to_hubspot/handler.py`

---

#### 7. StartEngagementFromOpportunityTask

**Purpose:** Submit an opportunity to AWS for co-sell review

**Request Parameters:**
```python
{
    "Catalog": "AWS",
    "Identifier": "O1234567890",
    "ClientToken": "submit-12345-1708282200",
    "AwsSubmission": {
        "InvolvementType": "Co-Sell",  # or "For Visibility Only"
        "Visibility": "Full"            # or "Limited"
    }
}
```

**Response Example:**
```python
{
    "TaskId": "task-submit-789",
    "TaskStatus": "IN_PROGRESS"
}
```

**Polling for Task Completion:**

**API:** `GetEngagementFromOpportunityTask`

**Request Parameters:**
```python
{
    "Catalog": "AWS",
    "TaskIdentifier": "task-submit-789"
}
```

**Response Example:**
```python
{
    "TaskId": "task-submit-789",
    "TaskStatus": "COMPLETE",
    "Message": "Opportunity submitted successfully"
}
```

**Used by:**
- `submit_opportunity/handler.py`

---

#### 8. AssociateOpportunity

**Purpose:** Associate a solution with an opportunity

**Request Parameters:**
```python
{
    "Catalog": "AWS",
    "OpportunityIdentifier": "O1234567890",
    "RelatedEntityIdentifier": "S-0000001",
    "RelatedEntityType": "Solutions"
}
```

**Used by:**
- `hubspot_to_partner_central/handler.py`
- `solution_matcher.py:associate_multiple_solutions()`

---

#### 9. ListSolutions

**Purpose:** List available Partner Central solutions for matching

**Request Parameters:**
```python
{
    "Catalog": "AWS",
    "Category": ["Database"],  # Optional filter
    "Status": ["Active"],
    "MaxResults": 100,
    "NextToken": "..."
}
```

**Response Example:**
```python
{
    "SolutionSummaries": [
        {
            "Id": "S-0000001",
            "Arn": "arn:aws:partnercentral-selling:us-east-1:...",
            "Name": "AWS Database Migration Service",
            "Category": "Database",
            "Status": "Active"
        }
    ],
    "NextToken": "..."
}
```

**Used by:**
- `solution_matcher.py:get_cached_solutions()`
- `solution_management/handler.py`

---

#### 10. GetAwsOpportunitySummary

**Purpose:** Fetch AWS's view of an opportunity (engagement score, seller, feedback)

**Request Parameters:**
```python
{
    "Catalog": "AWS",
    "Identifier": "O1234567890"
}
```

**Response Example:**
```python
{
    "Catalog": "AWS",
    "Customer": { ... },
    "Insights": {
        "EngagementScore": 85,  # 0-100 AWS interest score
        "NextBestActions": "Schedule joint customer call"
    },
    "LifeCycle": {
        "ReviewStatus": "Approved",
        "InvolvementType": "Co-Sell",
        "NextSteps": "AWS seller will reach out within 2 business days"
    },
    "OpportunityTeam": [
        {
            "FirstName": "Jane",
            "LastName": "Smith",
            "Email": "jsmith@amazon.com",
            "BusinessTitle": "Partner Development Manager"
        }
    ],
    "Project": { ... }
}
```

**Used by:**
- `sync_aws_summary/handler.py`

---

#### 11. GetResourceSnapshot

**Purpose:** Fetch AWS-provided resources for an engagement (whitepapers, case studies)

**Request Parameters:**
```python
{
    "Catalog": "AWS",
    "EngagementIdentifier": "eng-xyz123",
    "ResourceType": "All"  # or specific type
}
```

**Response Example:**
```python
{
    "Catalog": "AWS",
    "EngagementId": "eng-xyz123",
    "Resources": [
        {
            "Id": "res-abc456",
            "Type": "Case Study",
            "Name": "Healthcare Migration Success Story",
            "Description": "How Hospital XYZ migrated to AWS...",
            "Url": "https://partner-central.aws/resources/res-abc456"
        }
    ]
}
```

**Used by:**
- `resource_snapshot_sync/handler.py`

---

#### 12. ListEngagements

**Purpose:** List engagements for an opportunity

**Request Parameters:**
```python
{
    "Catalog": "AWS",
    "Identifier": ["O1234567890"],
    "MaxResults": 10
}
```

**Response Example:**
```python
{
    "EngagementSummaries": [
        {
            "Id": "eng-xyz123",
            "Arn": "arn:aws:partnercentral-selling:us-east-1:...",
            "Title": "Co-Sell Engagement - Acme Corp",
            "CreatedDate": datetime(2025, 2, 15, 10, 0, 0)
        }
    ]
}
```

**Used by:**
- `resource_snapshot_sync/handler.py`

---

## Internal REST APIs

The integration exposes several REST API endpoints via API Gateway for manual operations and external integrations.

### Base URL

```
https://{api-gateway-id}.execute-api.{region}.amazonaws.com/{stage}
```

Example:
```
https://abc123xyz.execute-api.us-east-1.amazonaws.com/production
```

### Authentication

No authentication required by default for webhook endpoints (verified via HubSpot signature). API endpoints can be secured with API keys or AWS IAM authentication if needed.

---

### REST API Endpoints

#### 1. HubSpot Webhook Receiver

**Endpoint:** `POST /webhook/hubspot`

**Purpose:** Receive HubSpot deal webhooks for creation and updates

**Request Headers:**
```http
Content-Type: application/json
X-HubSpot-Signature-v3: sha256={signature}
```

**Request Body:**
```json
[
  {
    "objectId": 12345,
    "propertyName": "dealname",
    "propertyValue": "Acme Corp Cloud Migration #AWS",
    "subscriptionType": "deal.creation",
    "occurredAt": 1708282200000
  }
]
```

**Response:**
```json
{
  "processed": 1,
  "skipped": 0,
  "errors": 0,
  "results": [
    {
      "action": "created",
      "hubspotDealId": "12345",
      "dealName": "Acme Corp Cloud Migration #AWS",
      "partnerCentralOpportunityId": "O1234567890",
      "solutionsAssociated": 3
    }
  ]
}
```

**Lambda:** `hubspot_to_partner_central/handler.py`

**Webhook Events Processed:**
- `deal.creation` - Creates Partner Central opportunity
- `deal.propertyChange` - Syncs updates to Partner Central

---

#### 2. Deal Update Webhook

**Endpoint:** `POST /webhook/deal-update`

**Purpose:** Dedicated endpoint for HubSpot deal property changes

**Request Body:** (Same as HubSpot webhook format)

**Response:**
```json
{
  "processed": 1,
  "results": [
    {
      "action": "updated",
      "hubspotDealId": "12345",
      "partnerCentralOpportunityId": "O1234567890",
      "changedProperty": "dealstage",
      "warnings": []
    }
  ]
}
```

**Lambda:** `hubspot_deal_update_sync/handler.py`

---

#### 3. Submit Opportunity

**Endpoint:** `POST /submit-opportunity`

**Purpose:** Submit an opportunity to AWS for co-sell review

**Request Body:**
```json
{
  "dealId": "12345",
  "involvementType": "Co-Sell",
  "visibility": "Full"
}
```

**Response (Success):**
```json
{
  "status": "submitted",
  "dealId": "12345",
  "opportunityId": "O1234567890",
  "taskId": "task-submit-789",
  "involvementType": "Co-Sell",
  "visibility": "Full",
  "submissionDate": "2025-02-18T18:30:00Z"
}
```

**Response (Already Submitted):**
```json
{
  "status": "already_submitted",
  "message": "Opportunity already in state: Approved",
  "dealId": "12345",
  "opportunityId": "O1234567890"
}
```

**Response (Validation Failed):**
```json
{
  "status": "validation_failed",
  "errors": [
    "Customer.Account.Industry is required",
    "Project.CustomerBusinessProblem too short (15 chars, need 20+)"
  ],
  "dealId": "12345",
  "opportunityId": "O1234567890"
}
```

**Lambda:** `submit_opportunity/handler.py`

---

#### 4. List Solutions

**Endpoint:** `GET /solutions`

**Purpose:** List available Partner Central solutions

**Query Parameters:**
- `category` (optional) - Filter by category (e.g., "Database")
- `status` (optional) - Filter by status (default: "Active")
- `limit` (optional) - Max results (default: 100, max: 100)
- `nextToken` (optional) - Pagination token

**Response:**
```json
{
  "solutions": [
    {
      "id": "S-0000001",
      "name": "AWS Database Migration Service",
      "category": "Database",
      "status": "Active"
    },
    {
      "id": "S-0000002",
      "name": "MongoDB Atlas on AWS",
      "category": "Database",
      "status": "Active"
    }
  ],
  "count": 2,
  "nextToken": null
}
```

**Lambda:** `solution_management/handler.py`

---

#### 5. Search Solutions

**Endpoint:** `GET /solutions/search`

**Purpose:** Search solutions by keyword

**Query Parameters:**
- `q` (required) - Search query
- `category` (optional) - Filter by category
- `limit` (optional) - Max results (default: 50)

**Response:**
```json
{
  "solutions": [
    {
      "id": "S-0000001",
      "name": "AWS Database Migration Service",
      "category": "Database",
      "status": "Active",
      "relevanceScore": 95
    }
  ],
  "count": 1,
  "query": "database migration"
}
```

**Lambda:** `solution_management/handler.py`

---

#### 6. Get Solution Details

**Endpoint:** `GET /solutions/{solutionId}`

**Purpose:** Get detailed information about a specific solution

**Response:**
```json
{
  "id": "S-0000001",
  "arn": "arn:aws:partnercentral-selling:us-east-1:...",
  "name": "AWS Database Migration Service",
  "category": "Database",
  "status": "Active",
  "description": "Comprehensive database migration solution...",
  "createdDate": "2024-01-01T00:00:00Z"
}
```

**Lambda:** `solution_management/handler.py`

---

## Webhook Integration

### HubSpot Webhooks

The integration uses HubSpot's v3 webhook API to receive real-time notifications when deals are created or updated.

#### Webhook Setup

1. **Configure in HubSpot:**
   - Navigate to: Settings → Integrations → Private Apps → Your App → Webhooks
   - Add subscription for `deal.creation`
   - Add subscription for `deal.propertyChange`
   - Set target URL to API Gateway endpoint

2. **Target URLs:**
   ```
   POST https://{api-gateway-id}.execute-api.{region}.amazonaws.com/production/webhook/hubspot
   POST https://{api-gateway-id}.execute-api.{region}.amazonaws.com/production/webhook/deal-update
   ```

3. **Event Types:**
   - `deal.creation` - Triggered when a deal is created
   - `deal.propertyChange` - Triggered when specific properties change

#### Webhook Payload Format

**Example deal.creation event:**
```json
[
  {
    "objectId": 12345,
    "propertyName": "dealname",
    "propertyValue": "Acme Corp Cloud Migration #AWS",
    "changeSource": "CRM",
    "eventId": 1234567890,
    "subscriptionId": 9876543,
    "portalId": 123456,
    "appId": 654321,
    "occurredAt": 1708282200000,
    "subscriptionType": "deal.creation",
    "attemptNumber": 0
  }
]
```

**Example deal.propertyChange event:**
```json
[
  {
    "objectId": 12345,
    "propertyName": "dealstage",
    "propertyValue": "presentationscheduled",
    "changeSource": "CRM",
    "eventId": 1234567891,
    "subscriptionId": 9876544,
    "portalId": 123456,
    "appId": 654321,
    "occurredAt": 1708282260000,
    "subscriptionType": "deal.propertyChange",
    "attemptNumber": 0
  }
]
```

#### Webhook Security

**Signature Verification:**

HubSpot signs all webhook requests with HMAC-SHA256. The integration verifies signatures when `HUBSPOT_WEBHOOK_SECRET` is configured.

**Signature Header:**
```
X-HubSpot-Signature-v3: sha256={hex_signature}
```

**Verification Process:**
```python
import hmac
import hashlib

def verify_signature(payload: bytes, signature: str, secret: str) -> bool:
    expected = hmac.new(
        secret.encode("utf-8"),
        payload,
        hashlib.sha256
    ).hexdigest()
    
    sig_clean = signature.lstrip("sha256=")
    return hmac.compare_digest(expected, sig_clean)
```

**Implementation:**
- Located in: `src/common/hubspot_client.py:verify_webhook_signature()`
- Used by: `hubspot_to_partner_central/handler.py`

---

### EventBridge Integration

The integration uses AWS EventBridge to receive real-time events from AWS Partner Central.

#### EventBridge Rule Configuration

**Rule Pattern:**
```json
{
  "source": ["aws.partnercentral-selling"],
  "detail-type": [
    "Opportunity Created",
    "Opportunity Updated",
    "Engagement Invitation Created"
  ]
}
```

**Target:** `EventBridgeEventsFunction` Lambda

#### Event Types

##### 1. Engagement Invitation Created

**Event Structure:**
```json
{
  "version": "0",
  "id": "event-uuid",
  "detail-type": "Engagement Invitation Created",
  "source": "aws.partnercentral-selling",
  "account": "123456789012",
  "time": "2025-02-18T18:30:00Z",
  "region": "us-east-1",
  "resources": ["arn:aws:partnercentral-selling:us-east-1:..."],
  "detail": {
    "invitationId": "inv-abc123",
    "status": "PENDING",
    "invitationDate": "2025-02-18T18:30:00Z"
  }
}
```

**Action:** Auto-accept invitation and create HubSpot deal

---

##### 2. Opportunity Updated

**Event Structure:**
```json
{
  "version": "0",
  "id": "event-uuid",
  "detail-type": "Opportunity Updated",
  "source": "aws.partnercentral-selling",
  "detail": {
    "opportunityId": "O1234567890",
    "changedFields": ["LifeCycle.Stage", "LifeCycle.ReviewStatus"],
    "newValues": {
      "LifeCycle.Stage": "Business Validation",
      "LifeCycle.ReviewStatus": "Approved"
    }
  }
}
```

**Action:** Sync changes back to HubSpot (reverse sync)

---

##### 3. Opportunity Created

**Event Structure:**
```json
{
  "version": "0",
  "id": "event-uuid",
  "detail-type": "Opportunity Created",
  "source": "aws.partnercentral-selling",
  "detail": {
    "opportunityId": "O0987654321",
    "createdBy": "aws",
    "engagementId": "eng-xyz123"
  }
}
```

**Action:** Create corresponding HubSpot deal (multi-partner scenarios)

---

## Authentication & Security

### HubSpot Authentication

**Method:** Private App Access Token (Bearer token)

**Token Location:** 
- Environment variable: `HUBSPOT_ACCESS_TOKEN`
- SSM Parameter Store: `/hubspot-pc-sync/{environment}/hubspot-access-token`

**Token Format:**
```
pat-na1-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
```

**Token Permissions:**
- `crm.objects.deals.read`
- `crm.objects.deals.write`
- `crm.objects.companies.read`
- `crm.objects.contacts.read`
- `crm.schemas.deals.write`
- `crm.objects.notes.write`

---

### AWS Partner Central Authentication

**Method:** IAM Role Assumption via AWS STS

**Architecture:**
```
Lambda Execution Role
    ├─ Policy: sts:AssumeRole
    └─ Assumes → HubSpotPartnerCentralServiceRole
                    └─ Policy: partnercentral-selling:*
```

**Security Features:**
- **ExternalId:** `HubSpotPartnerCentralIntegration` (prevents confused deputy)
- **Temporary credentials:** 1-hour session duration
- **No permanent keys:** All access via STS
- **Least privilege:** Lambda execution roles have minimal permissions

**Role ARN:**
```
arn:aws:iam::{account}:role/HubSpotPartnerCentralServiceRole
```

**Assume Role Code:**
```python
from common.aws_client import get_partner_central_client

# Automatically handles role assumption
pc_client = get_partner_central_client()
```

---

### Webhook Security

#### HubSpot Webhook Signatures

**Algorithm:** HMAC-SHA256

**Header:** `X-HubSpot-Signature-v3`

**Format:** `sha256={hex_digest}`

**Verification:**
1. Concatenate request method + URI + body
2. Compute HMAC-SHA256 with shared secret
3. Compare with provided signature (constant-time comparison)

**Configuration:**
- Environment variable: `HUBSPOT_WEBHOOK_SECRET`
- Optional but strongly recommended

---

### IAM Permissions

#### Lambda Execution Role Permissions

**Minimum Permissions:**
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "logs:CreateLogGroup",
        "logs:CreateLogStream",
        "logs:PutLogEvents"
      ],
      "Resource": "arn:aws:logs:*:*:*"
    },
    {
      "Effect": "Allow",
      "Action": "sts:AssumeRole",
      "Resource": "arn:aws:iam::*:role/HubSpotPartnerCentralServiceRole"
    }
  ]
}
```

#### HubSpotPartnerCentralServiceRole Permissions

**Partner Central Permissions:**
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "partnercentral-selling:CreateOpportunity",
        "partnercentral-selling:UpdateOpportunity",
        "partnercentral-selling:GetOpportunity",
        "partnercentral-selling:ListOpportunities",
        "partnercentral-selling:AssociateOpportunity",
        "partnercentral-selling:ListEngagementInvitations",
        "partnercentral-selling:GetEngagementInvitation",
        "partnercentral-selling:StartEngagementByAcceptingInvitationTask",
        "partnercentral-selling:GetEngagementByAcceptingInvitationTask",
        "partnercentral-selling:StartEngagementFromOpportunityTask",
        "partnercentral-selling:GetEngagementFromOpportunityTask",
        "partnercentral-selling:GetAwsOpportunitySummary",
        "partnercentral-selling:GetResourceSnapshot",
        "partnercentral-selling:ListEngagements",
        "partnercentral-selling:ListSolutions"
      ],
      "Resource": "*"
    }
  ]
}
```

**Trust Policy:**
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "AWS": "arn:aws:iam::{account}:root"
      },
      "Action": "sts:AssumeRole",
      "Condition": {
        "StringEquals": {
          "sts:ExternalId": "HubSpotPartnerCentralIntegration"
        }
      }
    }
  ]
}
```

---

## Field Mappings

### HubSpot Deal → Partner Central Opportunity

The mapper module (`src/common/mappers.py`) provides bidirectional field translation.

#### Customer Mapping

| HubSpot Source | Partner Central Field | Transformation |
|----------------|----------------------|----------------|
| Company `name` | `Customer.Account.CompanyName` | Direct |
| Company `website` | `Customer.Account.WebsiteUrl` | Add `https://` if missing |
| Company `industry` | `Customer.Account.Industry` | Enum mapping (see below) |
| Company `country` | `Customer.Account.Address.CountryCode` | ISO 3166-1 alpha-2 |
| Company `city` | `Customer.Account.Address.City` | Direct |
| Company `state` | `Customer.Account.Address.StateOrRegion` | Direct |
| Company `zip` | `Customer.Account.Address.PostalCode` | Direct |
| Contact `firstname` | `Customer.Contacts[].FirstName` | Direct |
| Contact `lastname` | `Customer.Contacts[].LastName` | Direct |
| Contact `email` | `Customer.Contacts[].Email` | Direct |
| Contact `phone` | `Customer.Contacts[].Phone` | Direct |
| Contact `jobtitle` | `Customer.Contacts[].Title` | Direct |

#### Lifecycle Mapping

| HubSpot Field | Partner Central Field | Transformation |
|---------------|----------------------|----------------|
| `dealstage` | `LifeCycle.Stage` | Stage mapping (see below) |
| `closedate` | `LifeCycle.TargetCloseDate` | ISO 8601 date |
| - | `LifeCycle.NextSteps` | Generated from stage |
| - | `LifeCycle.ReviewStatus` | Defaults to "Pending Submission" |

**Stage Mapping:**

| HubSpot Stage | Partner Central Stage |
|---------------|----------------------|
| `appointmentscheduled` | Prospect |
| `qualifiedtobuy` | Qualified |
| `presentationscheduled` | Technical Validation |
| `decisionmakerboughtin` | Business Validation |
| `contractsent` | Committed |
| `closedwon` | Launched |
| `closedlost` | Closed Lost |

#### Project Mapping

| HubSpot Field | Partner Central Field | Transformation |
|---------------|----------------------|----------------|
| `dealname` | `Project.Title` | Remove `#AWS` tag |
| `description` | `Project.CustomerBusinessProblem` | Min 20 chars, max 2000 |
| `aws_delivery_models` | `Project.DeliveryModels` | CSV → Array |
| `aws_use_case` | `Project.CustomerUseCase` | Direct |
| `amount` | `Project.ExpectedCustomerSpend[0].Amount` | Convert to string |
| `deal_currency_code` | `Project.ExpectedCustomerSpend[0].CurrencyCode` | Default: "USD" |
| - | `Project.ExpectedCustomerSpend[0].Frequency` | Default: "Monthly" |

#### Metadata Mapping

| HubSpot Field | Partner Central Field | Transformation |
|---------------|----------------------|----------------|
| `deal.id` | `ClientToken` | Idempotency key |
| `deal.id` | `PartnerOpportunityIdentifier` | Prefix: "HUBSPOT-" |
| `aws_primary_needs` | `PrimaryNeedsFromAws` | CSV → Array |
| - | `Origin` | "Partner Referral" |
| - | `OpportunityType` | "Net New Business" |
| - | `NationalSecurity` | "No" |

---

### Partner Central Opportunity → HubSpot Deal

#### Reverse Mapping

| Partner Central Field | HubSpot Field | Transformation |
|----------------------|---------------|----------------|
| `Project.Title` | `dealname` | Append " #AWS" |
| `Project.CustomerBusinessProblem` | `description` | Direct |
| `LifeCycle.Stage` | `dealstage` | Reverse stage mapping |
| `LifeCycle.TargetCloseDate` | `closedate` | ISO 8601 date |
| `Project.ExpectedCustomerSpend[0].Amount` | `amount` | Convert to number |
| `Id` | `aws_opportunity_id` | Direct |
| `Arn` | `aws_opportunity_arn` | Direct |
| `LifeCycle.ReviewStatus` | `aws_review_status` | Direct |
| Invitation ID | `aws_invitation_id` | From invitation context |

---

### Industry Mapping

**HubSpot → Partner Central:**

| HubSpot Industry | Partner Central Industry |
|------------------|-------------------------|
| BANKING, FINANCE, FINANCIAL_SERVICES | Financial Services |
| COMPUTER_SOFTWARE | Software and Internet |
| HEALTHCARE | Healthcare |
| MANUFACTURING | Manufacturing |
| RETAIL | Retail |
| EDUCATION | Education |
| GOVERNMENT | Government |
| HOSPITALITY | Hospitality |
| (see full list in mappers.py) | - |

---

### Custom Property Mappings

#### AWS-specific Properties

| Property | Direction | Description |
|----------|-----------|-------------|
| `aws_opportunity_id` | Both | Partner Central Opportunity ID |
| `aws_invitation_id` | PC → HS | Engagement Invitation ID |
| `aws_opportunity_title` | Both | Canonical PC title (immutable) |
| `aws_review_status` | PC → HS | Lifecycle.ReviewStatus |
| `aws_sync_status` | HS → PC | Sync state tracking |
| `aws_engagement_score` | PC → HS | AWS interest score (0-100) |
| `aws_seller_name` | PC → HS | Assigned AWS seller |
| `aws_next_steps` | PC → HS | AWS recommendations |
| `aws_involvement_type` | Both | Co-Sell or For Visibility Only |
| `aws_visibility` | Both | Full or Limited |

---

## Error Handling

### HTTP Status Codes

#### Success Codes

| Code | Meaning | Usage |
|------|---------|-------|
| 200 | OK | Successful operation |
| 201 | Created | Resource created (not currently used) |

#### Client Error Codes

| Code | Meaning | Usage |
|------|---------|-------|
| 400 | Bad Request | Invalid input, missing required fields |
| 404 | Not Found | Deal/opportunity not found |
| 409 | Conflict | Duplicate operation (idempotency) |

#### Server Error Codes

| Code | Meaning | Usage |
|------|---------|-------|
| 500 | Internal Server Error | Lambda errors, API failures |

---

### Error Response Format

**Standard Error Response:**
```json
{
  "statusCode": 400,
  "body": {
    "error": "dealId is required"
  }
}
```

**Validation Error Response:**
```json
{
  "status": "validation_failed",
  "errors": [
    "Customer.Account.CompanyName is required",
    "Project.CustomerBusinessProblem too short (15 chars, need 20+)"
  ],
  "dealId": "12345",
  "opportunityId": "O1234567890"
}
```

---

### Common Error Scenarios

#### 1. Missing AWS Tag

**Scenario:** Deal created without `#AWS` in title

**Behavior:** Silently skipped (not an error)

**Response:**
```json
{
  "processed": 0,
  "skipped": 1,
  "errors": 0
}
```

---

#### 2. Duplicate Opportunity

**Scenario:** Webhook fired twice for same deal

**Behavior:** Idempotency check prevents duplicate creation

**Log Message:**
```
Deal 12345 already synced to PC opportunity O1234567890
```

**Response:**
```json
{
  "processed": 0,
  "skipped": 1,
  "errors": 0
}
```

---

#### 3. Title Immutability Violation

**Scenario:** Sales rep changes deal name after submission

**Behavior:** Update blocked, note added to deal

**Note Added to HubSpot:**
```
🔒 AWS Partner Central — Title Change Blocked

Project.Title is immutable in Partner Central after submission.
The title 'Original Title' cannot be changed to 'New Title'.
Please contact AWS support if a title change is critical.
```

**Response:**
```json
{
  "action": "blocked",
  "hubspotDealId": "12345",
  "partnerCentralOpportunityId": "O1234567890",
  "warnings": [
    "Project.Title is immutable in Partner Central after submission"
  ]
}
```

---

#### 4. Update During AWS Review

**Scenario:** Sales rep updates deal while AWS is reviewing

**Behavior:** Update blocked to prevent review disruption

**Response:**
```json
{
  "action": "blocked",
  "hubspotDealId": "12345",
  "partnerCentralOpportunityId": "O1234567890",
  "warnings": [
    "Opportunity is under AWS review (status: In Review). Updates are blocked."
  ]
}
```

---

#### 5. HubSpot API Rate Limit

**Scenario:** Too many API calls to HubSpot

**Behavior:** Lambda retries with exponential backoff

**HTTP Response from HubSpot:**
```
429 Too Many Requests
Retry-After: 60
```

**Lambda Behavior:**
- Waits for Retry-After duration
- Retries up to 3 times
- If all retries fail, logs error and continues

---

#### 6. Partner Central API Error

**Scenario:** Partner Central API returns error

**Example Errors:**
- `ValidationException` - Invalid input data
- `ConflictException` - Concurrent modification
- `ResourceNotFoundException` - Opportunity not found
- `ThrottlingException` - Rate limit exceeded

**Lambda Behavior:**
```python
try:
    pc_client.create_opportunity(...)
except ClientError as e:
    error_code = e.response['Error']['Code']
    if error_code == 'ValidationException':
        # Log validation details
        logger.error("Validation failed: %s", e.response['Error']['Message'])
    elif error_code == 'ThrottlingException':
        # Retry with backoff
        time.sleep(2)
        # Retry logic...
    else:
        # Log and re-raise
        logger.exception("Partner Central API error")
        raise
```

---

### Logging and Monitoring

#### CloudWatch Logs

**Log Groups:**
```
/aws/lambda/hubspot-to-partner-central-{environment}
/aws/lambda/partner-central-to-hubspot-{environment}
/aws/lambda/submit-opportunity-{environment}
/aws/lambda/sync-aws-summary-{environment}
... (all Lambda functions)
```

**Log Retention:** 30 days

**Log Format:**
```
[INFO] 2025-02-18T18:30:00.123Z request-id Processing deal creation 12345: 'Acme Corp #AWS'
[INFO] 2025-02-18T18:30:00.456Z request-id Created PC opportunity O1234567890 for deal 12345
```

#### Error Tracking

**Structured Error Logs:**
```python
logger.error(
    "Error processing deal %s: %s",
    deal_id,
    str(exc),
    extra={
        "dealId": deal_id,
        "opportunityId": opportunity_id,
        "errorType": type(exc).__name__
    }
)
```

#### Metrics to Monitor

| Metric | Threshold | Action |
|--------|-----------|--------|
| Lambda Errors | > 5% | Investigate logs |
| Lambda Duration | > 50s | Optimize code |
| HubSpot API Errors | > 2% | Check token/permissions |
| Partner Central Errors | > 5% | Check IAM role |
| Webhook Failures | > 1% | Check signature verification |

---

## Rate Limits & Best Practices

### HubSpot API Rate Limits

**Limits (as of 2025):**
- **Professional/Enterprise:** 100 requests per 10 seconds
- **Burst:** 150 requests per 10 seconds
- **Daily:** 500,000 requests per day

**Integration Behavior:**
- Uses connection pooling (`requests.Session`)
- Respects `Retry-After` headers
- Implements exponential backoff on 429 errors

**Best Practices:**
- Batch operations when possible
- Cache company/contact data
- Use webhook subscriptions instead of polling

---

### AWS Partner Central Rate Limits

**General Limits:**
- Variable per API operation
- Typically 10-100 TPS per operation
- Burst capacity available

**Integration Behavior:**
- Uses AWS SDK default retry logic
- Exponential backoff on `ThrottlingException`
- Connection pooling via boto3 client reuse

**Best Practices:**
- Reuse boto3 clients across Lambda invocations
- Use pagination for list operations
- Implement exponential backoff

---

### API Gateway Limits

**Default Limits:**
- **Burst:** 5,000 requests per second
- **Steady-state:** 10,000 requests per second
- **Concurrent connections:** 10,000

**Integration Configuration:**
- No throttling configured by default
- Can add usage plans if needed

---

### Lambda Concurrency

**Configuration:**
- **Reserved concurrency:** Not set (uses account default)
- **Timeout:** 60 seconds per function
- **Memory:** 256 MB

**Best Practices:**
- Schedule-triggered functions (like sync) run sequentially
- Webhook-triggered functions auto-scale with API Gateway
- Monitor concurrent executions in CloudWatch

---

### Best Practices Summary

#### For HubSpot Integration

1. **Use webhooks** instead of polling for real-time updates
2. **Verify signatures** on all webhook requests
3. **Cache property schemas** to reduce API calls
4. **Batch updates** when syncing multiple deals
5. **Handle 429 errors** with exponential backoff

#### For Partner Central Integration

1. **Reuse clients** across Lambda invocations (global scope)
2. **Use ClientToken** for idempotency on create operations
3. **Check LastModifiedDate** before updates (optimistic locking)
4. **Poll async tasks** with exponential backoff
5. **Cache solution lists** to reduce ListSolutions calls

#### For Lambda Functions

1. **Keep functions stateless** - no local file dependencies
2. **Use environment variables** for configuration
3. **Implement proper error handling** with structured logging
4. **Monitor CloudWatch metrics** for errors and duration
5. **Test locally** with SAM CLI before deploying

#### Security Best Practices

1. **Store secrets in SSM** Parameter Store (SecureString)
2. **Use IAM roles** instead of access keys
3. **Implement ExternalId** in trust policies
4. **Rotate tokens** regularly (HubSpot Private App tokens)
5. **Enable CloudTrail** for audit logging

---

## Appendix

### Environment Variables Reference

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `HUBSPOT_ACCESS_TOKEN` | ✅ | - | HubSpot Private App token |
| `HUBSPOT_WEBHOOK_SECRET` | ⚠️ | "" | Webhook signature secret |
| `PARTNER_CENTRAL_ROLE_ARN` | ⚠️ | Auto | Service role ARN |
| `PARTNER_CENTRAL_SOLUTION_ID` | ❌ | "" | Default solution ID |
| `AWS_REGION` | ✅ | us-east-1 | AWS region |
| `ENVIRONMENT` | ✅ | production | Deployment environment |
| `LOG_LEVEL` | ❌ | INFO | Logging level |

---

### Useful Links

- **HubSpot CRM API:** https://developers.hubspot.com/docs/api/crm/understanding-the-crm
- **HubSpot Webhooks:** https://developers.hubspot.com/docs/api/webhooks
- **AWS Partner Central API:** https://docs.aws.amazon.com/partner-central/latest/selling-api/Welcome.html
- **AWS SAM:** https://docs.aws.amazon.com/serverless-application-model/
- **Repository:** https://github.com/thatmikereed/hubspot-aws-partner-central-sync

---

### Contact & Support

For issues or questions about this integration:

1. **Check CloudWatch Logs** for error details
2. **Review SECURITY.md** for security best practices
3. **See README.md** for deployment instructions
4. **Open GitHub Issue** for bugs or feature requests

---

*Last Updated: 2025-02-18*
