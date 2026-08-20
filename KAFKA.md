# Kafka — the event backbone

**Status: COMPLETE and drilled.** Kafka is the only event transport. Celery and
Redis are gone. TLS + mTLS + deny-by-default ACLs, four consumer groups, a
dedicated relay Deployment, consumer-lag and relay alerting that pages by email,
and a measured throughput number.

Read with `MICROSERVICES.md` (why) and `infra/eks/README.md` (the cluster).

This document is ordered **current state → how to operate it → what has actually
been proven → traps → history**. The traps are the expensive part; none have been
removed.

---

## What runs today

| | |
|---|---|
| Broker | Strimzi 1.1.0 running Kafka 4.2.1, KRaft, 3 combined controller+broker nodes, one per node and one per AZ, 10 Gi gp3 each |
| Durability | RF 3 / `min.insync.replicas` 2, `acks=all`. Internal topics replicated from the same value |
| Security | TLS on 9093 (the plaintext 9092 listener is gone), mTLS client certs, `authorization: simple` — **denies by default** |
| Topics | 26 — 13 event topics plus a `.dlt` for each |
| Producer | `relay_outbox --loop`, one Deployment, single replica, `Recreate` |
| Consumers | 4 Deployments, one per app: `notifications`, `search`, `social`, `reviews` |
| Observability | kube-prometheus-stack + Strimzi `kafkaExporter`; 7 alerts; Alertmanager → SNS → email |
| Throughput | **~87 events/sec** through the relay, measured (see below) |

**Consumers are declared per app** in `apps/<app>/consumers.py` as
`SUBSCRIPTIONS: topic -> handler`. There is no central registry — the producer
must not know who listens. Group ids are `frikkinwave.<app>`; the group name *is*
the app label, so there is no lookup table to drift. A test fails the build if
the producer side imports a `consumers` module.

---

## The guarantee

**The transactional outbox is unchanged by Kafka, and that is the point.** Kafka
does not solve dual-write — writing to Postgres and publishing to Kafka is still
two systems. `publish()` runs inside the producer's transaction, so the event row
and the state change commit together or neither does.

`publish()` dispatches **nothing**. The relay Deployment polls the outbox and is
the only path to the broker. Putting a synchronous produce in the request path
would turn a broker outage into a website outage.

**Single replica by choice, not by constraint.** Concurrent relays are safe —
rows are claimed with `select_for_update(skip_locked=True)`, so two can never
double-dispatch — but there is nothing to gain when the bottleneck is the broker
round-trip, and one writer keeps a deploy readable. The cost is that **if the
relay is down nothing is delivered at all**, which is why it has an alert of its
own.

Delivery is **at-least-once** in both directions, so consumers must be
idempotent. Kafka's exactly-once semantics cover Kafka→Kafka processing, not side
effects like sending mail.

**The produce must be synchronous.** `relay_pending()` marks an event published
only after a successful hand-off. `confluent_kafka.produce()` returns immediately
and buffers in memory, so an async produce would mark the row published while the
message was still unsent — losing it *with the database claiming otherwise*,
which is precisely the failure the outbox exists to prevent. `apps/events/kafka.py`
produces, `flush()`es, and inspects the delivery callback; a rejected message is
otherwise indistinguishable from a delivered one.

**Retries are spaced.** A failed dispatch sets
`next_attempt_at = now + min(2 × 2^attempts, 600)s`, so `MAX_ATTEMPTS` spans
**~27 minutes**: `[2 4 8 16 32 64 128 256 512 600]`. Before this, retries ran at
the 1s poll interval and burned all ten attempts in about **ten seconds** — any
broker outage longer than that stranded every pending event permanently. Do not
shorten the backoff without re-checking that `OutboxNotDraining` still fires
*before* events exhaust; a test enforces that ordering.

**Every consumer failure path ends in a committed offset.** A poison message
blocks its whole *partition*, unlike a stuck Celery task which blocked only
itself:

