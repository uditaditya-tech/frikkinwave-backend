# Kafka migration — working plan

**Status: stages 0–2 DONE (2026-08-19). Stages 3–5 deferred.**
Kafka runs on the cluster; the application is still entirely on Celery, so
everything so far is reversible by deleting two `helm_release`s.
Written 2026-08-19 as a handoff. Read this with `MICROSERVICES.md` (why) and
`infra/eks/README.md` (the cluster it runs on).

---

## The decision

Kafka replaces **Celery *and* Redis-as-broker**, not just the broker. Every
Celery task in this codebase is an event consumer except `events.relay_outbox`,
which is the nudge mechanism itself — so there is no background-job use left
once events move. Verify before assuming otherwise:

```bash
grep -rn 'name="' --include=tasks.py apps/
```

**Redis is PARKED, not deleted.** It is broker-only today; nothing caches. It
stays running (64 Mi) reserved for the read-through cache in MICROSERVICES.md §3
(profile payloads, rating summaries). If that work never happens, delete it —
a component with no consumer is not "kept for later", it is dead weight.

### What Kafka does NOT change

Worth being clear, because it is the usual misconception: **the transactional
outbox stays exactly as it is.** Kafka does not solve dual-write — writing to
Postgres and publishing to Kafka is still two systems. `publish()` still runs
inside the producer's transaction, the row still commits with the state change,
the sweep still recovers stranded events. Idempotent consumers stay mandatory:
Kafka is at-least-once too, and its exactly-once semantics cover Kafka→Kafka
processing, not side effects like sending mail.

What actually changes is the transport and, more importantly, the **direction of
the subscription** (see stage 4).

---

## Stage 0 — EBS CSI driver ✅ DONE

**This cluster could not provision persistent storage at all.** Verified
empirically on 2026-08-19, then fixed the same day in `infra/eks/ebs-csi.tf`.

### The diagnosis was two faults, not one

The original note here blamed the dead in-tree provisioner. That was correct but
incomplete, and the incomplete half is the one you hit first. Re-running the
probe printed:

```
no persistent volumes available for this claim and no storage class is set
```

**`gp2` was not marked default** (`IsDefaultClass: No`). A PVC that names no
class therefore got no class, and the binder gave up before any provisioner was
consulted. Only *behind* that sat the second fault: `gp2`'s provisioner is
`kubernetes.io/aws-ebs`, the in-tree one removed long before 1.36, so naming it
explicitly would not have helped either.

One gp3 class, marked default, fixes both. `gp2` is left alone — it is inert,
and fighting the addon that ships it is not worth it.

### What was built

1. IRSA role scoped to `system:serviceaccount:kube-system:ebs-csi-controller-sa`
   with the AWS managed `AmazonEBSCSIDriverPolicy` (modelled on `lb-controller.tf`).
2. `aws_eks_addon` for `aws-ebs-csi-driver` with `service_account_role_arn`.
3. A **gp3** StorageClass marked default, `WaitForFirstConsumer`, encrypted,
   `allowVolumeExpansion` — a full Kafka volume is only fixable in place.

### The probe needs a consumer pod

The version of this probe originally written here applied a bare PVC. **That is
not a valid test.** Both gp2 and gp3 are `WaitForFirstConsumer` — they must be,
since EBS volumes are zonal and have to be created in whatever AZ the pod lands
in — so a PVC with no pod stays `Pending` *even when storage works perfectly*.
It would have reported failure after a successful fix.

```bash
kubectl apply -f - <<'YAML'
apiVersion: v1
kind: PersistentVolumeClaim
metadata: { name: probe-pvc, namespace: frikkinwave }
spec:
  accessModes: [ReadWriteOnce]
  resources: { requests: { storage: 1Gi } }
---
apiVersion: v1
kind: Pod
metadata: { name: probe-pod, namespace: frikkinwave }
spec:
  containers:
    - name: probe
      image: public.ecr.aws/docker/library/busybox:1.36
      command: ["sh", "-c", "sleep 600"]
      volumeMounts: [{ name: data, mountPath: /data }]
  volumes:
    - name: data
      persistentVolumeClaim: { claimName: probe-pvc }
YAML
kubectl get pvc probe-pvc -n frikkinwave    # must reach Bound, not Pending
kubectl delete pod probe-pod pvc/probe-pvc -n frikkinwave
```

Result after the fix: `Bound` to a gp3 volume, pod `Running`. Do not trust the
addon reporting ACTIVE — it did, while nothing could provision.

---

## Stage 1 — node capacity ✅ DONE

`node_instance_types` is now `["t4g.medium", "t4g.large"]` and
`node_desired_size = 3`. `node_max_size` went to 4 for rolling-replacement
headroom.

