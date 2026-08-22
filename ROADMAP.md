# Roadmap — frikkinwave backend

Each phase is independently shippable. "Stop here" = the product is usable at this point.

---

## Phase 0 — Scaffold
**Status: ✅ Complete**
CI green, dev stack running, zero feature code. Frontend deferred until backend is release-ready.

| Sub-step | Status |
|---|---|
| 0.1 Django project skeleton (config as project dir) | ✅ |
| 0.2 Settings split: base / local / production + django-environ | ✅ |
| 0.3 Custom User model (UUIDv7, email login, username slug) + initial migration | ✅ |
| 0.4 ruff + mypy strict + pre-commit hooks | ✅ |
| 0.5 GitHub Actions CI (lint, type-check, migrate, pytest) | ✅ |
| 0.6 docker-compose (Postgres 16 + Redis 7) — *Redis since removed with Celery* | ✅ |
| 0.7 drf-spectacular wired — /api/schema/ returns valid OpenAPI doc | ✅ |
| 0.8 ~~Frontend~~ — out of scope for this repo | N/A |
| 0.9 ~~Frontend CI~~ — out of scope for this repo | N/A |

---

## Phase 1 — Musician profiles + jam partner discovery
**Status: ✅ Complete**
Shipped: live at https://api.frikkinwave.com (EKS + ALB + RDS, ap-south-1)

| Sub-step | Status |
|---|---|
| 1.1 Auth endpoints — register, login, refresh, logout + tests | ✅ |
| 1.2 MusicianProfile model (bio, city, country, availability) + migration | ✅ |
| 1.3 Instrument + Genre models + seed data (management command) | ✅ |
| 1.4 Profile create / update / retrieve endpoints + tests | ✅ |
| 1.5 Browse + filter profiles (city, country, instrument, genre) + tests | ✅ |
| 1.6 Public profile view (unauthenticated) + tests | ✅ |
| 1.7 ContactRequest flow (send → accept/decline → reveal) + tests | ✅ (email → Phase 2 w/ Celery) |
| 1.8 Dockerfile (multi-stage, collectstatic baked in) | ✅ |
| 1.9 ECR repo + push script; ECS task definition + Fargate service + ALB | ✅ |
| 1.10 RDS Postgres + secrets in SSM/Secrets Manager | ✅ |
| 1.11 DNS: api.frikkinwave.com → ALB + HTTPS (ACM, Route 53 delegation) | ✅ |

---

## Phase 2 — AI-powered matching
**Status: ✅ Complete — then SUPERSEDED (2026-08-23). See Phase 2R below.**
Shipped: deployed & verified live at https://api.frikkinwave.com (web + Celery worker + ElastiCache + pgvector, ap-south-1)

> Everything in this table was built, deployed and run against the real models in
> production. It is left marked complete because it was, and because the ticks
> record what was learned — the measured similarity floor, the async-on-write vs
> sync-on-read split, the degradation contract. **None of it is running now.**
> Phase 2R replaced the retrieval half with OpenSearch and deleted the rest.

| Sub-step | Status |
|---|---|
| 2.1 Celery app + Redis broker wired (settings, eager-in-tests, debug task) | ✅ |
| 2.2 Contact-request email notifications as Celery tasks (deferred from 1.7) | ✅ |
| 2.3 pgvector extension + `ProfileEmbedding` model + migration (HNSW cosine index) | ✅ |
| 2.4 Embedding pipeline: profile save → event → Celery task → OpenAI text-embedding-3-small → pgvector store | ✅ |
| 2.5 Semantic search endpoint: natural language query → embedding → nearest-neighbor retrieval | ✅ |
| 2.6 Compatibility blurb: gpt-4o-mini "Why you might click" per profile pair, cached (`CompatibilityBlurb`) | ✅ |
| 2.7 Profile coach: completeness score + field suggestions (rules) + LLM tip on profile setup | ✅ |
| 2.8 Evals: retrieval quality (recall@k, MRR) + blurb grounding — metrics + golden set + `eval_matching` command + deterministic CI harness | ✅ |
| 2.9 Infra: ElastiCache Redis + Celery worker task def + `OPENAI_API_KEY` secret — **deployed & verified live in prod** (real end-to-end semantic search via OpenAI). CD decision: **staying manual** (recorded in infra/README). `terraform destroy` takes a final RDS snapshot by default. | ✅ |

