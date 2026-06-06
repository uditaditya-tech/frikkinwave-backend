#!/usr/bin/env bash
#
# Tear down the disposable app stack (VPC, ALB, ECS, RDS, ElastiCache, …).
# The persistent infra/dns stack (Route 53 zone + ACM cert) is NEVER touched.
#
# By default RDS takes a FINAL SNAPSHOT on destroy (data is retained and can be
# restored later by bring-up.sh). Pass --wipe to destroy without a snapshot.
#
# Usage:  ./infra/scripts/teardown.sh [--wipe]
# Requires: terraform state present, AWS credentials configured.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TF_DIR="${SCRIPT_DIR}/../terraform"

WIPE=false
if [ "${1:-}" = "--wipe" ]; then
  WIPE=true
fi

cd "${TF_DIR}"

if [ "${WIPE}" = true ]; then
  echo "==> Tearing down WITHOUT a final snapshot (--wipe). Data will be LOST."
  read -r -p "    Type 'wipe' to confirm: " CONFIRM
  [ "${CONFIRM}" = "wipe" ] || { echo "Aborted."; exit 1; }
  terraform destroy -var db_skip_final_snapshot=true -auto-approve
  echo "==> Destroyed (no snapshot)."
else
  SNAP="$(terraform output -raw db_final_snapshot_name 2>/dev/null || echo '<computed on destroy>')"
  echo "==> Tearing down the app stack. A final RDS snapshot will be taken:"
  echo "        ${SNAP}"
  echo "    (infra/dns — Route 53 zone + ACM cert — is NOT touched.)"
  read -r -p "    Type 'yes' to confirm destroy: " CONFIRM
  [ "${CONFIRM}" = "yes" ] || { echo "Aborted."; exit 1; }
  terraform destroy -auto-approve
  echo "==> Destroyed. Final snapshot retained: ${SNAP}"
  echo "    Bring it back with data:  ./infra/scripts/bring-up.sh <image-sha>"
fi