- handler raises → bounded retry → `<topic>.dlt` → commit
- malformed JSON → dead-letter immediately, **no retry** (unparseable now is
  unparseable forever; retrying only stalls the partition)
- no handler → dead-letter (a wiring mistake, not a data problem)

Offsets are committed **manually**, after the handler returns.
`enable.auto.commit` is off — auto-commit acknowledges messages the handler may
never have processed, turning at-least-once into at-most-once for nothing.
`auto.offset.reset: earliest`, so a new group reads history rather than skipping
it.

---

## Operating it

**There is no Kafka console.** AKHQ was added and removed on 2026-08-19. Read
topics with `kafka-console-consumer.sh` via `kubectl exec`, with
`--command-config` pointing at the client cert and the cluster CA truststore.

**Grafana** — ClusterIP only, because it ships with a default admin password and
this repo is public. An Ingress here would be the AKHQ mistake with a login
screen instead of none.

```bash
kubectl port-forward -n observability svc/kube-prometheus-stack-grafana 3000:80
# admin / kubectl get secret kube-prometheus-stack-grafana -n observability \
#           -o jsonpath='{.data.admin-password}' | base64 -d
```

Dashboard **"frikkinwave — event pipeline"** (7 panels) is provisioned from a
ConfigMap in the app chart, so Grafana holds no state and needs no PVC.

**Checking the outbox by hand**, no port-forward needed:

```bash
kubectl exec -n frikkinwave deploy/frikkinwave-web -- python manage.py check_outbox_lag
```

The relay exports the same reading as Prometheus gauges — both call
`outbox_lag_snapshot()`, so the command and the alert cannot disagree.

**Adding a topic** requires all of: a `publish(topic=...)` call site, a
subscriber in some app's `consumers.py`, and **both** a `KafkaTopic` and a
`.dlt` topic in `infra/helm/kafka/values.yaml`. `authorization: simple` denies
anything ungranted, so a missing entry is a denied produce and a missing DLT
turns a dead-letter into a stalled partition. Tests enforce each of these.

---

## What has actually been proven

Run against the live cluster rather than reasoned about, because that is how
every surprise in this project has arrived.

### Failure behaviour

| drill | result |
|---|---|
| Relay force-killed, events published with none running | Restarted by k8s, all drained. No intervention. |
| One broker killed | Transparent — RF 3 / ISR 2 held. |
| **Two of three brokers killed** (quorum lost) | Produces failed, events stayed pending, relay survived, lag alert fired, everything drained on recovery. |
| Consumer group scaled to zero, 200 events published | Lag climbed to 183; lag + no-members alerts fired scoped to that group; the recovered pod consumed **exactly 200**; both alerts cleared on their own. |
| Poison message published ahead of a valid one | Retried, dead-lettered with original topic + group + error + raw payload, **and the message behind it was consumed**. |
| Relay scaled to zero (2026-08-21) | `OutboxRelayDown` fired after ~4m, cleared 37s after restore, unaided. |
| Publishing broken with the relay healthy (2026-08-21) | `OutboxEventsExhausted` fired. `OutboxNotDraining` **did not** — see below. |

**A total broker outage degrades to delayed delivery, never lost delivery** —
true since retries gained backoff. The earlier two-broker drill passed for a
narrower reason than it looked: the brokers returned inside the ten-second window
in which all ten attempts were then spent.

### The drill that found a defect

Publishing was broken while the relay stayed healthy — the exact condition
`OutboxNotDraining` was written for. It never fired.

`frikkinwave_outbox_oldest_seconds` peaked at **10.008s** against a 300s
threshold and fell to **0** as all five events exhausted inside 45 seconds. Both
of the alert's intended triggers were unreachable: a publish failure stopped
ageing the gauge after ten seconds, and a downed relay exports no gauge at all.

Fixed with the retry backoff above and re-drilled: the gauge now climbs while
retries are pending, and `OutboxNotDraining` fired at **10m45s** with the events
still at `attempts=9` of 10 — recoverable. That ordering, alert before
exhaustion, is the design property and a test pins it.

