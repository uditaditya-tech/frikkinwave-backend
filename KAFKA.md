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

## Security baseline ✅ DONE

Kafka ran for part of 2026-08-19 with a **plaintext listener and no
authentication**, on which any pod in the cluster could read every topic. That is
now closed, in the order Kafka's own security model and NIST SP 800-207 put it:
**encrypt in transit → authenticate → authorize**, with network isolation as a
second layer underneath rather than the control itself.

| layer | what | verified |
|---|---|---|
| encryption | `tls` listener on 9093; the plaintext 9092 listener is **gone** | bootstrap Service exposes only 9091 + 9093 |
| authentication | SCRAM-SHA-512 | anonymous client hangs and is killed, never served |
| authorization | `authorization.type: simple` — **denies by default** | `TopicAuthorizationException` on an ungranted op |
| network | `networkPolicyPeers` + enforcement enabled on the CNI | cross-namespace probe went **reachable → BLOCKED** |

`KafkaUser/frikkinwave-app` holds the app's identity, with Read/Write/Describe on
the 13 registry topics and Read/Describe on the `frikkinwave` group prefix.
Strimzi generates its SCRAM password into a Secret in the cluster — **never in
git, helm values, or Terraform state**, which matters because this repo is public.
Nothing uses the credential yet; stage 3 mounts it.

Proven end to end with a client pod: produce ✅, consume ✅, delete ❌ (denied).

### Why NetworkPolicy alone would NOT have worked

The intuitive fix was to enable NetworkPolicy enforcement. It would have closed
port 9091 and **left the data exposure completely open**, because Strimzi's
generated policy had four rules and the fourth was:

```
rule 3: ports=[9092] from=ALL (unrestricted)
```

Strimzi leaves a listener's rule unrestricted unless the Kafka CR sets
`networkPolicyPeers` — it has no idea who your clients are. 9092 was the port
that mattered. Enforcement without the listener work would have produced a
confident, verifiable, useless fix.

### Two silent failures found doing this

- **NetworkPolicy was not enforced at all.** `aws-eks-nodeagent` ran with
  `--enable-network-policy=false`, so every policy on the cluster was inert —
  including the two Strimzi creates for the brokers. They listed in
  `kubectl get networkpolicy` and read as protection in review. Now enabled via
  `configuration_values` on the `vpc-cni` addon in `eks.tf`.

- **A `KafkaUser` without the User Operator is inert.** Only `topicOperator` was
  enabled, so `KafkaUser` was never reconciled: the object existed,
  `kubectl get kafkauser` printed its auth type and ACLs, nothing errored, and
  **no SCRAM credential was created in Kafka and no Secret generated.** A client
  would have authenticated as a principal the broker had never heard of. Both
  operators are enabled now, and a test asserts that declaring a KafkaUser
  requires the User Operator.

### Still open

**mTLS instead of SCRAM** would be stronger (no shared secret to rotate), and the
listener already supports `authentication.type: tls`. SCRAM was chosen because
stage 3's Python client wires it with two settings rather than a certificate
lifecycle. Revisit if the app ever handles payment or identity data.

---

## Teardown: verified end to end, after three bugs

`eks-down.sh`'s Kafka path had never executed — it was written by reasoning about
how Strimzi reconciles. Running it found two more bugs the reasoning had missed,
the second of which was the worst kind: **the destroy aborted and left the stack
billing.**

**Bug 1 (fixed by reasoning, then confirmed):** deleting PVCs while the operator
lives lets it recreate them, orphaning EBS volumes past the destroy.

**Bug 2 (found by running it):** `KafkaTopic` and `KafkaUser` carry the
finalizers `strimzi.io/topic-operator` and `strimzi.io/user-operator`, and **only
the Entity Operator clears them — it dies with the Kafka resource.** Deleting
`kafka` first stranded every topic in `Terminating` forever, blocking
`helm uninstall` and, behind it, `terraform destroy`:

```
resource KafkaTopic/kafka/follow-created still exists.
status: Terminating, message: Resource scheduled for deletion
context deadline exceeded
```