---

## Phase 2R — Search without the AI
**Status: ✅ Complete (2026-08-23)** — code and infrastructure done; **not yet deployed**,
the stack has been torn down since 2026-08-21.

Phase 2 was replaced rather than extended. The embeddings were doing retrieval,
and BM25 does retrieval without a model, a key, a per-request cost or a vector
column. The blurbs and the coach tip went with them because they were the rest of
the same dependency, not because they were failing.

| Sub-step | Status |
|---|---|
| 2R.1 OpenSearch client seam (`apps/search/client.py`), one module importing the SDK, domain `SearchUnavailableError` | ✅ |
| 2R.2 Search swapped to OpenSearch: structured fields instead of one blended string, BM25 `score` replaces `similarity`, `SEARCH_SIMILARITY_THRESHOLD` deleted | ✅ |
| 2R.3 `ProfileEmbedding`, the `vector` extension and pgvector dropped; historical migrations rewritten to stand alone | ✅ |
| 2R.4 `apps/ai`, the compatibility endpoint + table, and the coach's LLM `tip` removed; `openai` and 13 orphaned transitives dropped from the image | ✅ |
| 2R.5 `reindex_profiles` + `--prune` (watermark sweep), which is also how deletions leave the index | ✅ |
| 2R.6 Terraform: managed OpenSearch domain, VPC-only, FGAC; `OPENAI_API_KEY` removed from the stack | ✅ |
| 2R.7 Rebuild wired in as a post-upgrade Helm hook — the index takes no snapshot, so a fresh stack would otherwise serve an empty search behind green probes | ✅ |
| 2R.8 Docs sync | ✅ |

**Known gaps, honestly:**
- **Nothing has been deployed.** The domain has never been created; `terraform validate`
  passes but the first apply is the real test.
- **No relevance harness.** The Phase 2.8 evals measured recall@k and MRR over
  embedding retrieval and were deleted with them. The field boosts in
  `apps/search/mapping.py` are reasoned, **not measured** — there is no query log
  to tune against yet.
- **Deletions are eventually consistent.** There is no delete endpoint in the
  project at all, so profiles leave via admin/cascade/seeder and only leave the
  index when a rebuild runs. `remove_profile()` is tested and uncalled, waiting
  for a `profile.deleted` event that has nothing to publish it.

---

## Phase 3 — Gig and audition board
**Status: ✅ Complete** — deployed & verified live at https://api.frikkinwave.com (image `fc4d97e`, 2026-06-07)

New `apps/listings` app (three-layer, no cross-app model imports).

| Sub-step | Status |
|---|---|
| 3.1 `Listing` model (gig / audition / venue types, soft-delete via `is_active`) + migration | ✅ |
| 3.2 Post / retrieve / update / soft-delete listing endpoints (author-only mutation) + tests | ✅ |
| 3.3 Browse + filter listings (type / city / country, active only, cursor-paginated) + tests | ✅ |
| 3.4 Apply to a listing — `ListingApplication` (contact-request variant): apply / accept / decline / reveal-on-accept + Celery email notifications + tests | ✅ |
| 3.5 Docs sync (DATAMODEL, CODEBASE, ROADMAP) | ✅ |

Deployed manually via the rolling update flow (push-image → apply -var image_tag → run-migrations; no CD).

---

## Phase 4 — Bands + session musicians
**Status: ✅ Complete** — deployed & verified live at https://api.frikkinwave.com (image `55a0f3e`, 2026-06-07). Three feature blocks, each shipped as its own PR.

### Block A — Bands → `apps/bands` ✅ (code complete)

