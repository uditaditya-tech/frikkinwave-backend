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
**Status: 🔄 In progress**
Stop here = shippable v1 on frikkinwave.com

| Sub-step | Status |
|---|---|
| 1.1 Auth endpoints — register, login, refresh, logout + tests | ✅ |
| 1.2 MusicianProfile model (bio, city, country, availability) + migration | ✅ |
| 1.3 Instrument + Genre models + seed data (management command) | ✅ |
| 1.4 Profile create / update / retrieve endpoints + tests | ✅ |
| 1.5 Browse + filter profiles (city, country, instrument, genre) + tests | ✅ |
| 1.6 Public profile view (unauthenticated) + tests | ✅ |
| 1.7 ContactRequest flow (send → email → accept/decline → reveal) + tests | ⬜ |
| 1.8 Dockerfile (multi-stage, collectstatic baked in) | ⬜ |
| 1.9 ECR repo + push script; ECS task definition + Fargate service + ALB | ⬜ |
| 1.10 RDS Postgres + secrets in SSM/Secrets Manager | ⬜ |
| 1.11 DNS: api.frikkinwave.com → ALB | ⬜ |

---

## Phase 2 — AI-powered matching
**Status: ⬜ Not started**
Stop here = AI on the tin, portfolio centerpiece live

- pgvector extension + ProfileEmbedding model
- Celery worker + Redis broker
- Embedding pipeline: profile save → Celery task → OpenAI text-embedding-3-small → pgvector store
- Semantic search endpoint: natural language query → embedding → nearest-neighbor retrieval
- Compatibility blurb: gpt-4o-mini generates "Why you might click" per profile pair, cached
- Profile coach: LLM evaluates completeness on profile setup, surfaces specific suggestions
- Evals: measure embedding retrieval quality, blurb relevance

---

## Phase 3 — Gig and audition board
**Status: ⬜ Not started**

- Listing model (gig / audition / venue types)
- Post a listing, browse listings, filter by type / city / country
- Apply to a listing (contact request variant)

---

## Phase 4 — Bands + session musicians
**Status: ⬜ Not started**

- Band as a group entity (multiple member profiles)
- Session musician marketplace (hire intent, paid engagement)
- Venue profiles (Phase 5 user type: venue)

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
| Backend API | AWS ECS + Fargate | ⬜ Phase 1 |
| Database | AWS RDS (Postgres 16) | ⬜ Phase 1 |
| Cache / broker | AWS ElastiCache (Redis) | ⬜ Phase 2 |
| Container registry | AWS ECR | ⬜ Phase 1 |
| DNS | api.frikkinwave.com → ALB | ⬜ Phase 1 |
| Future | AWS EKS | Phase 4+ |