`OutboxEventsExhausted` was added beyond the original plan and was the only
signal that saw the original failure. That is why it exists.

### Alert delivery

Two alerts fired twenty minutes apart on the same topic:

```
NumberOfMessagesPublished       2   <- Alertmanager published both (IRSA works)
NumberOfNotificationsFailed     0   <- nothing errored
NumberOfNotificationsDelivered  1   <- only ONE reached a human
```

The first was published while the SNS subscription was still
`PendingConfirmation`: SNS accepted it, found no confirmed subscriber, and
dropped it. No error in Alertmanager, no failure metric, no signal anywhere.
**Publishing succeeding is not delivery** — which is why the topic now lives in
the persistent stack, so the confirmation click happens once rather than after
every teardown.

### Throughput

The old estimate was "50-200 events/sec. That is a guess."

**Measured: ~87 events/sec**, roughly **11.5 ms per event**.

| burst | drain span | rate |
|---|---|---|
| 500 events | 6.56s | 76.2/sec |
| 2,000 events | 22.99s | 87.0/sec |

The larger burst is the truer figure; the smaller still carries the first poll's
startup in its span.

**The bottleneck is the flush, not CPU.** During the burst the relay was
throttled on **0.6%** of its CFS periods — essentially never — so it was waiting
on the `acks=all` round-trip. Worth checking, because "slow" and "throttled" look
identical from outside and call for opposite fixes. **Consumers were never the
constraint**: peak `kafka_consumergroup_lag` was **0**.

~87/sec is also the **sustained** ceiling, since anything published above it
grows the backlog without bound. That is the number to design against.

*Repeating it:* publish N events to `follow.removed` in one transaction and
measure first-to-last `published_at`. That topic is the safe choice on a live
system — its handler `prune_feed` is a `DELETE` filtered on two UUIDs, so random
ids match nothing and it writes nothing, it is **not** in the notifications topic
list (no failing emails, no dead letters), and it makes no OpenAI calls.
`profile.updated` would bill you per event.

*If ~87/sec is ever not enough, batch the flush:* produce the whole batch, flush
once, then mark all published. Still correct — nothing is marked published before
its acknowledgement — and it collapses N round-trips into one. The trade-off,
honestly: a batch then fails as a unit, so one poison message delays its whole
batch rather than only itself.

---

## Traps

Each of these was paid for once. None is obvious from the outside, and most look
like success until something else breaks.

### Storage

- **A StorageClass can fail two ways at once.** `gp2` was not marked default, so
  a PVC naming no class got none and the binder gave up before any provisioner
  was consulted. *Behind* that, `gp2`'s provisioner was `kubernetes.io/aws-ebs` —
  the in-tree one removed long before 1.36. One gp3 class marked default fixes
  both. Do not trust the addon reporting ACTIVE; it did, while nothing could
  provision.
- **A bare-PVC probe is not a valid test.** Both classes are
  `WaitForFirstConsumer` — they must be, since EBS volumes are zonal — so a PVC
  with no pod stays `Pending` *even when storage works perfectly*. It reports
  failure after a successful fix. The probe needs a consumer pod.

### Nodes and capacity

- **The instance-type list must span FAMILIES, not just sizes.** A t4g-wide
  capacity shortage in ap-south-1a stalled a rebuild for 20 minutes. The ASG fell
  back to `t4g.large`, which is why one node costs double.
- **Pods-per-node is capped by ENI capacity, not RAM** — t4g.small allows 11,
  medium 17. Brokers would have hit the pod cap even had the memory fit.
- **`node_desired_size` alone is a no-op.** The node group carries
  `ignore_changes = [scaling_config[0].desired_size]`. It only took effect
  because `instance_types` changed in the same commit and forced replacement.