| Sub-step | Status |
|---|---|
| 4.1 `Band` + `BandMembership` models (owner FK, slug, soft-delete; membership unique per band+member) + migration | ✅ |
| 4.2 Band CRUD (create/retrieve/update/soft-delete, owner-only) + public band page by slug (with accepted roster) + tests | ✅ |
| 4.3 Membership invite flow — owner invites by username → accept/decline → reveal-on-accept + Celery email notifications + tests | ✅ |
| 4.4 Browse + filter bands (city / country, active-only, cursor-paginated) + tests | ✅ |

### Block B — Session-musician marketplace → `apps/engagements` ✅ (code complete)

| Sub-step | Status |
|---|---|
| 4.5 "Open to session work" intent on `MusicianProfile` (`is_open_to_session_work` + `session_rate`) + serializers + `?open_to_session` filter + migration `0006` + tests | ✅ |
| 4.6 `EngagementRequest` hire-intent flow — send/list/accept/decline/complete, reveal-on-accept + Celery email notifications + tests. **Hire-intent only, no payments.** | ✅ |

### Block C — Venue profiles → `apps/venues` ✅ (code complete)

| Sub-step | Status |
|---|---|
| 4.7 `Venue` model owned by a User (name, slug, description, address, city, country, capacity, website, soft-delete) + migration | ✅ |
| 4.8 Venue CRUD (create/retrieve/update/soft-delete, owner-only) + public page by slug + browse/filter (city/country) + tests | ✅ |

Ties into the existing `venue` listing type; the Phase 5 "venue user-type" is a later auth refinement.

---

## Phase 5 — Social layer
**Status: 🟡 In progress** — sliced into independently-shippable blocks (like Phase 4).
Blocks A–C are **deployed & verified live** at https://api.frikkinwave.com (image `46ed564`, 2026-06-09 — includes the review-rating profile embed). Block D not started.

### Block A — Follow graph → `apps/social` ✅ (code complete)

| Sub-step | Status |
|---|---|
| 5.1 `Follow` model (follower/followed FKs, unique edge, no-self-follow check constraint) + migration `0001` | ✅ |
| 5.2 Follow / unfollow endpoints (idempotent) + own following/followers lists + public per-user follower/following lists + tests | ✅ |

**User→user only** for now; band / venue follow targets are a deliberate later extension
(needs the feed to consume them, and a polymorphic target conflicts with the no-cross-app-import rule).
No follow notifications (follows are higher-volume than invites — kept silent by choice).

### Block B — Activity feed → `apps/social` ✅ (code complete)

| Sub-step | Status |
|---|---|
| 5.3 `Activity` (canonical event log) + `FeedEntry` (per-recipient inbox) models + migration `0002` | ✅ |
| 5.4 Fan-out-on-write pipeline: `record_activity` (producer service call) → post-commit Celery `fan_out_activity` → Activity + a FeedEntry per follower (+ actor); follow backfills, unfollow prunes | ✅ |
| 5.5 `GET /api/social/feed/` (cursor-paginated) + wire `listings`/`bands` create services to record activities + tests | ✅ |

**Architecture:** fan-out-on-write + async (Celery) recording — the heavier path, chosen
to match the scale rules. Activities = creation events only (posted-listing, created-band).
Known trade-off: write-amplification on high-follower accounts (celebrity problem) — a
hybrid push/pull split is the future mitigation. No GenericForeignKey: producers supply a
denormalized summary + opaque target fields, so `social` stays schema-ignorant of other apps.

### Block C — Ratings + reviews → `apps/reviews` ✅ (code complete)

| Sub-step | Status |
|---|---|
| 5.6 `Review` model (author/subject, 1-5 rating, denormalized context_type/context_id, unique-per-author-per-context, rating-range + no-self checks) + migration `0001` | ✅ |
| 5.7 `create_review` gated via `engagements.services.parties_of_completed_engagement` (service call, no model import) + bidirectional + dedupe; `GET /api/reviews/<username>/` list + `/summary/` (avg, count) + tests | ✅ |