Landed as one node group replacement with the app live. `create_before_destroy`
plus the PodDisruptionBudget held the web tier through the drain;
`api.frikkinwave.com/api/health/` returned 200 throughout. Actual placement:

| node | zone | type |
|---|---|---|
| ip-10-0-0-22 | ap-south-1a | t4g.**large** |
| ip-10-0-1-36 | ap-south-1b | t4g.medium |
| ip-10-0-2-163 | ap-south-1c | t4g.medium |

**ap-south-1a came up as t4g.large** — the ASG fell back to the second type in
the list, which is exactly the scenario the list exists for. All three AZs are
now populated for the first time.

Two things learned that are not obvious:

- **Memory was not the only ceiling.** Pods-per-node is capped by ENI capacity,
  not RAM: t4g.small allows **11**, t4g.medium **17**. At 8 app pods plus
  DaemonSets across two nodes, brokers would have hit the pod cap even had the
  memory fit.
- **`node_desired_size` alone is a no-op.** The node group carries
  `ignore_changes = [scaling_config[0].desired_size]`, so raising it does
  nothing to a running group. It took effect only because `instance_types`
  changed in the same commit and forces replacement — a replacement is a
  *create*, which `ignore_changes` has no say over. Change that variable on its
  own and it silently does nothing.

> **`-target` does not isolate what you think it does.** The plan was to apply
> stage 0 alone first. `-target=aws_eks_addon.ebs_csi` pulled in
> `aws_eks_node_group.main` via its `depends_on`, and since that resource's
> config had already changed, the node replacement began too. Harmless here, but
> `-target` follows dependency edges — it does not fence off a single resource.

---

## Stage 2 — Strimzi + the Kafka cluster ✅ DONE

Infrastructure only. The application is still entirely on Celery; Kafka runs
alongside it with no producer and no consumer. Reversible by deleting the two
`helm_release`s in `infra/eks/kafka.tf`.

Live: **Strimzi 1.1.0**, **Kafka 4.2.1**, KRaft, 3 combined controller+broker
nodes, one per node and one per AZ, 10 Gi gp3 each.

### Traps paid for here

- **`kafka.strimzi.io/v1`, NOT `v1beta2`.** Strimzi 1.x *removed* v1beta2; the
  CRDs serve v1 only. Practically every Strimzi example in circulation is still
  v1beta2 and is rejected outright. Verified by reading the chart's own CRDs.
- **The same graduation moved `replicas` and `storage` off `spec.kafka`.** In v1
  they exist only on `KafkaNodePool`; `spec.kafka` requires just `listeners`.
  Node pools and KRaft are no longer opt-in.
- **A CRD and a CR of it cannot be created by one Terraform apply.**
  `kubernetes_manifest` validates against the API server at *plan* time, when
  the CRD does not exist yet. Hence a small local chart at `infra/helm/kafka/`
  installed by a second `helm_release` ordered with `depends_on` — Helm does no
  such lookup.
- **Each operator release supports a short list of Kafka versions** (1.1.0:
  4.2.0, 4.2.1, 4.3.0). Naming one outside it leaves the Kafka resource NotReady
  with the reason only in the operator log. Check before bumping:
  `helm template strimzi strimzi/strimzi-kafka-operator --version <v> | grep -A6 STRIMZI_KAFKA_IMAGES`

### One deliberate departure from the original plan

This document said "one per AZ via topology spread". What shipped is **hard
across `kubernetes.io/hostname`, soft (`ScheduleAnyway`) across zone**.

Hard-per-node is the constraint that matters: two brokers on one node means one
node failure drops two of three replicas and `min.insync.replicas: 2` can no
longer be met, so the cluster stops accepting writes. Worth failing to schedule
over.

A *hard* zone constraint is a different bet, and a bad one here — ap-south-1a
had zero t4g.small capacity when this cluster was built, which is the entire
reason `node_instance_types` is a list. It would convert a capacity shortfall
into a broker stuck `Pending` indefinitely. Soft spreads across AZs whenever it
can and degrades to running rather than not running.

### Checkpoint — passed

A `KafkaTopic` reconciled by the Topic Operator (RF 3), produced on broker-0
with `acks=all`, consumed via broker-1:

```
Topic: checkpoint-test  PartitionCount: 3  ReplicationFactor: 3  Configs: min.insync.replicas=2
        Partition: 0  Leader: 0  Replicas: 0,1,2  Isr: 0,1,2
        Partition: 1  Leader: 1  Replicas: 1,2,0  Isr: 1,2,0
        Partition: 2  Leader: 2  Replicas: 2,0,1  Isr: 2,0,1
```

`__consumer_offsets` also came up RF 3 / `min.insync.replicas=2`. **That one is
worth checking every time**: Kafka's internal topics do not inherit
`default.replication.factor` and default to 1, so a cluster can have perfectly
replicated data and lose every consumer position when the wrong broker dies.

