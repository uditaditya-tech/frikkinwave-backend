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
| 0.6 docker-compose (Postgres 16 + Redis 7) | ✅ |
| 0.7 drf-spectacular wired — /api/schema/ returns valid OpenAPI doc | ✅ |
| 0.8 ~~Frontend~~ — out of scope for this repo | N/A |
| 0.9 ~~Frontend CI~~ — out of scope for this repo | N/A |

---

## Phase 1 — Musician profiles + jam partner discovery
**Status: ✅ Complete**
Shipped: live at https://api.frikkinwave.com (ECS Fargate + ALB + RDS, ap-south-1)

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
**Status: ✅ Complete**
Shipped: deployed & verified live at https://api.frikkinwave.com (web + Celery worker + ElastiCache + pgvector, ap-south-1)

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
  layer), a new ECS service + target group, and health checks for the WS path. Terraform
  app-stack changes — see infra/README.
- **Channel layer.** Reuse the existing ElastiCache Redis (`channels_redis`) vs a dedicated
  instance.
- **Data model.** `Conversation` + `Message` (gating: who may DM whom — any user, or only
  connected/followed/engaged users?). Persistence + read receipts are scope decisions.
- **Auth over WS.** JWT in the connect handshake (query param / subprotocol), since headers
  are awkward on browser WebSocket clients.

No models, endpoints, or infra for this exist yet.

---

## Deployment targets (backend only)

| Service | Platform | Status |
|---|---|---|
| Backend API | AWS ECS + Fargate | ✅ Live (ap-south-1, HTTPS) — web + Celery worker |
| Database | AWS RDS (Postgres 16) | ✅ Live (ap-south-1, private subnets) |
| Cache / broker | AWS ElastiCache (Redis) | ✅ Live (ap-south-1, Celery broker) |
| Container registry | AWS ECR | ✅ Live (ap-south-1) |
| DNS | api.frikkinwave.com → ALB | ✅ Live (Route 53 subdomain + ACM HTTPS) |
| Future | AWS EKS | Phase 4+ |
