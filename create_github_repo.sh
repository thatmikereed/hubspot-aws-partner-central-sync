#!/usr/bin/env bash
# =============================================================
# create_github_repo.sh
# Creates a GitHub repository and pushes the integration code.
#
# Usage:
#   chmod +x create_github_repo.sh
#   GITHUB_TOKEN=ghp_xxx GITHUB_USERNAME=yourname ./create_github_repo.sh
#
# Or set the variables inline:
#   GITHUB_TOKEN=ghp_xxx GITHUB_USERNAME=yourname REPO_NAME=my-repo ./create_github_repo.sh
# =============================================================

set -euo pipefail

# -----------------------------------------------
# Config — override via environment variables
# -----------------------------------------------
GITHUB_TOKEN="${GITHUB_TOKEN:?Please set GITHUB_TOKEN}"
GITHUB_USERNAME="${GITHUB_USERNAME:?Please set GITHUB_USERNAME}"
REPO_NAME="${REPO_NAME:-hubspot-aws-partner-central-sync}"
REPO_DESCRIPTION="${REPO_DESCRIPTION:-Bidirectional HubSpot ↔ AWS Partner Central integration via serverless Lambda}"
REPO_PRIVATE="${REPO_PRIVATE:-true}"

# -----------------------------------------------
# Create the repository via GitHub API
# -----------------------------------------------
echo "▶ Creating GitHub repository: ${GITHUB_USERNAME}/${REPO_NAME}"

HTTP_STATUS=$(curl -s -o /tmp/gh_create_response.json -w "%{http_code}" \
  -X POST "https://api.github.com/user/repos" \
  -H "Authorization: Bearer ${GITHUB_TOKEN}" \
  -H "Accept: application/vnd.github+json" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  -d "{
    \"name\": \"${REPO_NAME}\",
    \"description\": \"${REPO_DESCRIPTION}\",
    \"private\": ${REPO_PRIVATE},
    \"auto_init\": false,
    \"has_issues\": true,
    \"has_projects\": false,
    \"has_wiki\": false
  }")

if [ "$HTTP_STATUS" -ne 201 ]; then
  echo "❌ Failed to create repo (HTTP ${HTTP_STATUS}):"
  cat /tmp/gh_create_response.json
  exit 1
fi

REPO_URL=$(python3 -c "import json,sys; d=json.load(open('/tmp/gh_create_response.json')); print(d['clone_url'])")
echo "✅ Repository created: https://github.com/${GITHUB_USERNAME}/${REPO_NAME}"

# -----------------------------------------------
# Push code via git
# -----------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

cd "${SCRIPT_DIR}"

git init -b main
git add -A
git commit -m "feat: initial HubSpot ↔ AWS Partner Central integration

- HubSpot deal.creation webhook → Partner Central CreateOpportunity (filtered by #AWS tag)
- Scheduled Lambda polls Partner Central for pending EngagementInvitations
- Automatically accepts invitations and creates HubSpot deals
- Bidirectional field mapping (mappers.py)
- IAM role HubSpotPartnerCentralServiceRole with least-privilege Partner Central permissions
- AWS SAM deployment template
- Full pytest test suite"

# Use token-authenticated HTTPS remote
REMOTE_URL="https://${GITHUB_USERNAME}:${GITHUB_TOKEN}@github.com/${GITHUB_USERNAME}/${REPO_NAME}.git"
git remote add origin "${REMOTE_URL}"
git push -u origin main

echo ""
echo "🎉 Done! Your repository is live at:"
echo "   https://github.com/${GITHUB_USERNAME}/${REPO_NAME}"
echo ""
echo "Next steps:"
echo "  1. Deploy the IAM role:   aws cloudformation deploy --template-file infra/iam-role.yaml --stack-name hubspot-pc-iam --capabilities CAPABILITY_NAMED_IAM"
echo "  2. Build & deploy:        sam build && sam deploy --guided"
echo "  3. Register the webhook URL (shown after deploy) in your HubSpot app"
