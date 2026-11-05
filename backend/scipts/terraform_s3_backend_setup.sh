#!/usr/bin/env bash
# Kept as a compatibility pointer; infrastructure setup is an explicit operator task.
set -euo pipefail
cat <<'HELP'
The Terraform runner uses native S3 lockfiles with Terraform 1.17.1.
This legacy helper no longer creates buckets/DynamoDB tables or edits private .env files.

Configure an existing, versioned, encrypted bucket in BOTH:
  backend/.env: TF_STATE_BUCKET and TF_STATE_REGION
  backend/runner/config.json: state_backend { "bucket": "...", "region": "..." }

Grant the execution principal:
  s3:ListBucket on the selected bucket (limited to workspace prefixes)
  s3:GetObject, s3:PutObject on <workspace>/terraform.tfstate
  s3:GetObject, s3:PutObject, s3:DeleteObject on <workspace>/terraform.tfstate.tflock

A DynamoDB lock table is not used. See docs/operations.md, "Isolated Terraform runner",
for setup, volume migration, reviewed destroy and recovery instructions.
No AWS calls or filesystem changes were performed by this helper.
HELP