Recovery is manual finalizer surgery:

```bash
kubectl patch kafkatopic <name> -n kafka --type=merge -p '{"metadata":{"finalizers":[]}}'
```

**Bug 3 (found by the FULL run, and the reason a partial test is not a test):**
`terraform destroy` re-evaluates data sources. By the time it runs, the script has
already deleted the Kafka cluster and Strimzi, so the mirrored-credential data
sources return null and the destroy aborts outright:

```
Error: Attempt to index null value
  on kafka.tf line 135, in resource "kubernetes_secret_v1" "kafka_app_user_mirror":
  "user.crt" = data.kubernetes_secret_v1.kafka_app_user.data["user.crt"]
```

The result was the worst possible outcome for a teardown script: the ALB and the
broker volumes were gone, and **the EKS control plane and RDS were still running
and billing**, with the script reporting nothing wrong. Fixed with `try(..., "")`
on each lookup so the destroy can evaluate them when the source no longer exists.

### Verified end to end

Full `eks-down.sh` run after the fixes:

```
==> Checking for orphaned resources in vpc-0598c08838cc512da
    None found.
==> Data preserved in snapshot: frikkinwave-prod-final-38cea2fd
==> Destroyed. Back to $0/hr.
```

Confirmed independently against AWS: no clusters, no RDS instance, no tagged
volumes, and the final snapshot `available`. The next `eks-up.sh` restores from it
automatically.

**The lesson worth keeping:** every one of these three bugs was invisible to
review and to partial testing. Bug 3 in particular only appears on a run that
reaches `terraform destroy` — testing the Kafka half twice, as was done earlier,
passed cleanly and proved nothing about it.

---

## Stage 3 — the switchover ✅ DONE (2026-08-19)

`_dispatch()` branches on `EVENT_TRANSPORT` (`celery` | `kafka`). **`publish()`
and the outbox are unchanged** — Kafka does not solve dual-write, so the event
row still commits with the state change and the sweep still recovers strays.

**Default is `celery`, so merging changed nothing.** Flip live, no image rebuild:

```bash
helm upgrade frikkinwave infra/helm/frikkinwave -n frikkinwave \
  --reset-then-reuse-values --set config.EVENT_TRANSPORT=kafka
```

### Verified on the live cluster

A real event through the real path, with the transport flipped:

```
event_published  topic=profile.updated
kafka_produced   topic=profile.updated
outbox_relayed   published=1
after: published_at 2026-08-19 18:28:44+00:00  attempts 1  last_error ''
```

Consumed back over TLS + SCRAM with the ACL'd user — the exact payload. Then
flipped back to `celery` and confirmed. The flag is the point: the event backbone
works today and is not worth betting on one deploy.

### The diff really is ten lines. The correctness around it is not

- **The produce MUST be synchronous.** `relay_pending()` marks an event published
  only after a successful hand-off — that is what makes delivery at-least-once.
  `confluent_kafka.produce()` returns immediately and buffers in memory, so an
  async produce would mark the row published while the message was still unsent
  and a crash would lose it *with the database claiming otherwise*. That is
  precisely the failure the outbox exists to prevent. `apps/events/kafka.py`
  produces, `flush()`es, and inspects the delivery callback — a rejected message
  is otherwise indistinguishable from a delivered one.
- **`acks=all` pairs with `min.insync.replicas=2`.** Two replicas must hold the
  message before the produce returns. `acks=1` returns once the leader has it and
  loses it if that leader dies before replicating.
- **The consumer lookup is a Celery concern only.** Under Kafka a topic with no
  registered handler is not an error — it is a topic nobody has subscribed to
  yet, which is the decoupling stage 4 is built on. Parking it would reintroduce
  exactly the producer-knows-its-consumers coupling Kafka is meant to remove. The
  Celery path still parks, and its test is unchanged.
- **Credentials go only where the relay runs** — the general worker and the relay
  CronJob. Web pods write the outbox row and nudge Celery, so they get none.
  `tests/test_architecture.py` asserts that stays true.

### Trap: a ConfigMap change does not restart anything

