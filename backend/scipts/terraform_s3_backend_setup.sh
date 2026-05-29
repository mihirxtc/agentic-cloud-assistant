#!/usr/bin/env bash
# setup_s3_backend.sh
# One-shot setup for the Terraform S3 state backend (Strategy 3).
# Creates the S3 bucket + DynamoDB lock table, then writes the vars into backend/.env.
#
# Usage:
#   ./setup_s3_backend.sh                        # auto-names bucket from AWS account ID
#   ./setup_s3_backend.sh --bucket my-tfstate    # custom bucket name
#   ./setup_s3_backend.sh --region eu-west-1     # custom region
#   ./setup_s3_backend.sh --dry-run              # print what would happen, no changes


set -euo pipefail

# ── Colours ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
RESET='\033[0m'

info()    { echo -e "${BLUE}[INFO]${RESET}  $*"; }
success() { echo -e "${GREEN}[OK]${RESET}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${RESET}  $*"; }
error()   { echo -e "${RED}[ERROR]${RESET} $*" >&2; }
header()  { echo -e "\n${BOLD}$*${RESET}"; }

# ── Defaults ─────────────────────────────────────────────────────────────────
REGION="us-east-1"
LOCK_TABLE="terraform-state-lock"
BUCKET_NAME=""
DRY_RUN=false
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$SCRIPT_DIR/backend/.env"

# ── Argument parsing ──────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --bucket)   BUCKET_NAME="$2"; shift 2 ;;
    --region)   REGION="$2";      shift 2 ;;
    --table)    LOCK_TABLE="$2";  shift 2 ;;
    --dry-run)  DRY_RUN=true;     shift   ;;
    -h|--help)
      echo "Usage: $0 [--bucket NAME] [--region REGION] [--table TABLE] [--dry-run]"
      exit 0 ;;
    *) error "Unknown argument: $1"; exit 1 ;;
  esac
done

# ── Helpers ───────────────────────────────────────────────────────────────────
run() {
  # Wrapper: prints the command in dry-run mode, executes it otherwise.
  if $DRY_RUN; then
    echo -e "  ${YELLOW}[dry-run]${RESET} $*"
  else
    "$@"
  fi
}

# ── Prerequisites ─────────────────────────────────────────────────────────────
header "── Checking prerequisites ──────────────────────────────────────────────"

if ! command -v aws &>/dev/null; then
  error "AWS CLI not found. Install it: https://docs.aws.amazon.com/cli/latest/userguide/install-cliv2.html"
  exit 1
fi
success "AWS CLI found: $(aws --version 2>&1 | head -1)"

if ! aws sts get-caller-identity &>/dev/null; then
  error "AWS credentials not configured or invalid. Run: aws configure"
  exit 1
fi

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
AWS_USER=$(aws sts get-caller-identity --query Arn --output text)
success "Authenticated as: $AWS_USER"

if [[ ! -f "$ENV_FILE" ]]; then
  error "backend/.env not found at $ENV_FILE"
  error "Copy the example first: cp backend/.env.example backend/.env"
  exit 1
fi
success "Found backend/.env"

# ── Derive bucket name ────────────────────────────────────────────────────────
if [[ -z "$BUCKET_NAME" ]]; then
  BUCKET_NAME="aca-app-tfstate-${ACCOUNT_ID}"
fi

header "── Configuration ───────────────────────────────────────────────────────"
echo -e "  Bucket name  : ${BOLD}$BUCKET_NAME${RESET}"
echo -e "  DynamoDB table: ${BOLD}$LOCK_TABLE${RESET}"
echo -e "  Region       : ${BOLD}$REGION${RESET}"
echo -e "  .env file    : ${BOLD}$ENV_FILE${RESET}"
if $DRY_RUN; then
  warn "DRY-RUN mode — no changes will be made"
fi

# ── Confirm ───────────────────────────────────────────────────────────────────
if ! $DRY_RUN; then
  echo ""
  read -rp "Proceed? [y/N] " CONFIRM
  [[ "$CONFIRM" =~ ^[Yy]$ ]] || { info "Aborted."; exit 0; }
fi

# ── S3 Bucket ─────────────────────────────────────────────────────────────────
header "── S3 Bucket ───────────────────────────────────────────────────────────"

if aws s3api head-bucket --bucket "$BUCKET_NAME" 2>/dev/null; then
  success "Bucket already exists: s3://$BUCKET_NAME — skipping creation"
else
  info "Creating bucket: s3://$BUCKET_NAME"
  # us-east-1 does not accept a LocationConstraint — every other region does.
  if [[ "$REGION" == "us-east-1" ]]; then
    run aws s3api create-bucket \
      --bucket "$BUCKET_NAME" \
      --region "$REGION"
  else
    run aws s3api create-bucket \
      --bucket "$BUCKET_NAME" \
      --region "$REGION" \
      --create-bucket-configuration LocationConstraint="$REGION"
  fi
  success "Bucket created"
