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
echo
echo "==> Cluster is up."
echo "    Console: $(terraform -chdir="${TF_DIR}" output -raw console_url)"
echo
echo "    Deploy the app:       ./infra/scripts/app-deploy.sh"
echo "    Tear down when done:  ./infra/scripts/eks-down.sh"
