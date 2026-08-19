# EKS stack

The Kubernetes replacement for the ECS app stack. **Phase 1 (cluster foundation)
is built and verified.** Everything here is disposable — the cluster is created
and destroyed around each working session.

> **Stack layout.** Three independent Terraform stacks:
> | Stack | Lifecycle |
> |---|---|
> | `infra/dns/` | **PERSISTENT** — Route 53 zone + ACM cert. Never destroy: it breaks the registrar's NS delegation. |
> | `infra/eks/` | **DISPOSABLE** — this stack. Destroy whenever idle. |
> | `infra/terraform/` | **LEGACY** — the ECS stack. Not applied ($0). Kept in git as a fallback and as a reference; being replaced by this one. |

---

## Daily use

```bash
./infra/scripts/eks-up.sh      # ~15 min. Creates everything, writes kubeconfig, waits for nodes.
./infra/scripts/eks-down.sh    # Destroys it. Run this when you stop working.
```

Both are idempotent — Terraform converges, so re-run after a transient failure.

**Cost while running: ~$0.16/hr** (EKS control plane $0.10 + 2× t4g.small + EBS).
Idle: **$0** (the Route 53 zone in the dns stack is ~$0.50/mo).

> Funded by AWS credits. Credits are consumed **silently** — there is no invoice
> to notice — so a cluster left running over a weekend can eat weeks of budget.
> Tear it down.

---

## Two traps already hit (do not re-learn these)

**1. Kubernetes version → 6× cost.** Once a version passes its standard-support
date, EKS silently switches to *extended support* pricing: ~$0.60/hr instead of
$0.10/hr for an identical cluster. Nothing in the console flags it. The first
draft of this stack defaulted to 1.31, which was already 9 months expired.

Check before changing `kubernetes_version`:

```bash
aws eks describe-cluster-versions --region ap-south-1 --query 'clusterVersions[].{v:clusterVersion,ends:endOfStandardSupportDate,default:defaultVersion}' --output table
```

Currently pinned to **1.36** (standard support to 2027-08-02).

**2. Do not pass your own ARN as `console_admin_principal_arn`.** The cluster is
created with `bootstrap_cluster_creator_admin_permissions`, so whoever runs
Terraform already has an access entry. Passing it again fails the apply with
`ResourceInUseException`. That variable is only for a *different* identity — e.g.
if you sign into the console as root rather than the IAM user holding your CLI
credentials.

---

## Verifying

```bash
kubectl get nodes -o wide
```

```bash
kubectl get pods -A
```

Console (the **Resources** tab lists pods/deployments):
`https://ap-south-1.console.aws.amazon.com/eks/clusters/frikkinwave-prod?region=ap-south-1`

If that tab says *"your current user or role does not have access to Kubernetes
objects"*, your console identity has no access entry — see trap 2.

---

## Why `eks-down.sh` is not just `terraform destroy`

Kubernetes controllers create AWS resources that Terraform never made and does
not track:

| K8s object | AWS resource it owns |
|---|---|
| `Ingress` | ALB + target groups (~$18/mo) |
| `Service type=LoadBalancer` | NLB |
| `PersistentVolumeClaim` | EBS volume |

A bare `terraform destroy` leaves those behind. They keep billing, and they
*block* VPC deletion — so the destroy half-fails and you believe you are at $0
when you are not. The script deletes the owning Kubernetes objects first, then
destroys, then re-queries AWS and **exits non-zero if anything survived**.

---

## Phase 1 — done ✅

VPC (2 AZs, public subnets, no NAT), EKS 1.36, managed ARM64 node group
(2× t4g.small — matches the `linux/arm64` app image), OIDC provider for IRSA,
addons (vpc-cni / coredns / kube-proxy), EKS access entry, AWS Budget alarm.

> **Subnet tags are load-bearing.** `kubernetes.io/role/elb` and
> `kubernetes.io/cluster/<name>` on the public subnets are how the AWS Load
> Balancer Controller discovers where to place an ALB. Without them it creates
> nothing and emits no useful error. Most common EKS setup mistake.

## Phase 2 — built, not yet applied ⏳

Goal: **`https://api.frikkinwave.com/api/health/` returns 200, served from Kubernetes.**

Everything is written and validated offline (`terraform validate`, `helm lint`,
`helm template`). It has **not been applied against AWS yet** — the cluster
bills from the moment it exists, so the code was finished first.

