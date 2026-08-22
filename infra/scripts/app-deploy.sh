#!/usr/bin/env bash
#
# Deploy the application onto the EKS cluster.
#
# Usage:  ./infra/scripts/app-deploy.sh [tag]
#         tag defaults to the current git sha.
#
# Split from eks-up.sh on purpose: bringing the cluster up is a ~15 minute
# Terraform apply, while shipping a new image should be a fast, repeatable
# `helm upgrade`. Terraform owns the AWS infrastructure; Helm owns the app.
#
# Requires: eks-up.sh has run (cluster + RDS + ECR + LB controller exist),
# docker running, helm and kubectl installed, AWS credentials configured.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TF_DIR="${SCRIPT_DIR}/../eks"
CHART_DIR="${SCRIPT_DIR}/../helm/frikkinwave"
REPO_ROOT="${SCRIPT_DIR}/../.."
RELEASE="frikkinwave"

# Default the tag to the git sha: an immutable tag is what makes a rollback
# expressible ("go back to 4f2a1c9") instead of a guess.
#
# But a sha only means something if the tree is CLEAN. Deploying uncommitted
# work under a commit's sha produces an image that tag lies about — and it
# happened here: the search extraction shipped tagged with the previous
# commit's sha, so that tag names an image containing code the commit does not
# have, and re-pushing it silently replaced the image the tag used to mean.
#
# A dirty tree therefore gets a tag that is obviously not a commit, and unique
# so two dirty deploys cannot collide.
if [ -n "${1:-}" ]; then
  TAG="$1"
else
  SHA="$(git -C "${REPO_ROOT}" rev-parse --short HEAD)"
  if [ -n "$(git -C "${REPO_ROOT}" status --porcelain)" ]; then
    TAG="${SHA}-dirty.$(date +%s)"
    echo "!! Working tree is dirty. Tagging ${TAG} rather than ${SHA}," >&2
    echo "   because this image does not correspond to any commit." >&2
    echo "   Commit first if you want a rollback target you can name." >&2
  else
    TAG="${SHA}"
  fi
fi

tf() { terraform -chdir="${TF_DIR}" output -raw "$1"; }

if ! tf cluster_name >/dev/null 2>&1; then
  echo "!! No EKS stack in state. Run ./infra/scripts/eks-up.sh first." >&2
  exit 1
fi

CLUSTER="$(tf cluster_name)"
REGION="$(tf aws_region)"
NAMESPACE="$(tf app_namespace)"
REPO_URL="$(tf ecr_repository_url)"
CERT_ARN="$(tf acm_certificate_arn)"
ZONE_ID="$(tf route53_zone_id)"
API_DOMAIN="$(tf api_domain)"
# All public subnets, so the ALB spans every AZ the nodes can land in. Rendered
# as helm's {a,b,c} list literal — a bare comma-joined string would be split by
# --set into separate keys and rejected.
SUBNETS="{$(terraform -chdir="${TF_DIR}" output -json public_subnet_ids | python3 -c 'import json,sys; print(",".join(json.load(sys.stdin)))')}"
REGISTRY="${REPO_URL%/*}"
LB_NAME="$(awk '/loadBalancerName:/ {print $2; exit}' "${CHART_DIR}/values.yaml")"

echo "==> Cluster ${CLUSTER} (${REGION}), namespace ${NAMESPACE}, tag ${TAG}"

aws eks update-kubeconfig --name "${CLUSTER}" --region "${REGION}" >/dev/null

# ---------------------------------------------------------------------------
# Build and push.
#
# --platform linux/arm64 is not optional: the node group is Graviton. An amd64
# image pulls and schedules fine, then dies with "exec format error", which
# reads like an application bug.
# ---------------------------------------------------------------------------
echo "==> Logging in to ECR ${REGISTRY}"
aws ecr get-login-password --region "${REGION}" \
  | docker login --username AWS --password-stdin "${REGISTRY}"

