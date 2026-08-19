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
  echo "==> Uninstalling the application release (removes its Ingress, and so its ALB)"
  helm uninstall frikkinwave -n frikkinwave --wait --timeout 5m 2>/dev/null || true

  echo "==> Removing Ingresses and Services of type LoadBalancer (they own ALBs/NLBs)"
  kubectl delete ingress --all --all-namespaces --ignore-not-found --timeout=120s || true
  kubectl delete svc --all-namespaces --field-selector spec.type=LoadBalancer \
    --ignore-not-found --timeout=120s || true
  # BEFORE the PVC sweep, and this ordering is the whole point. The Strimzi
  # operator reconciles its Kafka cluster continuously: delete a broker's PVC
  # while the operator is still running and it recreates it within seconds. The
  # replacement EBS volumes are then younger than the sweep, survive the
  # destroy, and keep billing — exactly the orphan class this script exists to
  # prevent. Remove the thing that owns them first.
  if kubectl get crd kafkas.kafka.strimzi.io >/dev/null 2>&1; then
    echo "==> Removing Kafka topics and users (finalizers need their operators ALIVE)"
    # ORDER IS LOAD-BEARING, and this half was learned the hard way.
    #
    # KafkaTopic and KafkaUser carry the finalizers strimzi.io/topic-operator and
    # strimzi.io/user-operator. Only the Entity Operator removes them — and it
    # dies with the Kafka resource. Delete Kafka first and every topic is stranded
    # in Terminating FOREVER, which then blocks `helm uninstall` and, after it,
    # `terraform destroy`:
    #
    #   resource KafkaTopic/kafka/follow-created still exists.
    #   status: Terminating, message: Resource scheduled for deletion
    #   context deadline exceeded
    #
    # Recovering from that means patching finalizers off by hand:
    #   kubectl patch kafkatopic <name> -n kafka --type=merge \
    #     -p '{"metadata":{"finalizers":[]}}'
    kubectl delete kafkatopic --all -n kafka --ignore-not-found --timeout=120s || true
    kubectl delete kafkauser --all -n kafka --ignore-not-found --timeout=120s || true

    echo "==> Removing the Kafka cluster and the Strimzi operator (they recreate PVCs)"
    kubectl delete kafka --all -n kafka --ignore-not-found --timeout=180s || true
    kubectl delete kafkanodepool --all -n kafka --ignore-not-found --timeout=120s || true
    helm uninstall strimzi -n kafka --wait --timeout 5m 2>/dev/null || true

    # Belt and braces: if anything above timed out, the operators are gone and
    # nothing will ever clear these. Strip the finalizers so the destroy proceeds
    # rather than hanging for five minutes and then failing.
    for kind in kafkatopic kafkauser; do
      for obj in $(kubectl get "${kind}" -n kafka -o name 2>/dev/null); do
        echo "    !! ${obj} outlived its operator — clearing its finalizer"
        kubectl patch "${obj}" -n kafka --type=merge \
          -p '{"metadata":{"finalizers":[]}}' >/dev/null 2>&1 || true
      done
    done
  fi

  echo "==> Removing PersistentVolumeClaims (they own EBS volumes)"
  kubectl delete pvc --all --all-namespaces --ignore-not-found --timeout=120s || true
  echo "    Waiting for AWS to release them..."
  sleep 30
fi

# The alias record would otherwise point at an ALB that no longer exists.
# Harmless (it just fails to resolve) but it makes `dig` lie about the state of
# the world, which costs ten confused minutes on the next bring-up.
API_DOMAIN="$(terraform -chdir="${TF_DIR}" output -raw api_domain 2>/dev/null || echo '')"
ZONE_ID="$(terraform -chdir="${TF_DIR}" output -raw route53_zone_id 2>/dev/null || echo '')"
if [ -n "${API_DOMAIN}" ] && [ -n "${ZONE_ID}" ]; then
  echo "==> Removing the ${API_DOMAIN} alias record"
  EXISTING="$(aws route53 list-resource-record-sets --hosted-zone-id "${ZONE_ID}" \
    --query "ResourceRecordSets[?Name=='${API_DOMAIN}.' && Type=='A']" --output json 2>/dev/null || echo '[]')"
  if [ "$(echo "${EXISTING}" | python3 -c 'import json,sys; print(len(json.load(sys.stdin)))')" != "0" ]; then
    aws route53 change-resource-record-sets --hosted-zone-id "${ZONE_ID}" \
      --change-batch "$(echo "${EXISTING}" | python3 -c '
import json, sys
rrs = json.load(sys.stdin)[0]
print(json.dumps({"Changes": [{"Action": "DELETE", "ResourceRecordSet": rrs}]}))
')" >/dev/null 2>&1 || echo "    (could not delete; remove it by hand if it lingers)"
  fi
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

# ---------------------------------------------------------------------------
# The database outlived the stack.
#
# Destroy takes a final snapshot, and the next `eks-up.sh` restores from the
# NEWEST snapshot automatically (see the aws_db_snapshot data source in
# rds.tf). Nothing to hand-edit — this is informational.
#
# It used to say "copy this id into variables.tf". Do not reintroduce that:
# snapshot_identifier is ForceNew, so editing it while a cluster is up made the
# next apply destroy and recreate the live database.
# ---------------------------------------------------------------------------
LATEST_SNAP="$(aws rds describe-db-snapshots --region "${REGION}" --snapshot-type manual \
  --db-instance-identifier "${CLUSTER}-db" \
  --query 'sort_by(DBSnapshots,&SnapshotCreateTime)[-1].DBSnapshotIdentifier' \
  --output text 2>/dev/null || echo '')"
if [ -n "${LATEST_SNAP}" ] && [ "${LATEST_SNAP}" != "None" ]; then
  echo
  echo "==> Data preserved in snapshot: ${LATEST_SNAP}"
  echo "    The next eks-up.sh restores from it automatically."
fi

echo "==> Destroyed. Back to \$0/hr."