- **`-target` follows dependency edges.** `-target=aws_eks_addon.ebs_csi` pulled
  in the node group via `depends_on` and began replacing it. It does not fence
  off a single resource.

### Strimzi and the Kafka CR

- **`kafka.strimzi.io/v1`, NOT `v1beta2`.** Strimzi 1.x removed v1beta2. Nearly
  every example in circulation is still v1beta2 and is rejected outright.
- **The same graduation moved `replicas` and `storage` off `spec.kafka`** — in v1
  they exist only on `KafkaNodePool`.
- **A CRD and a CR of it cannot be created by one Terraform apply.**
  `kubernetes_manifest` validates against the API server at *plan* time, when the
  CRD does not exist. Hence a local chart installed by a second `helm_release`
  ordered with `depends_on` — Helm does no such lookup.
- **Each operator release supports a short list of Kafka versions.** Naming one
  outside it leaves the Kafka resource NotReady with the reason only in the
  operator log:
  `helm template strimzi strimzi/strimzi-kafka-operator --version <v> | grep -A6 STRIMZI_KAFKA_IMAGES`
- **Broker spread is hard per node, soft per zone.** Two brokers on one node means
  one node failure drops two of three replicas and `min.insync.replicas: 2` can
  no longer be met. A *hard* zone constraint is a different and worse bet — it
  converts a capacity shortfall into a broker `Pending` forever.
- **Internal topics do not inherit `default.replication.factor`.** They default to
  1, so a cluster can have perfectly replicated data and lose every consumer
  position when the wrong broker dies. Check `__consumer_offsets` every time.
- **JVM heap must stay ≥256 MiB below the memory limit.** Heap growing into the
  limit is an OOMKill and a restart loop, not an `OutOfMemoryError`.

### Security

- **NetworkPolicy alone would NOT have worked.** Strimzi's generated policy had
  four rules and the fourth was `ports=[9092] from=ALL (unrestricted)` — it
  leaves a listener unrestricted unless the Kafka CR sets `networkPolicyPeers`.
  9092 was the port that mattered. Enforcement without the listener work would
  have produced a confident, verifiable, useless fix.
- **NetworkPolicy was not enforced at all.** `aws-eks-nodeagent` ran with
  `--enable-network-policy=false`, so every policy on the cluster was inert —
  including Strimzi's own. They listed in `kubectl get networkpolicy` and read as
  protection in review. Enabled via `configuration_values` on the `vpc-cni` addon.
  It is **defence in depth, not the access control**: network location is not an
  authorization signal.
- **A `KafkaUser` without the User Operator is inert, in the worst way.** With
  only `topicOperator` enabled the object existed, `kubectl get kafkauser`
  printed its ACLs, nothing errored, and **no credential was ever created**. A
  client would have authenticated as a principal the broker had never heard of.
  A test asserts this now.
- **A 0400 key is unreadable by a non-root container**, and the error says
  nothing about permissions (`ssl.certificate.location failed: error:0A080002`).
  Secret volumes are owned by `root:root` and the image runs as uid 10001. Fix is
  `defaultMode: 0440` **plus** `fsGroup: 10001`. Do not reach for `0444` — that
  makes a private key world-readable to sidestep a group-ownership problem.
- **Strimzi-issued credentials never go in git, helm values, or Terraform state.**
  This repo is public.

### Helm and deploys

- **A ConfigMap change does not restart anything.** `envFrom` values are injected
  when a container starts, so `helm upgrade` reports success, the ConfigMap
  updates, and every running pod keeps the old environment forever. The chart
  stamps `checksum/config` on the pod templates. Don't remove it.
- **`--reuse-values` silently drops NEW chart defaults.** It reuses the previous
  release's coalesced values and does not re-read the chart, so a key added to
  `values.yaml` in the same change is simply absent and renders empty. Use
  **`--reset-then-reuse-values`**. `--reuse-values` is only safe when the chart is
  unchanged, which is exactly when you are least likely to be thinking about it.