# --platform linux/arm64 is not optional (see above).
#
# --provenance/--sbom=false: BuildKit otherwise attaches a provenance
# attestation, which makes each push THREE ECR entries — an image index, the
# real image, and the attestation. The lifecycle policy counts entries, so
# "keep the last 10 images" silently became "keep the last 3 deploys", and
# expiry could drop an untagged child manifest that a current index still
# points at. Nothing here consumes attestations.
echo "==> Building ${REPO_URL}:${TAG} (linux/arm64)"
docker build --platform linux/arm64 --provenance=false --sbom=false \
  -t "${REPO_URL}:${TAG}" "${REPO_ROOT}"

echo "==> Pushing"
docker push "${REPO_URL}:${TAG}"

# ---------------------------------------------------------------------------
# Deploy.
#
# --wait blocks until the Deployments report ready. Combined with the chart's
# pre-upgrade migration Job, a failure here means the previous version is still
# serving rather than a half-rolled-out release.
#
# The chart also runs a POST-upgrade Job that rebuilds the search index, and
# Helm waits for hooks whether or not --wait is given. That is the step which
# keeps a rebuilt stack from serving an empty search behind green health checks:
# the OpenSearch domain takes no snapshot, so it always comes back empty and
# nothing else would ever notice.
# ---------------------------------------------------------------------------
echo "==> helm upgrade --install ${RELEASE}"
helm upgrade --install "${RELEASE}" "${CHART_DIR}" \
  --namespace "${NAMESPACE}" \
  --set image.repository="${REPO_URL}" \
  --set image.tag="${TAG}" \
  --set ingress.certificateArn="${CERT_ARN}" \
  --set ingress.subnets="${SUBNETS}" \
  --wait \
  --timeout 10m

# ---------------------------------------------------------------------------
# Point DNS at the ALB the Ingress just created.
#
# The ALB is created by the load balancer controller, not Terraform, so
# Terraform cannot own this record — it does not know the ALB exists. The alias
# is upserted here instead, once the controller reports an address.
# ---------------------------------------------------------------------------
echo "==> Waiting for the ALB to be provisioned (up to 5 min)"
for _ in $(seq 1 60); do
  ALB_HOST="$(kubectl get ingress "${RELEASE}" -n "${NAMESPACE}" \
    -o jsonpath='{.status.loadBalancer.ingress[0].hostname}' 2>/dev/null || true)"
  [ -n "${ALB_HOST}" ] && break
  sleep 5
done

if [ -z "${ALB_HOST:-}" ]; then
  echo "!! The Ingress never got an address. The controller could not place an ALB." >&2
  echo "   Almost always the subnet tags (kubernetes.io/role/elb). Check:" >&2
  echo "     kubectl logs -n kube-system deploy/aws-load-balancer-controller" >&2
  exit 1
fi

echo "==> ALB: ${ALB_HOST}"

# An alias record needs the ALB's own canonical hosted zone, which is a
# per-region AWS constant and not the same as our Route 53 zone.
read -r ALB_DNS ALB_ZONE <<EOF
$(aws elbv2 describe-load-balancers --names "${LB_NAME}" --region "${REGION}" \
  --query 'LoadBalancers[0].[DNSName,CanonicalHostedZoneId]' --output text)
EOF

echo "==> Upserting ${API_DOMAIN} -> ${ALB_DNS}"
aws route53 change-resource-record-sets --hosted-zone-id "${ZONE_ID}" \
  --change-batch "$(cat <<EOF
{
  "Comment": "frikkinwave api -> EKS ALB",
  "Changes": [{
    "Action": "UPSERT",
    "ResourceRecordSet": {
      "Name": "${API_DOMAIN}",
      "Type": "A",
      "AliasTarget": {
        "HostedZoneId": "${ALB_ZONE}",
        "DNSName": "${ALB_DNS}",
        "EvaluateTargetHealth": true
      }
    }
  }]
}
EOF
)" >/dev/null

