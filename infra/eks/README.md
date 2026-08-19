# EKS stack

The application stack. **Phase 1 (cluster foundation) is built and verified.** Everything here is disposable — the cluster is created
and destroyed around each working session.

> **Stack layout.** Two independent Terraform stacks:
> | Stack | Lifecycle |
> |---|---|
> | `infra/dns/` | **PERSISTENT** — Route 53 zone + ACM cert. Never destroy: it breaks the registrar's NS delegation. |
> | `infra/eks/` | **DISPOSABLE** — this stack. Destroy whenever idle. |
>
> The ECS stack that preceded this one was deleted once EKS was verified; it is
> in git history if it is ever needed.

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

## Phase 2 — done ✅

Goal: **`https://api.frikkinwave.com/api/health/` returns 200, served from Kubernetes.**

Applied and verified 2026-08-19: `https://api.frikkinwave.com/api/health/`
returned `{"status": "ok"}` over TLS from two Kubernetes pods behind an
ALB, with the demo dataset paginating and the event backbone delivering.

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

### What was verified on the live cluster

| Check | Result |
|---|---|
| `/api/health/` over TLS | `{"status": "ok"}`, 200, cert `CN=api.frikkinwave.com` |
| HTTP → HTTPS | 301 at the ALB, before Django |
| ALB target health | both pod IPs `healthy` (so the `POD_IP` fix works) |
| Migration Job | `Complete` in 17s, before the Deployments rolled |
| Demo data | profiles/followers/following all paginate (`has_next: true`) |
| Rating rollup | profile embed and reviews endpoint agree (4.33 / 24) |
| List feed | carries no `rating` key — the N+1 guard holds |
| Outbox end-to-end | `publish()` → nudge → relay → worker ran the consumer, `published_at` set, 1 attempt, no error |
| Relay CronJob | fires on schedule, `Complete` |
| Worker | connected to `redis://frikkinwave-redis:6379/0`, ready |

### Trap: a restored snapshot can predate a denormalization

The restore snapshot is from 2026-06-10, *before* the rating rollup was
denormalized onto the profile. `migrate` added `rating_avg` / `rating_count`
with empty defaults and — correctly — did not invent data for them. The result
is a database that is fully migrated and quietly wrong: every profile reported
`{"average_rating": null, "count": 0}` while `/api/reviews/<user>/summary/`,
which reads the source table, returned 4.33 over 24 reviews.

Nothing in the deploy path runs the backfill, and nothing fails. Only comparing
the two endpoints reveals it:

```bash
kubectl exec -n frikkinwave deploy/frikkinwave-web -- python manage.py backfill_profile_ratings
```

**Run this after any restore from a snapshot older than the denormalization.**
The general rule: restoring an old snapshot forward through migrations gives you
the right *schema*, never the derived *data*.

### Trap: negative DNS caching makes a good deploy look broken

`app-deploy.sh` creates the Route 53 alias and then polls the public URL. The
name did not exist a moment earlier, so a resolver that was asked for it during
the deploy has cached the NXDOMAIN — for up to the zone's SOA minimum (900s).
`dig` against 8.8.8.8 succeeds while `curl` still fails with "could not
resolve", which reads like a DNS misconfiguration and is not one.

Confirm the stack is actually fine without waiting:

```bash
curl --resolve api.frikkinwave.com:443:<alb-ip> https://api.frikkinwave.com/api/health/
```

That keeps real SNI and certificate validation and only bypasses the lookup.

### Making the cluster genuinely multi-AZ (five traps in one change)

The first build looked multi-AZ and was not: both nodes sat in `ap-south-1b`,
and both CoreDNS replicas sat on one node. Fixing it surfaced five distinct
failures worth recording.

**1. The AZ was out of capacity — the config was fine.** The ASG had been
retrying a rebalance into `1a` every four minutes for half an hour:

```
InsufficientInstanceCapacity — We currently do not have sufficient t4g.small
capacity in the Availability Zone you requested (ap-south-1a).
```

