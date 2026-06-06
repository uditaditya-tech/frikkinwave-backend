#!/usr/bin/env bash
#
# Bring the disposable app stack back up from nothing, in the correct order:
#   1. create ECR (so there's somewhere to push)
#   2. build + push the image
#   3. full apply (optionally restoring RDS from a snapshot)
#   4. run migrations + seed (idempotent)
#   5. verify health
#
# By default it AUTO-DETECTS the latest manual RDS snapshot for this instance and
# restores it. If none exists it creates a fresh empty DB.
#
# Usage:
#   ./infra/scripts/bring-up.sh <image-sha>                # auto-restore latest snapshot (or fresh)
#   ./infra/scripts/bring-up.sh <image-sha> --fresh        # force a fresh empty DB
#   ./infra/scripts/bring-up.sh <image-sha> <snapshot-id>  # restore a specific snapshot
#
# Requires: terraform.tfvars present (django_secret_key + openai_api_key),
# Docker running, AWS credentials configured.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TF_DIR="${SCRIPT_DIR}/../terraform"

# Defaults mirror infra/terraform/variables.tf; override via env if you change them.
PROJECT="${TF_PROJECT:-frikkinwave}"
ENVIRONMENT="${TF_ENVIRONMENT:-prod}"
REGION="${AWS_REGION:-ap-south-1}"
NAME="${PROJECT}-${ENVIRONMENT}"
DB_INSTANCE_ID="${NAME}-db"

TAG="${1:-}"
if [ -z "${TAG}" ]; then
  echo "Usage: $0 <image-sha> [--fresh | <snapshot-id>]" >&2
  exit 1
fi
MODE="${2:-auto}"

# --- Resolve which snapshot (if any) to restore -----------------------------
SNAPSHOT=""
case "${MODE}" in
  --fresh)
    echo "==> --fresh: creating an EMPTY database (ignoring snapshots)."
    ;;
  auto)
    echo "==> Detecting latest manual snapshot for ${DB_INSTANCE_ID}..."
    SNAPSHOT="$(aws rds describe-db-snapshots --region "${REGION}" \
      --db-instance-identifier "${DB_INSTANCE_ID}" \
      --snapshot-type manual \
      --query "reverse(sort_by(DBSnapshots,&SnapshotCreateTime))[0].DBSnapshotIdentifier" \
      --output text 2>/dev/null || echo "None")"
    if [ "${SNAPSHOT}" = "None" ] || [ -z "${SNAPSHOT}" ]; then
      SNAPSHOT=""
      echo "    No snapshot found — will create a FRESH empty DB."
    else
      echo "    Latest snapshot: ${SNAPSHOT}"
    fi
    ;;
  *)
    SNAPSHOT="${MODE}"
    echo "==> Restoring pinned snapshot: ${SNAPSHOT}"
    ;;
esac

if [ -n "${SNAPSHOT}" ]; then
  echo "==> Will RESTORE RDS from: ${SNAPSHOT}"
else
  echo "==> Will create a FRESH empty DB (reference seed only after migrations)."
fi
echo "==> Image to deploy: ${NAME} :${TAG}"
read -r -p "    Type 'yes' to continue: " CONFIRM
[ "${CONFIRM}" = "yes" ] || { echo "Aborted."; exit 1; }

cd "${TF_DIR}"

echo "==> [1/5] terraform init"
terraform init -input=false

echo "==> [2/5] Creating ECR repository"
terraform apply -input=false -auto-approve -target=aws_ecr_repository.app

echo "==> [3/5] Building + pushing image ${TAG}"
"${SCRIPT_DIR}/push-image.sh" "${TAG}"

echo "==> [4/5] Full apply"
if [ -n "${SNAPSHOT}" ]; then
  terraform apply -input=false -auto-approve \
    -var "image_tag=${TAG}" \
    -var "db_snapshot_identifier=${SNAPSHOT}"
else
  terraform apply -input=false -auto-approve \
    -var "image_tag=${TAG}"
fi

echo "==> [5/5] Migrations + seed"
"${SCRIPT_DIR}/run-migrations.sh"

API_URL="$(terraform output -raw api_url)"
echo "==> Done. Verifying ${API_URL}"
sleep 5
curl -s -o /dev/null -w "    health: HTTP %{http_code}\n" "${API_URL}" || true
echo "    (If DNS hasn't propagated locally, check: dig +short ${PROJECT} @8.8.8.8 or use --resolve)"