# ---------------------------------------------------------------------------
# Verify. A green helm upgrade only proves the pods are ready, not that the
# whole path — DNS, ALB, TLS, Django — actually serves a request.
# ---------------------------------------------------------------------------
echo "==> Waiting for https://${API_DOMAIN}/api/health/ (DNS + target registration, up to 5 min)"
for _ in $(seq 1 60); do
  CODE="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 \
    "https://${API_DOMAIN}/api/health/" || true)"
  [ "${CODE}" = "200" ] && break
  sleep 5
done

# ---------------------------------------------------------------------------
# A '000' above means curl never got a response, and by far the most common
# cause is NOT a broken deploy — it is this machine's DNS.
#
# Teardown deletes the Route 53 alias on purpose, so anything that resolved
# ${API_DOMAIN} while the stack was down cached a NEGATIVE answer. Home routers
# routinely hold that well past the 600s negative TTL. The site is then
# unreachable from this one network and perfectly healthy from everywhere else,
# and the deploy gets blamed for it. That happened twice on 2026-08-19/20.
#
# So before reporting failure, ask a resolver that is not ours. If the record is
# live there and the endpoint serves 200 over TLS, the deploy is fine and the
# problem is local — say so plainly instead of sending someone into kubectl.
# ---------------------------------------------------------------------------
if [ "${CODE:-}" != "200" ]; then
  PUBLIC_IP="$(dig +short @1.1.1.1 "${API_DOMAIN}" 2>/dev/null | grep -E '^[0-9]+\.' | head -1 || true)"
  if [ -n "${PUBLIC_IP}" ]; then
    # --resolve pins the address while keeping SNI and certificate validation
    # intact, so a 200 here is a genuine end-to-end success, not a shortcut.
    PUBLIC_CODE="$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 \
      --resolve "${API_DOMAIN}:443:${PUBLIC_IP}" \
      "https://${API_DOMAIN}/api/health/" || true)"
  fi
fi

echo
kubectl get pods -n "${NAMESPACE}" -o wide
echo

if [ "${CODE:-}" = "200" ]; then
  echo "==> Live: https://${API_DOMAIN}/api/health/ -> 200"
  echo "    Docs:  https://${API_DOMAIN}/api/docs/"
elif [ "${PUBLIC_CODE:-}" = "200" ]; then
  LOCAL_NS="$(scutil --dns 2>/dev/null | awk '/nameserver\[0\]/{print $3; exit}' \
    || grep -m1 '^nameserver' /etc/resolv.conf 2>/dev/null | awk '{print $2}')"
  echo "==> Live: https://${API_DOMAIN}/api/health/ -> 200 (verified via public DNS ${PUBLIC_IP})"
  echo "    Docs:  https://${API_DOMAIN}/api/docs/"
  echo
  echo "!! Your resolver (${LOCAL_NS:-unknown}) cannot resolve ${API_DOMAIN}." >&2
  echo "   THE DEPLOY IS FINE — the site is up for everyone else. Your resolver has" >&2
  echo "   a stale negative answer cached from while the stack was torn down." >&2
  echo "   Confirm:  dig +short @1.1.1.1 ${API_DOMAIN}   (answers)" >&2
  echo "             dig +short ${API_DOMAIN}            (empty)" >&2
  echo "   Fix:      restart the router, or point this machine at 1.1.1.1." >&2
else
  echo "!! https://${API_DOMAIN}/api/health/ returned '${CODE:-no response}'." >&2
  if [ -n "${PUBLIC_IP:-}" ]; then
    echo "   Public DNS resolves it to ${PUBLIC_IP} and that returned" >&2
    echo "   '${PUBLIC_CODE:-no response}', so this is NOT a local DNS problem." >&2
  else
    echo "   Public DNS (1.1.1.1) has no A record for it either — check the" >&2
    echo "   Route 53 alias, which app-deploy.sh writes just above this step." >&2
  fi
  echo "   The deploy itself succeeded — this is the edge path. Check in order:" >&2
  echo "     kubectl describe ingress ${RELEASE} -n ${NAMESPACE}" >&2
  echo "     aws elbv2 describe-target-health --region ${REGION} --target-group-arn <arn>" >&2
  echo "   A 400 here usually means ALLOWED_HOSTS; a 503 means no healthy targets." >&2
  exit 1
fi
