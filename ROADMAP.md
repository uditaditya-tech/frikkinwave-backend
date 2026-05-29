# Roadmap — frikkinwave backend

Each phase is independently shippable. "Stop here" = the product is usable at this point.

---

## Phase 0 — Scaffold
**Status: ✅ Complete**
CI green, dev stack running, zero feature code.

| Sub-step | Status |
|---|---|
| 0.1 Django project skeleton (config as project dir) | ✅ |
| 0.2 Settings split: base / local / production + django-environ | ✅ |
| 0.3 Custom User model (UUIDv7, email login, username slug) + initial migration | ✅ |
| 0.4 ruff + mypy strict + pre-commit hooks | ✅ |
| 0.5 GitHub Actions CI (lint, type-check, migrate, pytest) | ✅ |
| 0.6 docker-compose (Postgres 16 + Redis 7) | ✅ |
| 0.7 drf-spectacular wired — /api/schema/ returns valid OpenAPI doc | ✅ |
| 0.8 Frontend repo — Next.js 14, TypeScript strict, ESLint, Prettier, shadcn/ui | ⬜ |
| 0.9 Frontend CI + openapi-typescript script pointing at backend schema | ⬜ |

---

## Phase 1 — Musician profiles + jam partner discovery
**Status: ⬜ Not started**
Stop here = shippable v1 on frikkinwave.com

Sub-steps (to be detailed when Phase 0 closes):
- MusicianProfile model (instruments, genres, city/country, bio, availability toggle)
- Instrument + Genre seed data
- Profile create/update/retrieve endpoints
- Browse + filter (by city, country, instrument, genre)
- View a public profile
- Contact request flow (send → email notification → accept/decline → contact info revealed)
- Auth endpoints (register, login, refresh, logout)
- Dockerfile + ECR push
- ECS task definition + Fargate service + ALB
- DNS: api.frikkinwave.com → ALB
- Vercel frontend deploy + frikkinwave.com DNS

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

## Deployment targets

| Service | Platform | Status |
|---|---|---|
| Backend API | AWS ECS + Fargate | ⬜ Phase 1 |
| Database | AWS RDS (Postgres) | ⬜ Phase 1 |
| Cache / broker | AWS ElastiCache (Redis) | ⬜ Phase 2 |
| Container registry | AWS ECR | ⬜ Phase 1 |
| Frontend | Vercel | ⬜ Phase 1 |
| DNS | frikkinwave.com (custom) | ⬜ Phase 1 |
| Future | AWS EKS | Phase 4+ |
