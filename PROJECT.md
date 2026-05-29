# frikkinwave — Backend

## What it is

A global, profile-centric social network for musicians and live-shows industry professionals.

**The problem:** Finding jam partners, bandmates, session musicians, gigs, venues, and auditions is fragmented across WhatsApp groups, Instagram DMs, and word-of-mouth. No single platform is built specifically for this.

**The product:** Musicians build profiles. Others discover them by instrument, genre, city. The jam partner finder is the Phase 1 anchor. AI-powered semantic matching is the Phase 2 centerpiece.

**The portfolio goal:** Real deployment on frikkinwave.com signals genuine problem-solving intent — not just a demo project.

---

## Stack

| Layer | Choice | Why |
|---|---|---|
| Framework | Django 4.x + DRF | Three-layer architecture fits naturally; battle-tested |
| Auth | simplejwt + blacklist app | Refresh token rotation with blacklisting |
| Database | PostgreSQL + pgvector | One store for relational data AND embeddings |
| Background jobs | Celery + Redis | Async embedding generation on profile save |
| AI | OpenAI text-embedding-3-small + gpt-4o-mini | Cheapest capable models; swap-able behind a service interface |
| API contract | drf-spectacular → OpenAPI schema | Frontend auto-generates typed client via openapi-typescript |
| Deployment | AWS ECS + Fargate (EKS migration path later) | Container-based; same Docker image retargets to EKS |
| CI | GitHub Actions | Lint + type-check + migrate + pytest on every push |

---

## Architecture principles

**Three-layer architecture — strictly enforced:**
- **Views** — HTTP shell only. Parse request, call a service, return response. No business logic.
- **Services** (`apps/<app>/services.py`) — All business logic lives here. Called by views and tasks.
- **Models** — Data shape only. No methods that contain business logic.

**Custom User model from day one.**
`AUTH_USER_MODEL = "users.User"` — email as login identifier, username as URL-safe slug, UUIDv7 primary key.

**UUIDv7 primary keys throughout.**
Time-ordered, index-friendly, safe to expose in URLs. Uses `uuid6` backport (stdlib `uuid.uuid7()` is Python 3.14+).

**Settings split:** `config/settings/base.py` → `local.py` / `production.py`. Secrets via `django-environ`. `.env` is git-ignored; `.env.example` is committed.

---

## Deployment architecture (AWS)

```
GitHub → CI (Actions) → ECR (Docker image)
                              ↓
                        ECS Fargate task
                              ↓
                    ALB → api.frikkinwave.com
```

Frontend: Vercel → frikkinwave.com
DNS split: apex → Vercel, `api.*` → AWS ALB

---

## Companion repo

Frontend: `frikkinwave-frontend` (Next.js 14 App Router, TypeScript strict, Vercel)