**Gate:** a review requires a COMPLETED `EngagementRequest` between the two users, verified
through a new `engagements.services` function (no cross-app model import). Gate-agnostic
model (`context_type`/`context_id`) so accepted-listing-application gating is additive.
Fast-follow ✅ done: the musician profile payload embeds `{average_rating, count}` on
single-profile responses (public / `/me` / create) via `reviews.services.rating_summary`.

### Block D — Real-time messaging ⬜ (DEFERRED — to be planned later)
**Explicitly parked.** Blocks A–C shipped; Block D is intentionally postponed and will get
its own planning pass before any code. It is **not** a drop-in new app like A–C — it's the
one block that forces an infrastructure change, so it's treated as a separate mini-project.

Django Channels + Redis (WebSockets). Open questions / known implications to work through
when we pick it up:
- **ASGI runtime.** Current prod serves WSGI via gunicorn; Channels needs ASGI (uvicorn/
  daphne). Either swap the server or run a **separate ASGI service** alongside the WSGI web
  service (likely cleaner — keep REST on WSGI, WebSockets on their own task/target group).
- **ALB / infra.** WebSocket upgrade support + sticky sessions (or a stateless channel
  layer), a separate Deployment + Service with its own Ingress path, and health checks for
  the WS path. Helm chart changes — see infra/README.
- **Channel layer.** `channels_redis` needs a Redis, and **there is no longer one** — it went with Celery in KAFKA.md stage 5. This block would have to stand one back up, which is a real cost to weigh rather than a reuse.
  Note the current broker has no persistence — fine for Celery because the outbox makes
  delivery recoverable, but a channel layer losing state is directly user-visible.
- **Data model.** `Conversation` + `Message` (gating: who may DM whom — any user, or only
  connected/followed/engaged users?). Persistence + read receipts are scope decisions.
- **Auth over WS.** JWT in the connect handshake (query param / subprotocol), since headers
  are awkward on browser WebSocket clients.

No models, endpoints, or infra for this exist yet.

---

## Kafka migration — see `KAFKA.md`

Runs alongside the phase work rather than inside it: it replaces the *transport*
under the existing event backbone, not any product behaviour.

| Stage | Status |
|---|---|
| 0 EBS CSI driver + default gp3 StorageClass (the cluster could not provision storage at all) | ✅ |
| 1 Node capacity — 3 nodes across three AZs. **Instance-type list must span FAMILIES, not just sizes** — a t4g-wide capacity shortage stalled a rebuild for 20 min | ✅ |
| 2 Strimzi 1.1.0 + Kafka 4.2.1, KRaft, 3 brokers, RF 3 / ISR 2 | ✅ |
| — Security baseline: TLS + mTLS client certs + deny-by-default ACLs + NetworkPolicy enforcement | ✅ |
| 3 `_dispatch()` produces to Kafka behind `EVENT_TRANSPORT` (default `celery`) | ✅ |
| 4a Per-app `consumers.py`, consumer runtime, bounded retry + dead-letter topics | ✅ |
| 4b A Deployment per consumer group, DLT topics + ACLs, live verification | ✅ |
| 5 Remove Celery — modules, settings, workers, the dependency, the `EVENT_TRANSPORT` flag, and Redis. Replaced by a relay Deployment (`relay_outbox --loop`) | ✅ |

| 6 Observability — consumer lag, alerts, dashboard (see below) | ✅ |

**Complete.** Verified live: `publish()` → outbox → relay Deployment → Kafka →
consumer group → handler, with no Celery and no Redis in the cluster.

### Observability — complete (2026-08-20 → 2026-08-21)

**Consumer lag.** Strimzi's `kafkaExporter` feeds `kafka_consumergroup_lag` to a
kube-prometheus-stack installed by Terraform, with a Grafana event-pipeline
dashboard and four alerts: consumer lag, a group with **zero members** (not the
same as lag — with nothing joined there may be no lag series at all),
under-replicated partitions, and anything landing in a `.dlt` topic.