- **Bump `infra/helm/kafka/Chart.yaml`'s version on every chart change.**
  Terraform's `helm_release` diffs a *local* chart on its version, not its
  contents — edit a template without bumping and apply reports "0 changed" and
  deploys nothing.
- **Secrets cannot cross namespaces.** Strimzi creates them in `kafka`; the app
  runs in `frikkinwave`. Terraform mirrors them, and the wait before it is
  load-bearing: `helm_release` completes when helm has *applied* the CRs, not
  when Strimzi has reconciled them.
- **A chart rendering operator CRDs must depend on the operator's release.**
  `helm_release.kafka_cluster` creates `PodMonitor`/`PrometheusRule` and failed a
  from-scratch apply with `no matches for kind "PodMonitor"`. It hid for a whole
  phase because observability was added to a cluster where Kafka already ran, so
  the CRDs happened to exist. Only a rebuild orders them wrong. A test enforces
  the dependency now.

### Teardown

- **`kafkatopic`/`kafkauser` must be deleted FIRST.** Their finalizers are cleared
  only by the Entity Operator, which dies with the Kafka resource. Delete Kafka
  first and every topic strands in `Terminating` forever, blocking
  `helm uninstall` and `terraform destroy`. Recovery is manual finalizer surgery:
  `kubectl patch kafkatopic <name> -n kafka --type=merge -p '{"metadata":{"finalizers":[]}}'`
- **Anything that reconciles PVCs must be uninstalled BEFORE the PVC sweep.** True
  of Strimzi and of the Prometheus Operator, which owns Prometheus's PVC through
  a StatefulSet volumeClaimTemplate. Deleting the PVC while it lives just
  recreates it and the volume outlives `terraform destroy`.
- **Any Terraform reference to a Strimzi-generated Secret needs `try(..., "")`.**
  `terraform destroy` re-evaluates data sources *after* the script deleted the
  Kafka cluster, so the lookup returns null and the destroy aborts with "Attempt
  to index null value" — leaving the EKS control plane and RDS billing while the
  script reports nothing wrong. That is the worst possible teardown outcome.
- **A partial test is not a test.** The teardown script's Kafka path was written
  by reasoning and ran clean twice on its Kafka half alone. The bug that left the
  stack billing only appears on a run that reaches `terraform destroy`.

### Rebuilds

- **`aws eks update-kubeconfig` FIRST.** The stack gets a new API endpoint every
  time and the context name is *identical* across rebuilds, so a stale kubeconfig
  looks correct. `eks-up.sh` fails partway with `no such host` naming the
  *previous* cluster.
- **Teardown deletes the Route 53 alias**, so `api.frikkinwave.com` may look down
  from your own network afterwards while healthy everywhere else — a cached
  negative answer, which home routers hold past its 600s TTL. Check
  `dig +short @1.1.1.1 api.frikkinwave.com` before debugging the cluster.

### Alerting

- **`up == 0` cannot detect an absent target.** Scale a Deployment to zero and the
  series disappears entirely, so the expression has nothing to evaluate.
  `OutboxRelayDown` needs `or absent(up{...})`. Absence and zero are different
  failures — the same lesson `KafkaConsumerGroupHasNoMembers` encodes.
- **A PodMonitor selecting `strimzi.io/kind: Kafka` also matches the exporter.**
  Every consumer-lag series is then scraped under both jobs and
  `sum by (consumergroup)` returns **double** the real lag, so the alert fires at
  half its configured threshold — which reads as a tuning problem, not a selector
  bug. Select on `strimzi.io/component-type`.
- **PromQL rejects `\.` inside a double-quoted string** — Go escape processing
  makes it an invalid escape, not an escaped dot: `parse error: unknown escape
  sequence U+002E '.'`. It needs `\\.`. The Operator's admission webhook catches
  this at apply time, which is the good outcome; a rule loaded any other way just
  fails silently. Worth recording how it was nearly missed: curling the four
  expressions at Prometheus passed all four, because shell quoting meant the test
  string differed from the rule string.
