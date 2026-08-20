#!/usr/bin/env bash
#
# Bring the EKS stack up and point kubectl at it.
#
# Usage:  ./infra/scripts/eks-up.sh
#
# Idempotent — Terraform converges, so re-run after a transient failure.
# Cluster creation takes ~10-15 min; the node group another ~3-5.
#
# COST: ~$0.16/hr while running (EKS control plane $0.10 + 2x t4g.small + EBS).
#       Run ./infra/scripts/eks-down.sh when you're done.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TF_DIR="${SCRIPT_DIR}/../eks"

# NOTE: do NOT pass this caller as console_admin_principal_arn. The cluster is
# created with bootstrap_cluster_creator_admin_permissions, so whoever runs
# terraform already has an access entry — passing it again fails with
# "ResourceInUseException: access entry already in use". That variable exists
# only for a *different* identity (e.g. if you sign into the console as root or
# another IAM user than the one holding your CLI credentials).
CALLER_ARN="$(aws sts get-caller-identity --query Arn --output text)"
echo "==> Caller (already cluster-admin via bootstrap): ${CALLER_ARN}"

# ---------------------------------------------------------------------------
# Preflight: the persistent stack must exist first.
#
# This stack discovers three things from infra/dns/ via data sources — the
# Route 53 zone, the ACM certificate, and (since the alerting move) the SNS
# topic. A missing data source fails mid-plan with a provider-level error that
# names neither the stack nor the fix, so check here and say it plainly.
#
# Asking that stack's own output rather than probing AWS: its state is local and
# git-ignored, so "the output resolves" is the precise question — has the
# persistent stack been applied, from this machine, with the current config.
# ---------------------------------------------------------------------------
DNS_DIR="${SCRIPT_DIR}/../dns"
if ! terraform -chdir="${DNS_DIR}" output -raw alert_topic_arn >/dev/null 2>&1; then
  echo "==> The persistent stack is not applied (no alert_topic_arn output)."
  echo "    It owns the Route 53 zone, the ACM cert and the SNS alert topic,"
  echo "    and this stack reads all three. Run it first — safe and idempotent:"
  echo
  echo "        terraform -chdir=${DNS_DIR} init && terraform -chdir=${DNS_DIR} apply"
  echo
  echo "    NEVER 'terraform destroy' that stack: the GoDaddy NS delegation"
  echo "    breaks, and the alert subscription would need re-confirming."
  exit 1
fi

echo "==> terraform init"
terraform -chdir="${TF_DIR}" init -input=false

echo "==> terraform apply (this is the slow part — EKS takes ~15 min)"
terraform -chdir="${TF_DIR}" apply -auto-approve

CLUSTER="$(terraform -chdir="${TF_DIR}" output -raw cluster_name)"
REGION="$(terraform -chdir="${TF_DIR}" output -raw aws_region)"

echo "==> Writing kubeconfig"
aws eks update-kubeconfig --name "${CLUSTER}" --region "${REGION}"

echo "==> Waiting for nodes to register..."
kubectl wait --for=condition=Ready nodes --all --timeout=300s

echo
kubectl get nodes -o wide
echo
kubectl get pods -A
# ---------------------------------------------------------------------------
# Is alerting actually going to reach anyone?
#
# The SNS topic lives in THIS stack, so teardown destroys it along with its
# subscriptions. Every rebuild therefore creates a fresh subscription and AWS
# emails a fresh confirmation link. Until that link is clicked the subscription
# accepts publishes and delivers NOTHING — indistinguishable from a working
# route, right up until the first real alert goes nowhere.
# ---------------------------------------------------------------------------
ALERT_TOPIC="$(terraform -chdir="${TF_DIR}" output -raw alert_topic_arn 2>/dev/null || true)"
if [[ -n "${ALERT_TOPIC}" ]]; then
  PENDING="$(aws sns list-subscriptions-by-topic \
    --topic-arn "${ALERT_TOPIC}" --region "${REGION}" \
    --query "length(Subscriptions[?SubscriptionArn=='PendingConfirmation'])" \
    --output text 2>/dev/null || echo 0)"
  echo
  if [[ "${PENDING}" != "0" ]]; then
    echo "==> ALERTS ARE NOT BEING DELIVERED YET."
    echo "    ${PENDING} SNS subscription(s) still say PendingConfirmation."
    echo "    Check your email for 'AWS Notification - Subscription Confirmation'"
    echo "    and click the link. Alerts fire into the void until you do."
  else
    echo "==> Alert email subscription confirmed."
  fi
fi

echo
echo "==> Cluster is up."
echo "    Console: $(terraform -chdir="${TF_DIR}" output -raw console_url)"
echo
echo "    Deploy the app:       ./infra/scripts/app-deploy.sh"
echo "    Tear down when done:  ./infra/scripts/eks-down.sh"