### Guardrails

`tests/test_infrastructure.py` (13 tests) ties the chart and the Terraform stack
together, on the same principle as the queue tests in `test_architecture.py` —
each failure it catches is silent at runtime:

- the chart's `storage.class` must name a StorageClass Terraform creates
  (otherwise every broker PVC hangs `Pending` with the cause two levels down);
- no StorageClass may use a removed in-tree provisioner, and exactly one must be
  default — the two halves of the stage-0 bug;
- RF 3 / ISR 2, `RF - ISR == 1`, and internal topics replicated from the same
  value;
- brokers ≤ `node_desired_size`, since the spread is hard per node;
- manifests are `kafka.strimzi.io/v1`;
- the template actually *reads* every value the tests assert on — otherwise the
  assertions guarantee nothing;
- JVM heap stays ≥ 256 MiB below the memory limit (heap growing into the limit
  is an OOMKill and a restart loop, not an `OutOfMemoryError`).

### Teardown gap found and fixed

`eks-down.sh` deletes all PVCs cluster-wide before `terraform destroy`. With the
Strimzi operator still running it recreates the broker PVCs within seconds; the
replacement EBS volumes then outlive the destroy and keep billing — precisely
the orphan class that script exists to catch. It now deletes the `Kafka` and
`KafkaNodePool` resources and uninstalls Strimzi *before* the PVC sweep.

---

## The Kafka console (AKHQ)

There is no AWS-side view of any of this, and MSK was priced before accepting
that: **+29%/hr**, and its console shows broker health and CloudWatch metrics
but has **no topic browser, no message inspection and no consumer-lag view** —
none of the questions actually worth asking. MSK would also either be torn down
with the cluster (losing the managed benefit) or bill ~$110/month through every
session gap, which breaks the economics the whole up/down pattern depends on.

So: **AKHQ 0.28.0**, in the `kafka` namespace, on nodes already paid for.

```bash
kubectl port-forward -n kafka svc/kafka-kafka-ui 8080:8080
# http://localhost:8080/ui
```

- **ClusterIP only, and no Ingress template exists.** This is an
  unauthenticated Kafka admin console; behind the ALB it would publish read
  access to every event payload under a guessable hostname. A test asserts both.
- **Read-only** (`reader` group). Not just caution: topics are `KafkaTopic`
  manifests reconciled by the Topic Operator, and one created through the UI
  exists in Kafka with no CR behind it — invisible to git, unmanaged, and gone
  the next time the cluster is rebuilt from source.

### Four traps, all of which cost real time

- **`terraform apply` on a LOCAL chart is a silent no-op unless you bump the
  chart version.** `helm_release` diffs a local chart on its `version`, not its
  contents. Editing a template and applying reports **"0 changed"** and deploys
  nothing, which reads exactly like success. `infra/helm/kafka/Chart.yaml`
  carries a comment saying so; bump it on every template or values change.

- **AKHQ serves no `/health`.** It 404s, and setting Micronaut's
  `endpoints.health.enabled` does not change that. This is the nastiest failure
  of the four because it looks like everything works: the container logs
  `Startup completed in 10497ms. Server Running`, serves the UI correctly, and
  the kubelet kills it at exactly `failureThreshold x periodSeconds` — a clean
  boot log sitting next to a `CrashLoopBackOff`, with `exitCode 143`. Verified
  paths on the running pod: **`/ui` and `/api` are 200, `/` is 307, everything
  else 404s.** Probes use `/ui`, and a test pins that.

- **`AKHQ_CONFIGURATION` holds config *contents*, not a path.** The image's
  entrypoint writes them to `/app/application.yml`, so pointing that variable at
  a path and mounting the ConfigMap there makes the entrypoint try to overwrite
  a read-only volume: `cannot create /app/application.yml: Read-only file
  system`, before AKHQ starts at all. Use `MICRONAUT_CONFIG_FILES` and mount the
  ConfigMap as a directory instead.

- **Helm's three-way merge is ADDITIVE after a failed release.** When an upgrade
  fails, the next upgrade diffs against the last *successful* manifest. If the
  object did not exist there, helm cannot compute deletions and simply merges
  the new spec onto the live object — so the pod ended up with **both** the old
  and new env vars and **both** volume mounts, and kept failing for the original
  reason while the stored manifest looked correct. The fix is to delete the
  polluted object (`kubectl delete deployment kafka-kafka-ui -n kafka`) and let
  the next apply create it fresh. Suspect this whenever a live object disagrees
  with `helm get manifest`.

One more, self-inflicted but worth knowing: **overlapping `terraform apply` runs
fail on the state lock**, and if the output is being filtered through `grep` the
only symptom is that nothing happened. Let one apply finish before starting the
next.

