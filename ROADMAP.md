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
**Status: ✅ Complete (code)** — three feature blocks, each its own PR. Not yet deployed.

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
**Status: ⬜ Not started**

- Follow / unfollow
- Activity feed
- Ratings + reviews
- Real-time messaging (Django Channels + Redis)

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
