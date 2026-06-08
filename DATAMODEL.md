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
| `sound_url` | URLField(500) | Optional. External track (SoundCloud/Spotify/YouTube) embedded on the profile. |
| `is_open_to_session_work` | BooleanField | Default False. Session-musician marketplace flag (Phase 4 Block B). Filter: `?open_to_session=true`. |
| `session_rate` | CharField(200) | Optional free-text rate, e.g. "₹5000/session". Hire-intent only — no payments. |
| `created_at` | DateTimeField | `auto_now_add` |
| `updated_at` | DateTimeField | `auto_now` |

> Session-work fields added in migration `0006`. They are **not** part of
> `build_embedding_text`, so toggling them doesn't trigger re-embedding.

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
**Email notifications wired in Phase 2.2:** `send` emits `connections.notify_new_contact_request` (emails the recipient); `accept` emits `connections.notify_contact_request_accepted` (emails the sender, revealing the recipient's contact email). Both are emitted via `transaction.on_commit(... .delay())` from the service layer — see `apps/connections/tasks.py`.

---

### `musicians.ProfileEmbedding` (Phase 2 — 2.3 ✅)

One-to-one with `MusicianProfile`. Stores the pgvector embedding.
**App:** `apps/musicians` | **Migration:** `0004_profileembedding`
(enables the `vector` extension via `VectorExtension()` as its first op.)

| Field | Type | Notes |
|---|---|---|
| `id` | UUIDField (PK) | UUIDv7 |
| `profile` | OneToOneField → MusicianProfile | Cascade delete. `related_name="embedding"`. |
| `embedding` | VectorField(1536) | text-embedding-3-small output (1536 dims). `EMBEDDING_DIMENSIONS` const. |
| `embedding_text` | TextField | The raw text that was embedded (for debugging / re-embedding) |
| `generated_at` | DateTimeField | `auto_now` — when the embedding was last computed |

HNSW index `profile_embedding_hnsw` on `embedding` with `vector_cosine_ops`
(m=16, ef_construction=64) — cosine because text-embedding-3-small vectors are
normalised. **Populated by the 2.4 pipeline:** `create_profile` / `update_profile`
emit `musicians.generate_profile_embedding` via `on_commit`; the task builds the
profile text, embeds it through the OpenAI wrapper, and upserts this row. Skips
the OpenAI call when the embedding text is unchanged or no API key is set.

---

### `musicians.CompatibilityBlurb` (Phase 2 — 2.6 ✅)

Cached LLM-generated "Why you might click" text for a pair of profiles.
**App:** `apps/musicians` | **Migration:** `0005_compatibilityblurb`

| Field | Type | Notes |
|---|---|---|
| `id` | UUIDField (PK) | UUIDv7 |
| `profile_a` | ForeignKey → MusicianProfile | `related_name="compat_blurbs_as_a"` |
| `profile_b` | ForeignKey → MusicianProfile | `related_name="compat_blurbs_as_b"` |
| `blurb` | TextField | gpt-4o-mini generated text |
| `generated_at` | DateTimeField | `auto_now` |

Unique constraint on `(profile_a, profile_b)`. **Canonical unordered pair:**
`get_compatibility_blurb` orders the two profiles by id before lookup, so
`(A,B)` and `(B,A)` share one row. Generated synchronously on cache miss via
`GET /api/musicians/compatibility/<username>/`; returns None (→ 503) with no key.

---

### `listings.Listing` (Phase 3 — 3.1 ✅)

A gig / audition / venue posting on the board.
**App:** `apps/listings` | **Migration:** `0001_initial`

| Field | Type | Notes |
|---|---|---|
| `id` | UUIDField (PK) | UUIDv7 |
| `author` | ForeignKey → `AUTH_USER_MODEL` | `related_name="listings"`. String ref — no cross-app import. |
| `listing_type` | CharField(10) | `gig` / `audition` / `venue` (TextChoices) |
| `title` | CharField(200) | |
| `description` | TextField | |
| `city` | CharField(100) | Free-text. Browse filter is `__iexact`. |
| `country` | CharField(100) | Free-text. Browse filter is `__iexact`. |
| `is_paid` | BooleanField | Default False |
| `pay_description` | CharField(200) | Optional (blank). e.g. "₹2000 per show" |
| `deadline` | DateField | Optional (null/blank) |
| `is_active` | BooleanField | Default True. Soft-delete flag — browse + retrieve show active only. |
| `created_at` | DateTimeField | `auto_now_add` |
| `updated_at` | DateTimeField | `auto_now` |

`Meta.ordering = ["-created_at"]`. Author-only mutation enforced in the service layer.

---

### `listings.ListingApplication` (Phase 3 — 3.4 ✅)

A musician's application to a listing — the contact-request variant for the board.
**App:** `apps/listings` | **Migration:** `0002_listingapplication`

| Field | Type | Notes |
|---|---|---|
| `id` | UUIDField (PK) | UUIDv7 |
| `listing` | ForeignKey → Listing | Cascade delete. `related_name="applications"`. |
| `applicant` | ForeignKey → `AUTH_USER_MODEL` | `related_name="listing_applications"`. String ref. |
| `message` | TextField | Optional intro message (blank=True) |
| `status` | CharField(10) | `pending` / `accepted` / `declined`. Default `pending`. |
| `created_at` | DateTimeField | `auto_now_add` |
| `updated_at` | DateTimeField | `auto_now` |

Unique constraint on `(listing, applicant)`.
Self-applications (author applying to own listing) rejected in the service layer.
Flow: apply → accept/decline (listing author only) → contact email revealed to both parties once accepted.
**Email notifications:** `apply` emits `listings.notify_new_application` (emails the listing author); `accept` emits `listings.notify_application_accepted` (emails the applicant, revealing the author's contact email). Both via `transaction.on_commit(... .delay())` — see `apps/listings/tasks.py`.

---

### `bands.Band` (Phase 4 — 4.1 ✅)

A band / group entity, owned by one user, with an invited member roster.
**App:** `apps/bands` | **Migration:** `0001_initial`

| Field | Type | Notes |
|---|---|---|
| `id` | UUIDField (PK) | UUIDv7 |
| `owner` | ForeignKey → `AUTH_USER_MODEL` | `related_name="owned_bands"`. String ref. |
| `name` | CharField(200) | |
| `slug` | SlugField(120) | Unique. URL handle (`/api/bands/<slug>/`). Derived from name in the service layer with a numeric suffix on collision. |
| `bio` | TextField | Blank allowed. |
| `city` | CharField(100) | Blank allowed. Browse filter `__iexact`. |
| `country` | CharField(100) | Blank allowed. Browse filter `__iexact`. |
| `is_active` | BooleanField | Default True. Soft-delete — browse + retrieve show active only. |
| `created_at` / `updated_at` | DateTimeField | `auto_now_add` / `auto_now` |

`Meta.ordering = ["-created_at"]`. Owner-only mutation enforced in the service layer.

---

### `bands.BandMembership` (Phase 4 — 4.1 ✅)

An invitation / membership tying a user to a band — the contact-request variant for rosters.
**App:** `apps/bands` | **Migration:** `0001_initial`

| Field | Type | Notes |
|---|---|---|
| `id` | UUIDField (PK) | UUIDv7 |
| `band` | ForeignKey → Band | Cascade delete. `related_name="memberships"`. |
| `member` | ForeignKey → `AUTH_USER_MODEL` | `related_name="band_memberships"`. String ref (member is a User, not a MusicianProfile — keeps apps decoupled). |
| `role` | CharField(100) | Optional free-text role, e.g. "Lead guitarist". |
| `status` | CharField(10) | `pending` / `accepted` / `declined`. Default `pending`. |
| `created_at` / `updated_at` | DateTimeField | `auto_now_add` / `auto_now` |

Unique constraint on `(band, member)`. The owner is the `Band.owner` field, **not** a membership row — the roster (accepted memberships) lists invited members only.
Flow: owner invites by username → invitee accepts/declines → contact email revealed to both parties once accepted.
**Email notifications:** `invite` emits `bands.notify_band_invite` (emails the invitee); `accept` emits `bands.notify_band_invite_accepted` (emails the owner). Both via `transaction.on_commit(... .delay())` — see `apps/bands/tasks.py`.

---

### `engagements.EngagementRequest` (Phase 4 — Block B ✅)

A request to hire a musician for session/paid work — the contact-request variant
for the marketplace. Hire-intent only (no real payments).
**App:** `apps/engagements` | **Migration:** `0001_initial`

| Field | Type | Notes |
|---|---|---|
| `id` | UUIDField (PK) | UUIDv7 |
| `requester` | ForeignKey → `AUTH_USER_MODEL` | `related_name="sent_engagement_requests"`. String ref. |
| `musician` | ForeignKey → `AUTH_USER_MODEL` | `related_name="received_engagement_requests"`. The user being hired. String ref. |
| `message` | TextField | Optional intro (blank=True) |
| `proposed_date` | DateField | Optional (null/blank). When the session/gig is. |
| `rate_offer` | CharField(200) | Optional free-text offer, e.g. "₹5000". |
| `status` | CharField(10) | `pending` / `accepted` / `declined` / `completed`. Default `pending`. |
| `created_at` / `updated_at` | DateTimeField | `auto_now_add` / `auto_now` |

**No** unique constraint — a requester may hire the same musician repeatedly (different dates).
Self-hire rejected in the service layer.
Flow: send → accept/decline (musician only) → either party marks `completed` (only from `accepted`). Contact email revealed to both parties once accepted (and stays revealed when completed).
**Email notifications:** `send` emits `engagements.notify_new_engagement_request` (emails the musician); `accept` emits `engagements.notify_engagement_request_accepted` (emails the requester, revealing the musician's contact email). Both via `transaction.on_commit(... .delay())` — see `apps/engagements/tasks.py`.

---

### `venues.Venue` (Phase 4 — Block C ✅)

A user-owned venue profile (club, bar, studio, hall).
**App:** `apps/venues` | **Migration:** `0001_initial`

| Field | Type | Notes |
|---|---|---|
| `id` | UUIDField (PK) | UUIDv7 |
| `owner` | ForeignKey → `AUTH_USER_MODEL` | `related_name="venues"`. String ref. |
| `name` | CharField(200) | |
| `slug` | SlugField(120) | Unique. URL handle (`/api/venues/<slug>/`). Derived from name in the service layer with a numeric suffix on collision. |
| `description` | TextField | Blank allowed. |
| `address` | CharField(300) | Blank allowed. |
| `city` | CharField(100) | Blank allowed. Browse filter `__iexact`. |
| `country` | CharField(100) | Blank allowed. Browse filter `__iexact`. |
| `capacity` | PositiveIntegerField | Optional (null/blank). |
| `website` | URLField(500) | Optional. |
| `is_active` | BooleanField | Default True. Soft-delete — browse + retrieve show active only. |
| `created_at` / `updated_at` | DateTimeField | `auto_now_add` / `auto_now` |

`Meta.ordering = ["-created_at"]`. Owner-only mutation enforced in the service layer.
The Phase 5 "venue user-type" is a later auth refinement; for now a venue is owned by a regular user.

---

### `social.Follow` (Phase 5 — Block A ✅)

A directed follow edge in the social graph: `follower` follows `followed`.
**App:** `apps/social` | **Migration:** `0001_initial`

| Field | Type | Notes |
|---|---|---|
| `id` | UUIDField (PK) | UUIDv7 |
| `follower` | ForeignKey → `AUTH_USER_MODEL` | `related_name="following_set"`. String ref. |
| `followed` | ForeignKey → `AUTH_USER_MODEL` | `related_name="follower_set"`. String ref. |
| `created_at` | DateTimeField | `auto_now_add` |

`Meta.ordering = ["-created_at"]`. Constraints: `UniqueConstraint(follower, followed)`
(one edge per pair → follow is idempotent) and `CheckConstraint` blocking self-follow
(`follower != followed`; also guarded in the service). No soft-delete — unfollow is a
hard delete, and re-following simply recreates the edge. **User→user only** for now;
band / venue targets are a later extension once the activity feed (Block B) exists to
consume them.

---

### `social.Activity` (Phase 5 — Block B ✅)

Canonical event-log row — one per thing a user did. **Source of truth** for the feed.
**App:** `apps/social` | **Migration:** `0002`

| Field | Type | Notes |
|---|---|---|
| `id` | UUIDField (PK) | UUIDv7 |
| `actor` | ForeignKey → `AUTH_USER_MODEL` | `related_name="activities"`. String ref. |
| `verb` | CharField(32) | TextChoices: `posted_listing`, `created_band`. Vocabulary exposed to producers via `social.services.Verb`. |
| `summary` | CharField(300) | Denormalized human string supplied by the producer (e.g. listing title). |
| `target_type` | CharField(50) | Free string (`"listing"`, `"band"`). **No GenericForeignKey** — `social` stays ignorant of producers' schemas. |
| `target_id` | UUIDField | Nullable. The target object's id, for the frontend to link. |
| `target_slug` | CharField(120) | Blank allowed. Slug for linkable targets (bands, venues). |
| `created_at` | DateTimeField | `auto_now_add` |

`Meta.ordering = ["-created_at"]`, index on `(actor, -created_at)`. Written by the
`social.fan_out_activity` Celery task, **not** inline — producers call
`social.services.record_activity(...)` which emits the fan-out post-commit.

---

### `social.FeedEntry` (Phase 5 — Block B ✅)

Per-recipient inbox row (fan-out-on-write): one copy of an Activity per follower (plus
the actor). The feed read touches **only this table**.
**App:** `apps/social` | **Migration:** `0002`

| Field | Type | Notes |
|---|---|---|
| `id` | UUIDField (PK) | UUIDv7 |
| `owner` | ForeignKey → `AUTH_USER_MODEL` | `related_name="feed_entries"`. The recipient. String ref. |
| `activity` | ForeignKey → `Activity` | `related_name="feed_entries"`. |
| `created_at` | DateTimeField | **Denormalized** from `activity.created_at` so cursor pagination orders correctly even for follow-time backfilled entries (no `auto_now_add`). |

`Meta.ordering = ["-created_at"]`, `UniqueConstraint(owner, activity)` (fan-out is
idempotent via `ignore_conflicts`), index on `(owner, -created_at)`. Fan-out writes a
row per follower + the actor; **follow** backfills the followee's recent activities into
the new follower's inbox, **unfollow** prunes them — keeping the inbox consistent with
the follow graph. Both run as Celery tasks (`social.backfill_feed` / `social.prune_feed`).

**Architecture note:** fan-out-on-write was chosen deliberately (the heavier path) to
match the project's scale rules. The known trade-off is write-amplification on
high-follower accounts (the "celebrity problem"); a hybrid push/pull split is the future
mitigation. Activities recorded so far: posted-listing, created-band (creation events only).

---

### `reviews.Review` (Phase 5 — Block C ✅)

A 1-5 star rating + comment one user leaves about another, gated on a real completed
interaction so it can't be spammed against strangers.
**App:** `apps/reviews` | **Migration:** `0001_initial`

| Field | Type | Notes |
|---|---|---|
| `id` | UUIDField (PK) | UUIDv7 |
| `author` | ForeignKey → `AUTH_USER_MODEL` | `related_name="reviews_written"`. The reviewer. String ref. |
| `subject` | ForeignKey → `AUTH_USER_MODEL` | `related_name="reviews_received"`. The reviewed. String ref. |
| `rating` | PositiveSmallIntegerField | 1-5, enforced by validators **and** a DB check constraint. |
| `comment` | TextField | Blank allowed. |
| `context_type` | CharField(32) | TextChoices: `engagement` (the gate kind). |
| `context_id` | UUIDField | The gating interaction's id (a completed engagement), **denormalized — no cross-app FK**. |
| `created_at` / `updated_at` | DateTimeField | `auto_now_add` / `auto_now` |

`Meta.ordering = ["-created_at"]`, index on `(subject, -created_at)`. Constraints:
`UniqueConstraint(author, context_id)` (one review per reviewer per interaction →
blocks duplicate reviews of the same engagement), `CheckConstraint` rating 1-5, and
`CheckConstraint` author ≠ subject.

**Gate:** `reviews.services.create_review` verifies eligibility via
`engagements.services.parties_of_completed_engagement(id)` (service-to-service, no model
import) — the engagement must be `COMPLETED` and `{author, subject}` must be its two
parties. **Bidirectional**: both the requester and the musician can review each other
once per completed engagement. The model is **gate-agnostic** (`context_type`/`context_id`)
so future gates (e.g. accepted listing applications) are additive. Embedding a user's
average rating into the musician profile payload is a deliberate fast-follow.

---

## Design rules

- Every model gets a UUIDv7 `id` as primary key.
- Soft deletes via `is_active` flag where appropriate (listings, profiles).
- No business logic in model methods — services only.
- `__str__` must always be defined.
- `Meta.ordering` always declared explicitly.
