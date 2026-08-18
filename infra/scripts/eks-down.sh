#!/usr/bin/env bash
#
# Destroy the EKS stack. Run this whenever you stop working — the control plane
# bills $0.10/hr whether or not anything is deployed.
#
# Usage:  ./infra/scripts/eks-down.sh
#
# Why a script rather than a bare `terraform destroy`: Kubernetes controllers
# create AWS resources that Terraform does not know about (an ALB from an
# Ingress, EBS volumes from PVCs). Those survive the destroy, keep billing, and
# block VPC deletion. This deletes the Kubernetes objects that own them first,
# then destroys the stack, then reports anything left behind.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TF_DIR="${SCRIPT_DIR}/../eks"

if ! terraform -chdir="${TF_DIR}" output -raw cluster_name >/dev/null 2>&1; then
  echo "==> No EKS stack found in state. Nothing to destroy."
  exit 0
fi

CLUSTER="$(terraform -chdir="${TF_DIR}" output -raw cluster_name)"
REGION="$(terraform -chdir="${TF_DIR}" output -raw aws_region)"
VPC_ID="$(terraform -chdir="${TF_DIR}" output -raw vpc_id 2>/dev/null || echo '')"

echo "==> This destroys the EKS cluster '${CLUSTER}' in ${REGION}."
echo "    (infra/dns — Route 53 zone + ACM cert — is NOT touched.)"
read -r -p "    Type 'yes' to confirm: " CONFIRM
[ "${CONFIRM}" = "yes" ] || { echo "Aborted."; exit 1; }

# Delete controller-owned AWS resources by deleting the K8s objects that own
# them. Best-effort: the cluster may already be unreachable.
if aws eks update-kubeconfig --name "${CLUSTER}" --region "${REGION}" >/dev/null 2>&1; then
  echo "==> Removing Ingresses and Services of type LoadBalancer (they own ALBs/NLBs)"
  kubectl delete ingress --all --all-namespaces --ignore-not-found --timeout=120s || true
  kubectl delete svc --all-namespaces --field-selector spec.type=LoadBalancer \
    --ignore-not-found --timeout=120s || true
  echo "==> Removing PersistentVolumeClaims (they own EBS volumes)"
  kubectl delete pvc --all --all-namespaces --ignore-not-found --timeout=120s || true
  echo "    Waiting for AWS to release them..."
  sleep 30
fi

echo "==> terraform destroy"
terraform -chdir="${TF_DIR}" destroy -auto-approve

# Orphan check — the whole reason this script exists.
if [ -n "${VPC_ID}" ]; then
  echo "==> Checking for orphaned resources in ${VPC_ID}"
  ORPHAN_LB="$(aws elbv2 describe-load-balancers --region "${REGION}" \
    --query "LoadBalancers[?VpcId=='${VPC_ID}'].LoadBalancerName" --output text 2>/dev/null || echo '')"
  ORPHAN_VOL="$(aws ec2 describe-volumes --region "${REGION}" \
    --filters "Name=tag:kubernetes.io/cluster/${CLUSTER},Values=owned" \
    --query 'Volumes[].VolumeId' --output text 2>/dev/null || echo '')"
  if [ -n "${ORPHAN_LB}${ORPHAN_VOL}" ]; then
    echo "    !! STILL BILLING — delete these manually:"
    [ -n "${ORPHAN_LB}" ]  && echo "       load balancers: ${ORPHAN_LB}"
    [ -n "${ORPHAN_VOL}" ] && echo "       ebs volumes:    ${ORPHAN_VOL}"
    exit 1
  fi
  echo "    None found."
fi

echo "==> Destroyed. Back to \$0/hr."