**The relay reports its own health.** Three gauges on
`EVENT_RELAY_METRICS_PORT` behind a PodMonitor, with `OutboxRelayDown`,
`OutboxNotDraining` and `OutboxEventsExhausted`. This is the failure no
Kafka-side alert can see — a stalled relay means nothing reaches the broker to be
lagged on, so every lag alert stays silent while the pipeline is dead. The
`check_outbox_lag` CronJob is retired in favour of the gauge; the command remains
for use by hand, and both read `outbox_lag_snapshot()` so they cannot disagree.

**Alerts reach a human.** SNS → email, with Alertmanager publishing via IRSA
(`infra/eks/alerting.tf`). The topic and subscription live in the **persistent**
stack, so the confirmation click happens once instead of after every teardown.
Watchdog and InfoInhibitor are black-holed — Watchdog fires constantly by design
as a dead-man's switch, and supplying an Alertmanager `config` replaces the
chart's default handling of it.

**Everything above is drilled, and one drill found a defect.** `OutboxNotDraining`
could not fire: unspaced retries burned `MAX_ATTEMPTS` in ~10 seconds, so its
gauge peaked at 10s against a 300s threshold and fell back to zero. Fixed with
**retry backoff** (`next_attempt_at`, 2s doubling to a 600s cap, ~27 minutes) and
re-drilled — it now fires at 10m45s with events still recoverable. That also
repaired the durability claim: a broker outage longer than ten seconds previously
stranded every pending event permanently.

**Load, measured at last.** ~**87 events/sec** through the relay, ~11.5ms per
event, with the relay throttled on only 0.6% of CFS periods — so the bottleneck
is the `acks=all` round-trip, not CPU. Consumer lag peaked at 0, so the ceiling
is producer-side. Batching the flush is the available optimisation if that is
ever not enough.

### Failure behaviour, verified not assumed

| drill | result |
|---|---|
| Relay force-killed, events published with none running | Restarted by k8s, all drained. No intervention. |
| One broker killed | Transparent — RF 3 / ISR 2 held. |
| **Two of three brokers killed** (quorum lost) | Produces failed, events stayed pending, relay survived, outbox lag alerted, everything drained on recovery. |
| Consumer group scaled to zero | Lag and no-members alerts fired, then cleared once restored. |
| Relay scaled to zero (2026-08-21) | `OutboxRelayDown` fired after ~4m, cleared 37s after restore, unaided. |
| Publishing broken, relay healthy (2026-08-21) | `OutboxEventsExhausted` fired; `OutboxNotDraining` did not — the defect that produced the backoff fix. |
| Alert delivered to email (2026-08-21) | SNS: published 2, delivered 1 — the undelivered one was published while the subscription was unconfirmed. |

A total broker outage degrades to **delayed** delivery, never lost delivery —
true since retries gained backoff (2026-08-21). Before that, `MAX_ATTEMPTS` was
spent in ~10 seconds, so an outage longer than that stranded every pending event
permanently. The earlier drill only passed because the brokers returned inside
that window. Ten attempts now span 27 minutes.

That is the outbox earning its place.

---

## Deployment targets (backend only)

| Service | Platform | Status |
|---|---|---|
| Backend API | AWS EKS (Kubernetes) | ✅ Live (ap-south-1, HTTPS) — web ×2, relay, + 4 Kafka consumer groups |
| Database | AWS RDS (Postgres 16) | ✅ Live (ap-south-1, reachable only from the cluster SG) |
| Search | AWS OpenSearch 3.7 | ⬜ Declared in Terraform, never applied — VPC-only, FGAC, index rebuilt on deploy |
| Event backbone | Strimzi Kafka on EKS | ✅ Live (3 brokers, KRaft, RF 3 / ISR 2, TLS + mTLS + ACLs) — see `KAFKA.md` |
| Container registry | AWS ECR | ✅ Live (ap-south-1) |
| DNS | api.frikkinwave.com → ALB | ✅ Live (Route 53 subdomain + ACM HTTPS) |
| Future | AWS EKS | Phase 4+ |