Flipping the flag appeared to work and did nothing. `helm upgrade` reported
success, the ConfigMap read `kafka`, and every worker still had
`EVENT_TRANSPORT=celery` in its environment.

**`envFrom` values are injected when a container starts.** Changing a ConfigMap
never updates a running pod, and an unchanged pod template produces no new
ReplicaSet to restart it. The chart now stamps
`checksum/config` on the web and worker pod templates, so a config change rolls
them. CronJobs do not need it — each run is a fresh pod.

This invalidated a claim in `CLAUDE.md`, now corrected: `SEARCH_SIMILARITY_THRESHOLD`
was documented as live-tunable by `helm upgrade --set`, which updated the
ConfigMap and changed no behaviour until something happened to restart the pods.

### Secrets cannot cross namespaces

Strimzi creates the SCRAM credential and the cluster CA in the `kafka` namespace;
the workers run in `frikkinwave`. Terraform mirrors both
(`infra/eks/kafka.tf`), and the wait before it is load-bearing:
`helm_release.kafka_cluster` completes when helm has *applied* the CRs, not when
Strimzi has reconciled them, so a data source alone fails on a fresh cluster with
"secret not found". This is a stopgap — External Secrets Operator (Phase 3) or a
replication controller is the right answer.

### ACLs are real, and the first consumer proved it

A console consumer failed with:

```
GroupAuthorizationException: Not authorized to access group: console-consumer-18221
```

The app user has `Read, Describe` on the **`frikkinwave` group prefix** only, and
the console client had invented a random group name. Working as designed —
consumers must use a group under that prefix, which is also how stage 4 gives
each extracted service its own group without an ACL change per service.

---

## mTLS ✅ DONE (2026-08-19)

The listener and `KafkaUser` moved from SCRAM-SHA-512 to **mTLS**. SCRAM worked,
but it is a shared secret: it sits in a Secret, gets mirrored across namespaces,
and rotating it means coordinating every client. Under mTLS the client proves
possession of a private key that never leaves its pod, Strimzi issues and renews
the certificate, and the principal Kafka authorizes is the certificate subject
(`CN=frikkinwave-app`) rather than a string anyone holding the Secret can replay.

**The application code did not change.** `_producer_config()` was written to emit
only the keys that are set, so this was `KAFKA_SECURITY_PROTOCOL: SSL` plus two
file paths in the chart, and the removal of every `KAFKA_SASL_*` value. That was
the point of making it settings-driven.

Verified live: a real `profile.updated` event through the outbox, produced over
mTLS, consumed back with a client certificate.

### Trap: a 0400 key is unreadable by a non-root container

The first attempt failed with:

```
ssl.certificate.location failed: error:0A080002:SSL routines::system lib
```

which says nothing about permissions. Secret volumes are owned by `root:root`,
the image runs as `appuser` (uid 10001), and `defaultMode: 0400` therefore made
the key readable only by a user this container does not have. The fix is
`defaultMode: 0440` **plus** `fsGroup: 10001` on the pod, which sets the volume's
group ownership so the container's own user can read it. Do not reach for `0444`
— that makes a private key world-readable to sidestep a group-ownership problem.

`fsGroup` must track the Dockerfile's uid; a test pins the mTLS shape.

### Trap: `--reuse-values` silently drops NEW chart defaults

The `fsGroup` fix appeared to deploy and did nothing — `helm upgrade` reported
success and the Deployment's `securityContext` stayed `{}`.

**`--reuse-values` reuses the previous release's coalesced values and does not
re-read the chart's defaults**, so a key added to `values.yaml` in the same change
is simply absent. `{{ .Values.kafka.fsGroup }}` rendered empty and Kubernetes
dropped the field.

Use **`--reset-then-reuse-values`**: reset to the chart's defaults, reapply the
release's own overrides, then merge `--set`. Every documented flip command in this
repo has been corrected. `--reuse-values` is only safe when the chart is
unchanged — which is exactly when you are least likely to be thinking about it.

### The outbox guarantee, proven in production

