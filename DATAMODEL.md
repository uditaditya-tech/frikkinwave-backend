# Data Model — frikkinwave backend

All models use UUIDv7 primary keys. All timestamps are UTC.
Models are data-shape only — business logic lives in services.

---

## Current models

### `users.User` (custom auth model)

**App:** `apps/users`
**Migration:** `0001_initial`

| Field | Type | Notes |
|---|---|---|
| `id` | UUIDField (PK) | UUIDv7 via `uuid6` backport |
| `email` | EmailField | Unique. Login identifier (`USERNAME_FIELD`). |
| `username` | SlugField(50) | Unique. URL-safe. Used in profile URLs: `/u/<username>` |
| `is_active` | BooleanField | Default True |
| `is_staff` | BooleanField | Default False. Admin access. |
| `date_joined` | DateTimeField | `auto_now_add` |
| `updated_at` | DateTimeField | `auto_now` |

**Auth:** `AbstractBaseUser` + `PermissionsMixin`
**Manager:** `UserManager` (custom — `create_user`, `create_superuser`)

---

### `musicians.MusicianProfile` (Phase 1 — 1.2 ✅)

One-to-one with `User`. The public-facing profile.
**App:** `apps/musicians` | **Migration:** `0001_initial`

| Field | Type | Notes |
|---|---|---|
| `id` | UUIDField (PK) | UUIDv7 |
| `user` | OneToOneField → `AUTH_USER_MODEL` | Cascade delete |
| `bio` | TextField | Free-form. Blank allowed. Fed into embedding (Phase 2). |
| `city` | CharField(100) | Free-text for Phase 1; normalise later if geo-search demands it. |
| `country` | CharField(100) | Free-text for Phase 1. |
| `is_available` | BooleanField | Default True. Toggles visibility in jam finder. |
| `created_at` | DateTimeField | `auto_now_add` |
| `updated_at` | DateTimeField | `auto_now` |

---

### `musicians.Instrument` + `musicians.Genre` (Phase 1 — 1.3 ✅)

**App:** `apps/musicians` | **Migration:** `0002_*`
Seeded via `python manage.py seed_music_data` (44 instruments, 31 genres). Idempotent.

| Field | Type | Notes |
|---|---|---|
| `id` | UUIDField (PK) | UUIDv7 |
| `name` | CharField(100) | Unique |
| `slug` | SlugField(100) | Unique. Auto-derived from name on save. |

`MusicianProfile.instruments` — M2M through `MusicianInstrument` (with `proficiency`).
`MusicianProfile.genres` — plain M2M.

### `musicians.MusicianInstrument` (through model, Phase 1 — 1.3 ✅)

| Field | Type | Notes |
|---|---|---|
| `id` | UUIDField (PK) | UUIDv7 |
| `profile` | ForeignKey → MusicianProfile | Cascade delete |
| `instrument` | ForeignKey → Instrument | Cascade delete |
| `proficiency` | CharField | `beginner` / `intermediate` / `advanced` |

Unique constraint on `(profile, instrument)`.

---

### `connections.ContactRequest` (Phase 1 — 1.7 ✅)

A one-directional request from one user to connect with another.
**App:** `apps/connections` | **Migration:** `0001_initial`

| Field | Type | Notes |
|---|---|---|
| `id` | UUIDField (PK) | UUIDv7 |
| `sender` | ForeignKey → `AUTH_USER_MODEL` | `related_name="sent_contact_requests"`. String ref — no cross-app model import. |
| `recipient` | ForeignKey → `AUTH_USER_MODEL` | `related_name="received_contact_requests"`. String ref. |
| `message` | TextField | Optional intro message (blank=True) |
| `status` | CharField(10) | `pending` / `accepted` / `declined`. Default `pending`. |
| `created_at` | DateTimeField | `auto_now_add` |
| `updated_at` | DateTimeField | `auto_now` |

Unique constraint on `(sender, recipient)`.
Self-requests rejected in the service layer.
Username → user resolution goes through `apps.users.services.get_user_by_username` (no model import).
Flow: send → accept/decline → contact email revealed to both parties once accepted.
**Email notification on send/accept is deferred to Phase 2** (handled by a Celery task once the worker + broker land).

---

## Planned models (Phase 2 — AI)

### `musicians.ProfileEmbedding`

One-to-one with `MusicianProfile`. Stores the pgvector embedding.

> **pgvector setup:** enable the `vector` extension (`CREATE EXTENSION vector`) as the
> first operation in the migration that adds the `VectorField` — RDS Postgres 16 supports
> it, the master user can run it. Add `pgvector` to requirements; for local dev use the
> `pgvector/pgvector` Postgres image in docker-compose. (See CLAUDE.md gotchas.)

| Field | Type | Notes |
|---|---|---|
| `id` | UUIDField (PK) | UUIDv7 |
| `profile` | OneToOneField → MusicianProfile | Cascade delete |
| `embedding` | VectorField(1536) | text-embedding-3-small output (1536 dims) |
| `embedding_text` | TextField | The raw text that was embedded (for debugging) |
| `generated_at` | DateTimeField | When the embedding was last computed |

### `musicians.CompatibilityBlurb`

Cached LLM-generated "Why you might click" text for a pair of profiles.

| Field | Type | Notes |
|---|---|---|
| `id` | UUIDField (PK) | UUIDv7 |
| `profile_a` | ForeignKey → MusicianProfile | |
| `profile_b` | ForeignKey → MusicianProfile | |
| `blurb` | TextField | gpt-4o-mini generated text |
| `generated_at` | DateTimeField | For cache invalidation |

Unique constraint on `(profile_a, profile_b)`.

---

## Planned models (Phase 3 — gigs & auditions)

### `listings.Listing`

| Field | Type | Notes |
|---|---|---|
| `id` | UUIDField (PK) | UUIDv7 |
| `author` | ForeignKey → User | |
| `listing_type` | CharField | `gig` / `audition` / `venue` |
| `title` | CharField(200) | |
| `description` | TextField | |
| `city` | CharField(100) | |
| `country` | CharField(100) | |
| `is_paid` | BooleanField | |
| `pay_description` | CharField(200) | Optional. e.g. "₹2000 per show" |
| `deadline` | DateField | Optional |
| `is_active` | BooleanField | Default True |
| `created_at` | DateTimeField | `auto_now_add` |

---

## Design rules

- Every model gets a UUIDv7 `id` as primary key.
- Soft deletes via `is_active` flag where appropriate (listings, profiles).
- No business logic in model methods — services only.
- `__str__` must always be defined.
- `Meta.ordering` always declared explicitly.