- **Supplying an Alertmanager `config` replaces the chart's default wholesale**,
  including its handling of `Watchdog` — which fires constantly *by design*, as a
  dead-man's switch. Route it to SNS with everything else and it emails forever.
  Black-hole it, and `InfoInhibitor` too.
- **SNS rejects a publish with an empty subject.** The subject template reads
  `.CommonLabels.alertname`, so `group_by` must guarantee one. Group by something
  else and delivery *fails* rather than arriving unlabelled.
- **An unconfirmed SNS subscription accepts publishes and delivers nothing**, and
  reports no error anywhere. `terraform apply` succeeding is not evidence that
  alerting works.

---

## How it was built

Six stages, 2026-08-19 to 2026-08-21. Kept short because the state above is what
matters; the traps each stage paid for are in the section above.

| stage | what |
|---|---|
| 0 | EBS CSI driver + default gp3 StorageClass — the cluster could not provision storage at all |
| 1 | Node capacity: 3 nodes across three AZs, instance-type list spanning families |
| 2 | Strimzi 1.1.0 + Kafka 4.2.1, KRaft, 3 brokers, RF 3 / ISR 2 |
| — | Security baseline: TLS, then SCRAM, then **mTLS**, deny-by-default ACLs, NetworkPolicy enforcement |
| 3 | `_dispatch()` produces to Kafka behind a reversible `EVENT_TRANSPORT` flag |
| 4 | Per-app `consumers.py`, consumer runtime, bounded retry + dead-letter topics, a Deployment per group |
| 5 | Celery removed — modules, settings, workers, the dependency, the flag, and Redis. Replaced by the relay Deployment |
| 6 | Observability: consumer lag, relay self-metrics, alert routing, load measurement |

**The decision.** Kafka replaced Celery *and* Redis-as-broker, not just the
broker: every Celery task here was an event consumer except the relay nudge
itself, so nothing was left for a job queue to do. It was bought for replay and
multi-consumer-per-topic, which the old central registry structurally could not
express — **not** for durability, which the outbox already provided.

**The inversion is the point.** The producer went from reading a central table
of handlers to knowing nothing about its consumers:

```python
# before — a CENTRAL table the producer's relay reads
EVENT_HANDLERS = {"profile.updated": "search.index_profile"}

# after — apps/search/consumers.py, which no producer ever sees
SUBSCRIPTIONS = {"profile.updated": index_profile}
```

**Stage 5 was not just deletion.** The nudge had to be replaced (a Deployment
polling the outbox, not an inline produce in the request path), the test suite
needed an in-process transport (`EVENT_RELAY_INLINE`, the direct descendant of
`CELERY_TASK_ALWAYS_EAGER`), and the registry had to be *replaced* rather than
removed — it was the source of truth for five guardrails. Topics are now derived
by AST-scanning `publish(topic=...)` call sites, which is strictly better because
it checks the real runtime requirement: a topic missing from the chart is a
denied produce, not a warning.

---

## Cost

Prices from the AWS Pricing API, ap-south-1, 2026-08-19.

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

**9% above plan, and +42% over the pre-Kafka stack.** The gap is entirely the
ap-south-1a node falling back to `t4g.large`, which costs exactly double a
medium — the capacity constraint from stage 1 showing up as a line item rather
than as an outage. A fair trade, but not one the plan predicted.

`node_desired_size = 2` would drop this under the planned figure. **Don't:** the
one-broker-per-node guarantee is what `min.insync.replicas: 2` rests on.

> **Do not reach for MSK without pricing it.** MSK Serverless carries a base
> charge around $0.75/hr before any throughput — four times this entire stack.
>
> **Credit coverage is unverified.** AWS exposes no credit balance via API, and
> Cost Explorer reports $0.00 for every month including June when ECS ran, which
> is not credible. Check Billing → Credits by hand for the balance, the expiry,
> and which services are covered.