Both traps above caused real produce failures against the live cluster. **No event
was lost.** The relay left each one unpublished with `attempts` incrementing and
`last_error` recorded, and once the permissions were fixed it delivered them on
the next sweep — one row shows `attempts=3, published=True` with the old error
text still attached. A misconfiguration delayed delivery and never cancelled it,
which is the whole reason the outbox exists.

---

## Stage 4a — consumers ✅ DONE (application layer)

The inversion this whole migration is for:

```python
# before — a CENTRAL table the producer's relay reads
EVENT_HANDLERS = {"profile.updated": "search.index_profile"}

# after — apps/search/consumers.py, which no producer ever sees
SUBSCRIPTIONS = {"profile.updated": index_profile}
```

**The producer no longer knows who listens.** Adding a second consumer of a topic
is now a service declaring its own subscription under its own group id — no
change to the producer, no shared file, nothing to coordinate. A test asserts
the producer side never imports a `consumers` module, checked against the import
graph rather than the file text.

Four groups, one per app, matching the queue split they replace:
`notifications`, `search`, `social`, `reviews`. Group ids are
`frikkinwave.<app>`, inside the prefix the ACLs already grant — **no ACL change**.

**The runtime** is `apps/events/consumer.py`, run as
`python manage.py consume_events --group <app>`. The group name *is* the app
label, so there is no lookup table between them and therefore nothing to drift.
A misspelled group raises instead of starting, subscribing to nothing, and
looking healthy forever.

### What Celery gave free, and what replaces it

| Celery | here |
|---|---|
| `autoretry_for` / `retry_backoff` | bounded in-process retry, then a **dead-letter topic** |
| a worker runtime | `consume_events`, with SIGTERM handling and a clean `close()` |
| a stuck task blocked only itself | **a poison message blocks its whole partition** |

That last row is the one that changes the design. Under Celery a permanently
failing task was an isolated nuisance. Here, refusing to advance past a bad
message halts *everything behind it on that partition* — one malformed payload
taking out a service. So **every failure path ends in a committed offset**:

- handler raises → retry `KAFKA_CONSUMER_MAX_ATTEMPTS` times → dead-letter → commit
- malformed JSON → dead-letter immediately, **no retry** (unparseable now is
  unparseable forever; retrying only stalls the partition for nothing)
- no handler for the topic → dead-letter (a wiring mistake, not a data problem)

Dead-lettering is deliberately not a silent drop: `<topic>.dlt` carries the
original topic, the consumer group, the error and the raw payload — decoded
leniently, because the reason it is there may be that it would not decode.

The in-process retry is **bounded and small on purpose**. The partition is
stalled for its whole duration, so it covers a transient blip and nothing more.
Tiered retry topics (`.retry.5s`, `.retry.30s`) are the fuller answer and are not
built.

**Offsets are committed manually, after the handler returns.** `enable.auto.commit`
is off — auto-commit acknowledges messages the handler may never have processed,
turning at-least-once into at-most-once with nothing to show for it.

**`auto.offset.reset: earliest`**, so a new group reads history rather than
skipping everything published before it existed. Consumers are idempotent, so
replay is safe and losing history is not.

### The guardrail that matters most

`tests/test_architecture.py` now asserts **every topic with a Celery consumer also
has a Kafka subscription**. While both transports exist, a topic handled today
that nobody declares would silently stop being processed the moment
`EVENT_TRANSPORT` flips: the producer publishes happily, the message lands in its
topic, and no group ever reads it. Nothing errors.

The Celery queue tests stay until stage 5 removes Celery. What *changes* under
Kafka is the direction: a topic with **no** subscriber is no longer a bug — that
decoupling is the point — while a declared subscription nobody runs still is.

### Stage 4b — NOT DONE

Still outstanding, and it needs a live cluster:

- a Deployment per consumer group in the Helm chart, replacing the Celery workers
- a test tying declared groups to Deployments that actually run them — the exact
  Kafka analogue of the queue test, and the same silent failure
- `KafkaTopic` manifests for the `.dlt` topics
- live verification: real events through real consumer groups over mTLS

Until then the consumers exist and are tested, and nothing runs them.

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