Nothing surfaces this in the EKS console; the node group just reports healthy
with a lopsided distribution. Check it directly:

```bash
aws autoscaling describe-scaling-activities --auto-scaling-group-name <asg> --region ap-south-1 --max-items 5
```

The fix is not scheduler configuration. It is **more placement options**: a
third subnet (`1c`, which AWS explicitly named as having capacity) and
`node_instance_types` as a *list* so the ASG can fall back to another size.
A single-type node group is stranded whenever that one type is exhausted.

**2. A cluster's AZ set is immutable.** Adding the `1c` subnet to the existing
cluster's `vpc_config` is rejected — `InvalidParameterException: ... should
belong to the exact set of AZs ... in which subnets were provided during cluster
creation`. Changing control-plane AZs means recreating the cluster. Node groups
have no such restriction, so nodes span all three subnets while the control
plane keeps its original two; `ignore_changes` on `vpc_config[0].subnet_ids`
stops Terraform attempting a diff the API can never satisfy.

**3. `topologySpreadConstraints` needs `matchLabelKeys`.** Spread is evaluated
only at scheduling time and counts *every* matching pod. During a rolling update
the old revision's pods shape the skew for the new ones, so both new replicas
land legally on one node — and when the old pods disappear nothing rebalances,
because Kubernetes never relocates a running pod. `matchLabelKeys:
[pod-template-hash]` scopes the calculation to the pod's own revision. Without
it the constraint quietly means something other than it appears to.

**4. ALB subnet discovery is a one-time snapshot.** The load balancer controller
picks subnets when it first places the ALB. Add an AZ later and the ALB keeps
its original span; a pod scheduled into the new zone registers as an **`unused`**
target — reachable by nothing, while the pod, the Ingress and the Deployment all
report perfectly healthy. Half the web tier served no traffic and nothing looked
wrong. The Ingress now names its subnets explicitly
(`alb.ingress.kubernetes.io/subnets`, fed from the Terraform output) so the set
is a declared value the controller reconciles.

**5. `helm --set` splits on commas.** `--set ingress.subnets=a,b,c` fails with
`key "b" has no value`. Passing a list literal (`{a,b,c}`) and joining in the
template is cleaner than backslash-escaping at every call site.

Also added alongside these: a **PodDisruptionBudget** (`minAvailable: 1`) so a
node drain cannot evict both web pods at once, and `create_before_destroy` +
`node_group_name_prefix` on the node group — changing `subnet_ids` forces
replacement, and the default destroy-then-create would take the whole cluster
down. With both in place the node group was replaced with **zero downtime**;
`/api/health/` returned 200 throughout.

Verify the end state:

```bash
kubectl get nodes -o custom-columns='NODE:.metadata.name,AZ:.metadata.labels.topology\.kubernetes\.io/zone'
```

### Snapshots: restore is automatic, and the id must never be hand-edited

Teardown takes a final snapshot with a fresh random suffix; the next `eks-up.sh`
restores from the **newest snapshot for this instance**, discovered by an
`aws_db_snapshot` data source. There is nothing to copy across.

The previous design pinned the id in `variables.tf` and asked you to update it
after each teardown. That was not merely fragile — it was **destructive**.
`snapshot_identifier` is `ForceNew` in the AWS provider, so editing it while a
cluster is running does not re-restore anything; it destroys and recreates the
database. Measured against the live instance:

```
# aws_db_instance.main must be replaced
~ snapshot_identifier = "...-528299d1" -> "...-0f41e8b8" # forces replacement
```

`lifecycle.ignore_changes = [snapshot_identifier]` now blocks that outright —
the same plan reports *No changes*. This is also semantically right: RDS reads
the snapshot only when creating the instance, so the value is meaningless
afterwards. To deliberately restore a different point in time, destroy the
instance first, then set `db_snapshot_identifier`.

For a genuinely empty database — including the first apply in a fresh AWS
account, where no snapshot exists and the lookup would fail — set
`db_restore_from_latest_snapshot = false`.

## Kafka stages 0-2 — done ✅ (2026-08-19)

Strimzi 1.1.0 + Kafka 4.2.1, 3 KRaft combined controller+broker nodes, one per
node and one per AZ, 10 Gi gp3 each. **The application is still entirely on
Celery** — Kafka runs alongside with no producer and no consumer. Full detail
and the deferred stages are in `KAFKA.md`; the cluster-level traps are here.

### Trap: this cluster could not provision ANY persistent storage

Two separate faults stacked, and the usual diagnosis catches only the second.

1. **No default StorageClass.** `gp2` shipped with `IsDefaultClass: No`, so a
   PVC naming no class got no class at all. The binder reported
   `no persistent volumes available for this claim and no storage class is set`
   and never consulted a provisioner.
2. **`gp2`'s provisioner is `kubernetes.io/aws-ebs`** — the in-tree one, removed
   from Kubernetes long before 1.36. So naming it explicitly would not have
   helped either.

`infra/eks/ebs-csi.tf` installs the EBS CSI driver (IRSA + addon) and a **gp3**
class marked default, fixing both. `gp2` is left alone; it is inert.

**Verify with a PVC that has a consumer pod.** Both classes are
`WaitForFirstConsumer` — they must be, since EBS volumes are zonal and have to
be created in the AZ the pod lands in — so a pod-less PVC stays `Pending` *even
when everything works*. A bare PVC reports failure after a successful fix. And
do not trust `aws eks list-addons` reporting ACTIVE: it did, while nothing could
provision. The working probe is in `KAFKA.md` stage 0.

### Trap: `-target` does not fence off one resource

The intent was to apply the storage fix alone before touching nodes.
`-target=aws_eks_addon.ebs_csi` pulled `aws_eks_node_group.main` in through its
`depends_on`, and because that resource's config had already changed, the node
group replacement started too. `-target` follows dependency edges. If you want
true isolation, stage the *config* changes, not the apply.

### Trap: `node_desired_size` on its own is a no-op

`aws_eks_node_group.main` carries `ignore_changes = [scaling_config[0].desired_size]`
so the autoscaler is not fought on every apply. The consequence is that raising
`node_desired_size` does **nothing** to a running group. It worked here only
because `instance_types` changed in the same commit and forces replacement — and
a replacement is a *create*, which `ignore_changes` has no say over. Change that
variable alone and it silently does nothing.

### Node capacity: memory was not the only ceiling

Nodes went from 2× t4g.small to 3× (`["t4g.medium", "t4g.large"]`). Pods-per-node
is capped by **ENI capacity, not RAM**: t4g.small allows 11, t4g.medium 17. At 8
app pods plus DaemonSets on two nodes, brokers would have hit the pod cap even
if the memory had fit.

**ap-south-1a came up as a t4g.large** — the ASG fell back to the second type in
the list, which is exactly why that variable is a list (1a had zero t4g.small
capacity when this cluster was built). All three AZs are populated for the first
time. It also costs double a medium: actual spend is **$0.264/hr**, not the
$0.243 the plan predicted. See the cost table in `KAFKA.md`.

The replacement ran with the app live — `create_before_destroy` plus the PDB held
the web tier through the drain and `/api/health/` returned 200 throughout.

### Terraform cannot create a CRD and a CR of it in one apply

`kubernetes_manifest` validates against the API server's schema at **plan** time,
when the CRD does not yet exist. So the Kafka resources live in a small local
chart (`infra/helm/kafka/`) installed by a second `helm_release` ordered with
`depends_on` — Helm does no such lookup. Also note Strimzi 1.x serves **only**
`kafka.strimzi.io/v1`; `v1beta2`, which nearly every example online still uses,
was removed.

### `eks-down.sh` had a Kafka-shaped hole

It deleted all PVCs cluster-wide before `terraform destroy`. With the Strimzi
operator still running, it recreates the broker PVCs within seconds — and those
EBS volumes then outlive the destroy and keep billing, which is the exact orphan
class the script exists to prevent. It now removes the `Kafka` and
`KafkaNodePool` resources and uninstalls Strimzi *before* the PVC sweep.

### ⚠ NetworkPolicy is not enforced, and Kafka has no auth

`aws-eks-nodeagent` runs with `--enable-network-policy=false`, so **every
NetworkPolicy on this cluster is decorative** — including the two Strimzi creates
to protect the Kafka broker ports. They appear in `kubectl get networkpolicy` and
enforce nothing.

Verified from a throwaway pod in the `default` namespace: broker port 9091 was
reachable despite Strimzi's policy naming only its own components, and event
payloads (recipient emails, message bodies) were readable in full. Kafka's
listener is plaintext with no authentication, so any pod can be a client.

Nothing here is reachable from the internet — no Ingress, no LoadBalancer, and a
test in `tests/test_infrastructure.py` keeps it that way. This is lateral,
in-cluster exposure on a single-tenant cluster torn down between sessions.

Two fixes, neither done (see `KAFKA.md` for detail):

1. Set `enableNetworkPolicy` on the `vpc-cni` addon — one `configuration_values`
   change that activates Strimzi's dormant policies. Apply deliberately: they
   have never been in effect, so it is a real behaviour change.
2. A SCRAM-SHA-512 listener with `KafkaUser` ACLs. **Do this with stage 3**, when
   the app becomes a producer and needs credentials anyway.

A Kafka console (AKHQ) was deployed and removed the same day — it had no auth,
and removing it did not close any of the above. Use `kafka-console-consumer.sh`
via `kubectl exec` instead.

---

## Phase 3 — next

IRSA + External Secrets (syncs the SSM params written in Phase 2) + HPA →
Helm/ArgoCD → KEDA scale-to-zero on the Celery worker + Prometheus/Grafana.

---

## Open items (carried across sessions)

Ordered by consequence, not effort.

- **`KAFKA.md` stages 3-5** — the switchover, the consumers, and removing
  Celery. Stages 0-2 are done (storage, nodes, Kafka itself); stage 3 is the
  first one that touches application code and the first that is not trivially
  reversible.
- **The budget alarm lives in this disposable stack**, so `eks-down.sh` destroys
  it exactly when it would be most useful, since orphaned resources bill *after*
  teardown. Should move to `infra/dns/` or its own tiny stack. Matters more than
  it looks: credits expire December 2026 and one forgotten month of uptime is
  ~96% of the budget.
- **Notification emails have never been delivered.** `EMAIL_HOST_USER` /
  `EMAIL_HOST_PASSWORD` are unset everywhere, so every send fails with
  `SMTPSenderRefused(550, 'Unauthenticated senders not allowed')` and retries
  three times. The notifications service is correct; it has simply never
  succeeded. Plumbing is a Terraform variable → SSM → Secret; the SendGrid key
  goes in `terraform.tfvars`.
- **Dev tooling ships to production.** `requirements/base.txt` is a single file
  containing pytest, mypy, ruff, pre-commit, faker and the stubs, and the
  Dockerfile installs it into the runtime image — 47.5 MB of test and lint
  tooling in every pod. The size is secondary; the attack surface is the point.
  Split into `base.txt` + `dev.txt` (touches Dockerfile, CI, setup docs).
- **Three redundant RDS snapshots** from June. Nothing references a specific id
  any more (restore is discovered), so they are safe to delete — but deletion is
  irreversible and has never been explicitly approved.
- **AWS credit balance and coverage are unverified.** No API exposes them, and
  Cost Explorer reports $0.00 for every month including June when ECS ran, which
  is not credible. Check Billing → Credits by hand.
- **The `18dca52` image was deleted from ECR** because it was mislabelled — built
  from a dirty tree, so it contained code no commit had. Consequence:
  `helm rollback frikkinwave 5` will fail with ImagePullBackOff. Revisions 1-4
  and 6 are fine.

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
