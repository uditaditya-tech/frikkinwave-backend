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

## Phase 2 — next (not built)

Goal: **`https://api.frikkinwave.com/api/health/` returns 200, served from Kubernetes.**

1. **ECR** — recreate the repo (the old one died with the ECS stack) and push the
   `linux/arm64` image.
2. **RDS** — restore from snapshot **`frikkinwave-prod-final-528299d1`**
   (2026-06-10, 20 GB). It carries the reference data, the real profiles
   (`jazzcat`, `udit94`), and the whole `demo-*` Phase 5 dataset, so every list
   endpoint paginates for a frontend. Verify the snapshot still exists first:
   `aws rds describe-db-snapshots --region ap-south-1 --snapshot-type manual`
3. **AWS Load Balancer Controller** — via IRSA + `helm_release`.
4. **Redis** — in-cluster (saves ~$13/mo vs ElastiCache, and gives KEDA an
   in-cluster broker to watch later). ElastiCache remains an option if
   durability matters more than cost.
5. **Helm chart** — web + worker Deployments, Service, Ingress → ALB reusing the
   **existing ACM cert** from the dns stack, liveness/readiness on
   `/api/health/`, ConfigMap, and migrations as a **`Job`** (never on container
   start — concurrent pods would race, same reason the ECS stack used a one-off
   task).
6. **Outbox relay** — `manage.py relay_outbox` as a **CronJob**. This is what
   makes event delivery *guaranteed*; the post-commit nudge alone can be lost.
   Nothing schedules it today, so wire it in this phase.

Later phases: IRSA + External Secrets (reads the existing SSM params) + HPA →
Helm/ArgoCD → KEDA scale-to-zero on the Celery worker + Prometheus/Grafana.

---

## Known issues

- **The budget alarm lives in this disposable stack**, so it is destroyed by
  `eks-down.sh` — exactly when it would be most useful, since orphaned resources
  bill *after* teardown. It should move to `infra/dns/` (persistent) or become
  its own tiny stack. There is a pre-existing account-level budget as a backstop.
- **`helm` CLI is not installed locally.** Terraform's helm provider does not
  need it, but you will want it to debug releases: `brew install helm`.
- **Credit expiry is unknown.** Check it — it determines how much urgency the
  EKS work carries.