### The ConfigMap checksum has to hash the config

The Deployment carries
`checksum/config: {{ include (print $.Template.BasePath "/kafka-ui-config.yaml") . | sha256sum }}`,
and the ConfigMap lives in **its own template file** so that reference works.

An earlier version hashed a few values (image, readOnly) instead. That is worse
than no checksum at all: a config-only change leaves the pod template identical,
so no new ReplicaSet rolls, the release reports success, and the running pod
keeps the old configuration. Kubernetes does not restart pods on a ConfigMap
change and AKHQ reads its config only at startup.

---

## Stage 3 — the switchover (DEFERRED, and the risky one)

`_dispatch()` in `apps/events/services.py` stops resolving a Celery task by name
and starts producing to a Kafka topic. **`publish()` and the outbox do not
change.** It is roughly a ten-line diff in one function.

Put it behind a setting (`EVENT_TRANSPORT=celery|kafka`) so it can be flipped
back. The event backbone currently works; do not bet it on one deploy.

---

## Stage 4 — consumers (DEFERRED, the bulk of the work)

The real change, and the reason this is worth doing at all:

```python
# today — a CENTRAL dispatch table, one consumer per topic
EVENT_HANDLERS = {"profile.updated": "search.index_profile"}

# after — each service declares its OWN subscription
consumer.subscribe(["profile.updated"], group_id="search")
```

The producer stops knowing who listens. Adding a second consumer to a topic
becomes deploying a service with a new `group_id`, with no change to the
producer and no shared file. That removes both the one-consumer-per-topic
ceiling and the last shared-code coupling.

**Budget for what Celery gave free:**

- `autoretry_for` / `retry_backoff` disappear. The Kafka idiom is retry topics
  (`<topic>.retry.5s`, `.30s`) plus a **dead-letter topic**. This matters more
  than it sounds: a poison message blocks its *partition*, whereas a stuck
  Celery task only blocked itself.
- A consumer runtime — process management, concurrency, graceful shutdown.
- `tests/test_architecture.py` asserts every registered handler lands on a queue
  some Deployment consumes. That test must be rewritten against consumer groups,
  not deleted; the failure it prevents (a topic nobody consumes, silently) is
  identical under Kafka.

---

## Stage 5 — remove Celery (DEFERRED)

Delete `config/celery.py`, `CELERY_*` settings, the `--queues` worker args, and
the Celery dependency. Redis stays parked per the decision above.

---

## Cost

Prices from the AWS Pricing API, ap-south-1, 2026-08-19.
The ACTUAL column is what is running after stages 0-2.

| | before | planned | **ACTUAL** |
|---|---|---|---|
| EKS control plane | 0.1000 | 0.1000 | 0.1000 |
| nodes | 0.0224 (2× t4g.small) | 0.0672 (3× t4g.medium) | **0.0896** (2× medium + 1× **large**) |
| ALB | 0.0225 | 0.0225 | 0.0225 |
| RDS db.t4g.micro | 0.0160 | 0.0160 | 0.0160 |
| public IPv4 | 0.0200 | 0.0250 | 0.0250 |
| EBS | 0.0050 | 0.0125 | 0.0113 |
| **$/hr** | **0.186** | **0.243** | **0.264** |
| **$/month** | **136** | **178** | **193** |

Instance prices re-pulled from the AWS Pricing API on 2026-08-19:
t4g.medium `$0.0224/hr`, t4g.large `$0.0448/hr` (ap-south-1).

**The actual figure is 9% above plan, and +42% over the pre-Kafka stack.** The
gap is entirely the ap-south-1a node: the ASG had to fall back to t4g.large,
which costs exactly double a medium. That is the capacity constraint documented
in stage 1 showing up as a line item rather than as an outage — a fair trade,
but it is a real 0.0224/hr that the plan did not predict.

If it matters, `node_desired_size = 2` drops this back under the planned figure
at the cost of the one-broker-per-node guarantee. Don't: that guarantee is what
`min.insync.replicas: 2` rests on.

In runway: ~807 cluster-hours become ~568. Kafka costs about **239 hours**
before the credits expire in December 2026.

> **Do not reach for MSK without pricing it.** MSK Serverless carries a base
> charge around $0.75/hr before any throughput — four times this entire stack.
> Strimzi on nodes you already pay for is dramatically cheaper at this scale.
>
> **Credit coverage is unverified.** AWS exposes no credit balance via API, and
> Cost Explorer reports $0.00 for every month including June when ECS ran, which
> is not credible as a real bill. Check Billing → Credits by hand for the
> balance, the expiry, and **which services are covered** — EC2/EBS coverage is
> what makes the Strimzi path effectively free.