| Piece | Where | Notes |
|---|---|---|
| ECR repo | `ecr.tf` | Recreated; the old one died with the ECS stack. |
| RDS | `rds.tf` | Restored from a snapshot, in the public subnets, reachable only from the cluster SG. |
| Secrets | `secrets.tf` | Terraform writes both a Kubernetes Secret (read today) and SSM params (for Phase 3). |
| LB controller | `lb-controller.tf` | IRSA role + vendored IAM policy + `helm_release`. |
| App chart | `../helm/frikkinwave/` | web, worker, Redis, migration Job, outbox CronJob, Ingress. |
| Deploy | `../scripts/app-deploy.sh` | Build/push, `helm upgrade`, Route 53 alias, verify. |

### Flow

```bash
./infra/scripts/eks-up.sh       # ~15 min: cluster, RDS, ECR, LB controller
```

```bash
./infra/scripts/app-deploy.sh   # ~5 min: build, push, helm upgrade, DNS, verify
```

```bash
./infra/scripts/eks-down.sh     # back to $0
```

`eks-up.sh` needs `infra/eks/terraform.tfvars` (git-ignored) — copy
`terraform.tfvars.example` and set `django_secret_key`.

### Decisions worth knowing

**Terraform owns AWS; Helm owns the app.** Shipping a new image is a ~30s
`helm upgrade` rather than a `terraform apply` over the whole stack. It is also
the natural on-ramp to ArgoCD later.

**`POD_IP` had to be added to the app.** `production.py` whitelisted the
container's own IP by reading the *ECS* metadata endpoint, which does not exist
on EKS. Both the kubelet probes and the ALB health check (ip-mode targets) send
the pod IP as the `Host` header, so without this every readiness probe gets a
Django 400 and no pod ever reaches Ready. The pod IP now arrives via the
downward API; the ECS branch is kept for the legacy stack.

**Migrations are a `pre-upgrade` hook Job.** Never a container entrypoint step —
every replica would race the same migration on every rollout. As a hook it must
succeed before Helm touches the Deployments, so a failed migration leaves the
previous version serving.

**The ConfigMap is also a hook,** at a lighter weight. Helm creates *all* hooks
before *any* ordinary resource, so a plain ConfigMap would not exist yet when
the migration Job's pod starts, and a first install would hang in
`CreateContainerConfigError`.

**Redis is in-cluster with no persistence.** Saves ~$13/mo against ElastiCache,
and losing the queue is survivable *because of the transactional outbox*: events
commit to Postgres with the state change, and the relay CronJob re-dispatches
anything unpublished. Redis holds work in flight, not the record of it.

**The outbox relay CronJob is new here.** Nothing scheduled `relay_outbox`
before, so "guaranteed delivery" was theoretical — only the best-effort
post-commit nudge ever ran.

**Route 53 is upserted by the script, not Terraform.** The ALB is created by the
load balancer controller, so Terraform does not know it exists and cannot own an
alias to it. `eks-down.sh` removes the record.

### Snapshot rotation trap

`eks-down.sh` takes a **final snapshot with a fresh random suffix**, but
`db_snapshot_identifier` in `variables.tf` still names the previous one. Apply
again without updating it and RDS restores the *older* snapshot — the session's
data is not lost, it just silently is not what comes back. The down script
prints the new snapshot id at the end; move it into `variables.tf` to keep it.

## Phase 3 — next

IRSA + External Secrets (syncs the SSM params written in Phase 2) + HPA →
Helm/ArgoCD → KEDA scale-to-zero on the Celery worker + Prometheus/Grafana.

---

## Known issues

- **The budget alarm lives in this disposable stack**, so it is destroyed by
  `eks-down.sh` — exactly when it would be most useful, since orphaned resources
  bill *after* teardown. It should move to `infra/dns/` (persistent) or become
  its own tiny stack. There is a pre-existing account-level budget as a backstop.
- **`.terraform.lock.hcl` is git-ignored for this stack** (`.gitignore` lines
  68-69), which contradicts the comment above it saying lock files are committed
  for reproducible provider versions. The other two stacks commit theirs. Worth
  reconciling — it matters more now that this stack pins four providers.
- **Credit expiry is unknown.** Check it — it determines how much urgency the
  EKS work carries.