fi

info "Enabling versioning (allows state recovery)"
run aws s3api put-bucket-versioning \
  --bucket "$BUCKET_NAME" \
  --versioning-configuration Status=Enabled

info "Enabling server-side encryption (AES-256)"
run aws s3api put-bucket-encryption \
  --bucket "$BUCKET_NAME" \
  --server-side-encryption-configuration \
  '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'

info "Blocking all public access"
run aws s3api put-public-access-block \
  --bucket "$BUCKET_NAME" \
  --public-access-block-configuration \
  "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true"

success "S3 bucket configured"

# ── DynamoDB Lock Table ───────────────────────────────────────────────────────
header "── DynamoDB Lock Table ─────────────────────────────────────────────────"

TABLE_STATUS=$(aws dynamodb describe-table \
  --table-name "$LOCK_TABLE" \
  --region "$REGION" \
  --query "Table.TableStatus" \
  --output text 2>/dev/null || echo "NOT_FOUND")

if [[ "$TABLE_STATUS" == "ACTIVE" ]]; then
  success "Table already exists: $LOCK_TABLE — skipping creation"
elif [[ "$TABLE_STATUS" == "NOT_FOUND" ]]; then
  info "Creating DynamoDB table: $LOCK_TABLE"
  run aws dynamodb create-table \
    --table-name "$LOCK_TABLE" \
    --attribute-definitions AttributeName=LockID,AttributeType=S \
    --key-schema AttributeName=LockID,KeyType=HASH \
    --billing-mode PAY_PER_REQUEST \
    --region "$REGION"

  if ! $DRY_RUN; then
    info "Waiting for table to become ACTIVE..."
    aws dynamodb wait table-exists --table-name "$LOCK_TABLE" --region "$REGION"
  fi
  success "DynamoDB table created"
else
  warn "Table exists but status is: $TABLE_STATUS — proceeding anyway"
fi

# ── Write .env ────────────────────────────────────────────────────────────────
header "── Updating backend/.env ───────────────────────────────────────────────"

# Check if vars already present — avoid duplicate entries on re-runs.
update_or_append() {
  local key="$1"
  local value="$2"
  if grep -q "^${key}=" "$ENV_FILE" 2>/dev/null; then
    # Key exists — update in place with sed
    run sed -i "s|^${key}=.*|${key}=${value}|" "$ENV_FILE"
    info "Updated  ${key}=${value}"
  elif grep -q "^#\s*${key}=" "$ENV_FILE" 2>/dev/null; then
    # Key exists but commented out — uncomment and set value
    run sed -i "s|^#\s*${key}=.*|${key}=${value}|" "$ENV_FILE"
    info "Uncommented and set  ${key}=${value}"
  else
    # Key not present — append to file
    if ! $DRY_RUN; then
      echo "${key}=${value}" >> "$ENV_FILE"
    else
      echo -e "  ${YELLOW}[dry-run]${RESET} append to .env: ${key}=${value}"
    fi
    info "Appended ${key}=${value}"
  fi
}

update_or_append "TF_STATE_BUCKET"     "$BUCKET_NAME"
update_or_append "TF_STATE_LOCK_TABLE" "$LOCK_TABLE"
update_or_append "TF_STATE_REGION"     "$REGION"

success ".env updated"

# ── Verification ──────────────────────────────────────────────────────────────
if ! $DRY_RUN; then
  header "── Verifying resources ─────────────────────────────────────────────────"

  aws s3api head-bucket --bucket "$BUCKET_NAME" \
    && success "s3://$BUCKET_NAME — reachable" \
    || error   "s3://$BUCKET_NAME — NOT reachable"

  TABLE_STATUS=$(aws dynamodb describe-table \
    --table-name "$LOCK_TABLE" \
    --region "$REGION" \
    --query "Table.TableStatus" \
    --output text 2>/dev/null)
  if [[ "$TABLE_STATUS" == "ACTIVE" ]]; then
    success "DynamoDB table '$LOCK_TABLE' — ACTIVE"
  else
    error "DynamoDB table '$LOCK_TABLE' — status: $TABLE_STATUS"
  fi
fi

# ── Done ──────────────────────────────────────────────────────────────────────
header "── Done ────────────────────────────────────────────────────────────────"
echo -e "${GREEN}S3 state backend is ready.${RESET}"
echo ""
echo "  Next steps:"
echo "  1. Restart the backend to pick up the new .env vars:"
echo "     # Stop uvicorn (Ctrl+C), then re-run:"
echo "     cd backend && uvicorn main:app --reload --port 8000"
echo ""
echo "  2. Run a plan from the UI — after apply, verify state landed in S3:"
echo "     aws s3 ls s3://$BUCKET_NAME/ --recursive"
echo ""
echo "  3. Confirm no local terraform.tfstate in the workdir (it should be in S3)."
