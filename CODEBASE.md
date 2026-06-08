# Codebase guide — frikkinwave backend

Read this to understand where things live and how to navigate the repo.

---

## Directory structure

```
frikkinwave-backend/
│
├── apps/                          # All Django apps live here
│   ├── __init__.py
│   │
│   ├── users/                     # Auth — custom User model + JWT auth endpoints
│   │   ├── admin.py
│   │   ├── apps.py                # name="apps.users", label="users"
│   │   ├── migrations/
│   │   │   └── 0001_initial.py
│   │   ├── models.py              # User (UUIDv7 PK, email login, username slug)
│   │   ├── serializers.py         # RegisterSerializer
│   │   ├── services.py            # register_user(), get_user_by_username()
│   │   ├── urls.py                # /register/, /logout/
│   │   ├── views.py               # RegisterView, LogoutView
│   │   └── tests/
│   │       ├── __init__.py
│   │       ├── conftest.py        # users-app-specific fixtures (currently empty)
│   │       └── test_auth.py       # 15 tests: register, login, refresh, logout
│   │
│   ├── musicians/                 # Musician profiles, instruments, genres
│   │   ├── admin.py
│   │   ├── apps.py                # name="apps.musicians", label="musicians"
│   │   ├── migrations/
│   │   │   ├── 0001_initial.py    # MusicianProfile
│   │   │   ├── 0002_*.py          # Instrument, Genre, MusicianInstrument, M2M fields
│   │   │   ├── 0003_*.py          # MusicianProfile.sound_url
│   │   │   ├── 0004_profileembedding.py  # VectorExtension + ProfileEmbedding + HNSW index
│   │   │   └── 0005_compatibilityblurb.py # CompatibilityBlurb (cached per profile pair)
│   │   ├── models.py              # Instrument, Genre, MusicianInstrument, MusicianProfile, ProfileEmbedding, CompatibilityBlurb
│   │   ├── serializers.py         # Read + Write + Detail (adds review rating) + ProfileSearchResultSerializer (adds similarity)
│   │   ├── services.py            # profiles + embeddings/search/compatibility blurb/coach_profile
│   │   ├── openai_client.py       # OpenAIClient (embed + complete) + get_openai_client() (swappable seam; mocked in tests)
│   │   ├── tasks.py               # Celery task: generate_profile_embedding (emitted on profile save via on_commit)
│   │   ├── urls.py                # /search/, /compatibility/<username>/, /profiles/, /profile/, /profile/coach/, /profile/me/
│   │   ├── views.py               # ProfileList/Public/Create/Me/Search/Compatibility/Coach views (+ ProfileCursorPagination)
│   │   ├── evals/                 # Phase 2.8 matching evals
│   │   │   ├── golden.py          # Golden profiles + labeled retrieval cases + blurb pairs
│   │   │   ├── metrics.py         # recall@k, precision@k, MRR, blurb_is_grounded (pure)
│   │   │   └── runner.py          # run_matching_eval() — seed→embed→search→blurbs→metrics (rolled back)
│   │   ├── management/
│   │   │   └── commands/
│   │   │       ├── seed_music_data.py   # Seeds 44 instruments + 31 genres
│   │   │       └── eval_matching.py     # Real eval (needs OPENAI_API_KEY) → JSON report
│   │   └── tests/
│   │       ├── __init__.py
│   │       ├── conftest.py        # instrument, genre, profile fixtures
│   │       ├── test_profile.py    # 26 tests: create, retrieve, update, list + filter, public view
│   │       ├── test_embedding.py  # 4 tests: vector round-trip, 1-per-profile, dim check, cosine kNN ordering
│   │       ├── test_embedding_pipeline.py  # 7 tests: build-text, save→embed, re-embed, content-skip, guards (OpenAI mocked)
│   │       ├── test_search.py     # 7 tests: ranking, limit, available filter, no-embedding exclusion, 400s, no-key (OpenAI mocked)
│   │       ├── test_compatibility.py  # 8 tests: generate+cache, reverse-pair cache, self/404/no-profile/401/503 (LLM mocked)
│   │       ├── test_coach.py      # 5 tests: missing-field suggestions, score 100, no-key null tip, no-profile 400, 401 (LLM mocked)
│   │       └── test_evals.py      # 7 tests: metric math + end-to-end harness w/ deterministic fake embedder + rollback
│   │
│   ├── connections/               # Contact requests between users (send → accept/decline → reveal)
│   │   ├── admin.py
│   │   ├── apps.py                # name="apps.connections", label="connections"
│   │   ├── migrations/
│   │   │   └── 0001_initial.py    # ContactRequest
│   │   ├── models.py              # ContactRequest (sender/recipient FKs via AUTH_USER_MODEL string ref)
│   │   ├── serializers.py         # Read (conditional contact_email reveal) + Create
│   │   ├── services.py            # send / list / get / accept / decline + email notify fns; calls users.services for username lookup
│   │   ├── tasks.py               # Celery tasks: notify recipient on send, notify sender on accept (emitted via on_commit)
│   │   ├── urls.py                # /requests/, /requests/<id>/, /requests/<id>/accept/, /decline/
│   │   ├── views.py               # ListCreate, Detail, Accept, Decline views
│   │   └── tests/
│   │       ├── __init__.py
│   │       ├── test_contact.py    # 14 tests: send, list, accept, decline, retrieve + reveal
│   │       └── test_notifications.py  # 5 tests: send/accept emails, decline silent, missing-request no-op
│   │
│   ├── listings/                  # Gig & audition board — listings + applications (Phase 3)
│   │   ├── admin.py
│   │   ├── apps.py                # name="apps.listings", label="listings"
│   │   ├── migrations/
│   │   │   ├── 0001_initial.py    # Listing
│   │   │   └── 0002_listingapplication.py  # ListingApplication (unique per listing+applicant)
│   │   ├── models.py              # Listing, ListingApplication (FKs via AUTH_USER_MODEL string ref)
│   │   ├── serializers.py         # Listing Read/Create/Update + Application Read (reveal-on-accept)/Create
│   │   ├── services.py            # listing CRUD (author-only) + apply/list/accept/decline + email notify fns
│   │   ├── tasks.py               # Celery tasks: notify author on apply, notify applicant on accept (on_commit)
│   │   ├── urls.py                # /, /<id>/, /<id>/apply/, /applications/, /applications/<id>/(accept|decline)
│   │   ├── views.py               # ListingListCreate/Detail/Apply + ApplicationList/Detail/Accept/Decline views
│   │   └── tests/
│   │       ├── __init__.py
│   │       ├── conftest.py        # author + listing fixtures, auth/make_user helpers
│   │       ├── test_listing.py    # 16 tests: CRUD happy + negatives, ownership, active-only browse, filters
│   │       └── test_application.py  # 20 tests: apply, list (in/out box), accept/decline, reveal, notifications
│   │
│   ├── bands/                     # Bands as group entities + member rosters (Phase 4, Block A)
│   │   ├── admin.py
│   │   ├── apps.py                # name="apps.bands", label="bands"
│   │   ├── migrations/
│   │   │   └── 0001_initial.py    # Band + BandMembership (unique per band+member)
│   │   ├── models.py              # Band, BandMembership (owner/member FKs via AUTH_USER_MODEL string ref)
│   │   ├── serializers.py         # Band Read (w/ accepted roster)/Create/Update + Membership Read (reveal-on-accept)/Invite
│   │   ├── services.py            # band CRUD (owner-only, slug derivation) + invite/list/accept/decline + email notify fns
│   │   ├── tasks.py               # Celery tasks: notify member on invite, notify owner on accept (on_commit)
│   │   ├── urls.py                # /, /<slug>/, /<slug>/invite/, /memberships/, /memberships/<id>/(accept|decline)
│   │   ├── views.py               # BandListCreate/Detail/Invite + MembershipList/Detail/Accept/Decline views
│   │   └── tests/
│   │       ├── __init__.py
│   │       ├── conftest.py        # owner + band fixtures, auth/make_user helpers
│   │       ├── test_band.py       # 14 tests: CRUD happy + negatives, slug derivation/collision, roster, browse/filters
│   │       └── test_membership.py  # 17 tests: invite, list, accept/decline, reveal, notifications
│   │
│   ├── engagements/               # Session-musician hire-intent marketplace (Phase 4, Block B)
│   │   ├── admin.py
│   │   ├── apps.py                # name="apps.engagements", label="engagements"
│   │   ├── migrations/
│   │   │   └── 0001_initial.py    # EngagementRequest (no unique — repeat hires allowed)
│   │   ├── models.py              # EngagementRequest (requester/musician FKs via AUTH_USER_MODEL string ref)
│   │   ├── serializers.py         # Read (reveal-on-accept/completed) + Create
│   │   ├── services.py            # send/list/get/accept/decline/complete + email notify fns
│   │   ├── tasks.py               # Celery tasks: notify musician on send, notify requester on accept (on_commit)
│   │   ├── urls.py                # /, /<id>/, /<id>/(accept|decline|complete)
│   │   ├── views.py               # EngagementListCreate/Detail/Accept/Decline/Complete views
│   │   └── tests/
│   │       ├── __init__.py
│   │       ├── conftest.py        # auth/make_user helpers
│   │       └── test_engagement.py  # 19 tests: send, list (in/out), accept/decline/complete, reveal, notifications
│   │
│   ├── venues/                    # User-owned venue profiles (Phase 4, Block C)
│   │   ├── admin.py
│   │   ├── apps.py                # name="apps.venues", label="venues"
│   │   ├── migrations/
│   │   │   └── 0001_initial.py    # Venue
│   │   ├── models.py              # Venue (owner FK via AUTH_USER_MODEL string ref)
│   │   ├── serializers.py         # Venue Read/Create/Update
│   │   ├── services.py            # venue CRUD (owner-only, slug derivation) + browse/filter
│   │   ├── urls.py                # /, /<slug>/
│   │   ├── views.py               # VenueListCreate/Detail views
│   │   └── tests/
│   │       ├── __init__.py
│   │       ├── conftest.py        # owner + venue fixtures, auth/make_user helpers
│   │       └── test_venue.py      # 15 tests: CRUD happy + negatives, slug derivation/collision, browse/filters
│   │
│   └── social/                    # Follow graph + activity feed (Phase 5, Blocks A+B)
│       ├── admin.py
│       ├── apps.py                # name="apps.social", label="social"
│       ├── migrations/
│       │   ├── 0001_initial.py    # Follow (unique edge + no-self-follow check constraint)
│       │   └── 0002_activity_feedentry_and_more.py  # Activity (event log) + FeedEntry (per-recipient inbox)
│       ├── models.py              # Follow, Activity (canonical log), FeedEntry (fan-out inbox)
│       ├── serializers.py         # Following/Follower Read + FeedEntry Read (flattens joined Activity)
│       ├── services.py            # follow/unfollow (+ backfill/prune emit) + record_activity/fan_out/backfill/prune/get_feed + Verb alias
│       ├── tasks.py               # Celery: fan_out_activity, backfill_feed, prune_feed (thin → services)
│       ├── management/
│       │   └── commands/
│       │       └── seed_demo_phase5.py  # Seeds demo-* data across Phase 5 (follows/feed/reviews); eager+dummy-email; --reset
│       ├── urls.py                # /feed/, /follow/<username>/, /following/, /followers/, /<username>/(followers|following)/
│       ├── views.py               # Feed + Follow + Following/Followers list + Public follower/following views
│       └── tests/
│           ├── __init__.py
│           ├── conftest.py        # alice + bob fixtures, auth/make_user helpers
│           ├── test_follow.py     # 14 tests: follow/idempotent/self/unknown, unfollow, lists, public lists, auth
│           └── test_feed.py       # 10 tests: fan-out, self-feed, band activity, ordering, backfill/prune, recording, rollback
│
│   # NOTE: listings.create_listing + bands.create_band call apps.social.services.record_activity
│   # (service-to-service, no model import) to emit feed activities.
│   #
│   ├── reviews/                    # Ratings + reviews, gated on completed engagements (Phase 5, Block C)
│   │   ├── admin.py
│   │   ├── apps.py                # name="apps.reviews", label="reviews"
│   │   ├── migrations/
│   │   │   └── 0001_initial.py    # Review (unique per author+context, rating-range + no-self checks)
│   │   ├── models.py              # Review (author/subject FKs; denormalized context_type/context_id — no cross-app FK)
│   │   ├── serializers.py         # ReviewCreate (write) + ReviewRead (public)
│   │   ├── services.py            # create_review (engagement-gated) + list_reviews_for + rating_summary
│   │   ├── urls.py                # /, /<username>/, /<username>/summary/
│   │   ├── views.py               # ReviewCreate + ReviewList + ReviewSummary views
│   │   └── tests/
│   │       ├── __init__.py
│   │       ├── conftest.py        # requester + musician fixtures, make_engagement/auth helpers
│   │       └── test_review.py     # 15 tests: gated create (happy/bidirectional/ineligible/dup/range), public list + summary
│
│   # NOTE: reviews.create_review gates via engagements.services.parties_of_completed_engagement
│   # (service-to-service, no model import) — a review needs a COMPLETED engagement between the two users.
│
│   # NOTE: musicians gained session-work intent fields (is_open_to_session_work,
│   # session_rate) in migration 0006 — see apps/musicians/tests/test_session_work.py (4 tests).
│
├── config/                        # Django project config (not an app)
│   ├── __init__.py                # Loads the Celery app so @shared_task binds
│   ├── celery.py                  # Celery app (Redis broker, autodiscovers tasks.py)
│   ├── asgi.py
│   ├── wsgi.py
│   ├── urls.py                    # Root URL conf — all routes wired here
│   └── settings/
│       ├── base.py                # Shared — all envs inherit from here
│       ├── local.py               # Dev: DEBUG=True, CORS open, human logs
│       └── production.py          # Prod: HTTPS, ALLOWED_HOSTS from env + ECS task IP (ALB health), SSM secrets
│
├── .github/
│   └── workflows/
│       └── ci.yml                 # Lint + type-check + migrate + pytest on every push
│
├── requirements/
│   └── base.txt                   # All dependencies pinned (uv pip freeze)
│
├── infra/                         # AWS infrastructure (Terraform) — see infra/README.md
│   ├── dns/                       # PERSISTENT stack: Route 53 zone + ACM cert (never destroy)
│   ├── terraform/                 # APP stack: VPC, ECR, RDS, ALB, ECS/Fargate, IAM, SSM secrets, logs
│   └── scripts/
│       ├── push-image.sh          # build linux/arm64 → push to ECR
│       └── run-migrations.sh      # one-off Fargate task: migrate + seed
│
├── conftest.py                    # Root pytest fixtures: api_client, user
├── tests/                         # Project-level tests not tied to one app
│   └── test_celery_wiring.py      # Celery app wiring (2.1)
├── .env                           # Git-ignored. Copy from .env.example.
├── .env.example                   # Committed template for all env vars.
├── .gitignore
├── .pre-commit-config.yaml        # Hooks: whitespace, yaml, detect-private-key, ruff
├── docker-compose.yml             # Postgres 16 + Redis 7 for local dev
├── Dockerfile                     # Multi-stage prod image (uv venv → slim runtime, gunicorn, non-root)
├── .dockerignore                  # Keeps build context lean; excludes .env, tests, .venv, docs
├── manage.py                      # Defaults to config.settings.local
├── pyproject.toml                 # ruff + mypy + pytest config
│
├── PROJECT.md                     # What/why + stack + AWS architecture
├── CLAUDE.md                      # Working rules, conventions, all known gotchas
├── DATAMODEL.md                   # All models — current and planned
├── ROADMAP.md                     # Phase plan and sub-step status
└── CODEBASE.md                    # This file
```

---

## Current API endpoints

Production base URL: **https://api.frikkinwave.com** (ECS Fargate + ALB + RDS, `ap-south-1`).

| Method | URL | Auth | Description |
|---|---|---|---|
| GET | `/api/health/` | None | Health check (AWS ALB exempt) |
| POST | `/api/auth/register/` | None | Create account, returns token pair |
| POST | `/api/auth/token/` | None | Login, returns token pair |
| POST | `/api/auth/token/refresh/` | Refresh token | Rotate refresh token |
| POST | `/api/auth/logout/` | Bearer | Blacklist refresh token |
| GET | `/api/auth/me/` | Bearer | Current user identity (id, email, username, date_joined) |
| GET | `/api/musicians/instruments/` | None | Full instrument catalogue (for profile-editor pickers) |
| GET | `/api/musicians/genres/` | None | Full genre catalogue (for profile-editor pickers) |
| GET | `/api/musicians/search/` | None | Semantic search (`?q=` NL query, `?limit=`, `?available=true`) — cosine kNN, ranked w/ similarity; drops results below `SEARCH_SIMILARITY_THRESHOLD` (default 0.4) |
| GET | `/api/musicians/profiles/` | None | List/filter profiles (cursor-paginated); filters incl. `?open_to_session=true` |
| GET | `/api/musicians/profiles/<username>/` | None | Public single profile by username (incl. `rating` `{average_rating, count}`) |
| GET | `/api/musicians/compatibility/<username>/` | Bearer | Cached gpt-4o-mini "why you might click" blurb between you and `<username>` |
| POST | `/api/musicians/profile/` | Bearer | Create musician profile (response incl. `rating`) |
| GET | `/api/musicians/profile/me/` | Bearer | Retrieve own profile (incl. `rating`) |
| PATCH | `/api/musicians/profile/me/` | Bearer | Partial update own profile (incl. `rating`) |
| GET | `/api/musicians/profile/coach/` | Bearer | Profile completeness score + field suggestions + LLM tip |
| POST | `/api/connections/requests/` | Bearer | Send a contact request (by recipient username) |
| GET | `/api/connections/requests/` | Bearer | List own requests (`?box=incoming\|outgoing`) |
| GET | `/api/connections/requests/<id>/` | Bearer | Retrieve a request you are party to |
| POST | `/api/connections/requests/<id>/accept/` | Bearer | Recipient accepts (reveals contact email) |
| POST | `/api/connections/requests/<id>/decline/` | Bearer | Recipient declines |
| GET | `/api/listings/` | None | Browse active listings (cursor-paginated); filter `?type=` / `?city=` / `?country=` |
| POST | `/api/listings/` | Bearer | Post a listing (gig / audition / venue) |
| GET | `/api/listings/<id>/` | None | Public single active listing |
| PATCH | `/api/listings/<id>/` | Bearer | Update own listing (author only) |
| DELETE | `/api/listings/<id>/` | Bearer | Soft-delete own listing (author only) |
| POST | `/api/listings/<id>/apply/` | Bearer | Apply to a listing |
| GET | `/api/listings/applications/` | Bearer | List own applications (`?box=incoming\|outgoing`) |
| GET | `/api/listings/applications/<id>/` | Bearer | Retrieve an application you are party to (reveal-on-accept) |
| POST | `/api/listings/applications/<id>/accept/` | Bearer | Listing author accepts (reveals contact email) |
| POST | `/api/listings/applications/<id>/decline/` | Bearer | Listing author declines |
| GET | `/api/bands/` | None | Browse active bands (cursor-paginated); filter `?city=` / `?country=` |
| POST | `/api/bands/` | Bearer | Create a band (caller becomes owner) |
| GET | `/api/bands/<slug>/` | None | Public band page (with accepted member roster) |
| PATCH | `/api/bands/<slug>/` | Bearer | Update own band (owner only) |
| DELETE | `/api/bands/<slug>/` | Bearer | Soft-delete own band (owner only) |
| POST | `/api/bands/<slug>/invite/` | Bearer | Owner invites a user (by username) to the band |
| GET | `/api/bands/memberships/` | Bearer | List the caller's own memberships / invites |
| GET | `/api/bands/memberships/<id>/` | Bearer | Retrieve a membership you are party to (reveal-on-accept) |
| POST | `/api/bands/memberships/<id>/accept/` | Bearer | Invited member accepts (reveals contact email) |
| POST | `/api/bands/memberships/<id>/decline/` | Bearer | Invited member declines |
| POST | `/api/engagements/` | Bearer | Send a hire request to a musician (by username) |
| GET | `/api/engagements/` | Bearer | List own hire requests (`?box=incoming\|outgoing`) |
| GET | `/api/engagements/<id>/` | Bearer | Retrieve a request you are party to (reveal-on-accept) |
| POST | `/api/engagements/<id>/accept/` | Bearer | Hired musician accepts (reveals contact email) |
| POST | `/api/engagements/<id>/decline/` | Bearer | Hired musician declines |
| POST | `/api/engagements/<id>/complete/` | Bearer | Either party marks an accepted request completed |
| GET | `/api/venues/` | None | Browse active venues (cursor-paginated); filter `?city=` / `?country=` |
| POST | `/api/venues/` | Bearer | Create a venue (caller becomes owner) |
| GET | `/api/venues/<slug>/` | None | Public venue page |
| PATCH | `/api/venues/<slug>/` | Bearer | Update own venue (owner only) |
| DELETE | `/api/venues/<slug>/` | Bearer | Soft-delete own venue (owner only) |
| POST | `/api/social/follow/<username>/` | Bearer | Follow a user (idempotent — re-follow is a 200 no-op) |
| DELETE | `/api/social/follow/<username>/` | Bearer | Unfollow a user (idempotent — 204 even if not following) |
| GET | `/api/social/following/` | Bearer | Users the caller follows (cursor-paginated) |
| GET | `/api/social/followers/` | Bearer | Users following the caller (cursor-paginated) |
| GET | `/api/social/<username>/following/` | None | Public list of who a user follows |
| GET | `/api/social/<username>/followers/` | None | Public list of a user's followers |
| GET | `/api/social/feed/` | Bearer | Activity feed — what followed users (+ self) did, newest first (cursor-paginated) |
| POST | `/api/reviews/` | Bearer | Leave a review (gated on a completed engagement; body: subject_username, engagement_id, rating, comment) |
| GET | `/api/reviews/<username>/` | None | Public list of reviews a user received (cursor-paginated) |
| GET | `/api/reviews/<username>/summary/` | None | Public `{average_rating, count}` for a user |
| GET | `/api/schema/` | None | OpenAPI 3.0 schema (YAML/JSON) |
| GET | `/api/docs/` | None | Swagger UI |

---

## Settings system

`manage.py` defaults to `config.settings.local`.
`wsgi.py` / `asgi.py` default to `config.settings.production`.
CI sets `DJANGO_SETTINGS_MODULE=config.settings.local` via env var.

---

## Adding a new app

```bash
python manage.py startapp <name> apps/<name>
```

Then:
1. In `apps/<name>/apps.py`: set `name = "apps.<name>"` and `label = "<name>"`
2. Add `"apps.<name>"` to `LOCAL_APPS` in `config/settings/base.py`
3. Create `apps/<name>/serializers.py`, `services.py`, `urls.py`
4. Wire URL include in `config/urls.py`
5. Create `apps/<name>/tests/` package with `__init__.py`, `conftest.py`
6. Update `DATAMODEL.md` + `ROADMAP.md`

---

## Adding a new endpoint

Pattern: **URL → View → Serializer → Service → Model**

1. `serializers.py` — define request/response shape (Read + Write pair)
2. `services.py` — business logic, DB queries
3. `views.py` — parse request, call service, return Response (no logic here)
4. `urls.py` — register URL (specific before catch-alls)
5. `config/urls.py` — include app URLs if new app
6. `tests/test_<feature>.py` — happy path + negatives

---

## Key conventions

### Authentication
All endpoints require JWT by default (`REST_FRAMEWORK` in `base.py`).
To make an endpoint public, explicitly set BOTH on the view:
```python
authentication_classes = [JWTAuthentication]
permission_classes = [AllowAny]
```
(See CLAUDE.md for the 401→403 gotcha explanation.)

### UUIDv7 primary keys
Copy the `_new_uuid()` pattern from any existing `models.py`:
```python
import uuid
import uuid6

def _new_uuid() -> uuid.UUID:
    return uuid6.uuid7()

class MyModel(models.Model):
    id = models.UUIDField(primary_key=True, default=_new_uuid, editable=False)
```

### Three-layer architecture
```
View → Service → Model
```
Views call services. Services call models. Never skip a layer.
No cross-app model imports — use `TYPE_CHECKING` guard for type hints only.

### Serializer pairs (read / write)
- `XxxReadSerializer` — nested objects, used in responses
- `XxxWriteSerializer` — flat IDs, used for create/update input
- View returns `ReadSerializer(result).data` after every mutation

### Tests
- Root `conftest.py` has `api_client` and `user` fixtures (available to all apps)
- App-level `tests/conftest.py` has app-specific fixtures
- All test classes decorated with `@pytest.mark.django_db`

---

## Running locally

```bash
docker compose up -d                     # Postgres 16 + Redis 7
source .venv/bin/activate
python manage.py migrate
python manage.py seed_music_data         # 44 instruments + 31 genres
python manage.py runserver

# Verify:
curl http://localhost:8000/api/health/   # → {"status": "ok"}
# http://localhost:8000/api/docs/        → Swagger UI
```

---

## Production container

`Dockerfile` builds the image deployed to ECS/Fargate (Phase 1, sub-steps 1.9+).

```bash
docker build -t frikkinwave-backend .

# Run it (health check needs no DB; full app needs DATABASE_URL → RDS):
docker run -p 8000:8000 \
  -e DJANGO_SECRET_KEY=... \
  -e DATABASE_URL=postgres://... \
  -e ALLOWED_HOSTS=api.frikkinwave.com \
  -e CORS_ALLOWED_ORIGINS=https://frikkinwave.com \
  frikkinwave-backend

curl http://localhost:8000/api/health/   # → {"status": "ok"}
```

Design notes:
- **Multi-stage:** builder installs deps into `/opt/venv` via uv; runtime stage copies only the venv + source → smaller image, no build tooling shipped.
- **`DJANGO_SETTINGS_MODULE=config.settings.production`** is baked in; served by **gunicorn** (sync workers, count via `WEB_CONCURRENCY`, default 3).
- **collectstatic runs at build time** with placeholder env vars (WhiteNoise manifest storage needs the files present in the image). Placeholders are never used at runtime.
- **Non-root** (`appuser`, uid 10001). Filesystem treated as ephemeral — all real storage is S3/RDS.
- **Migrations are NOT run on container start** — they run as a separate one-off ECS task (avoids races across concurrent Fargate tasks). Wired in 1.9/1.10.

---

## CI pipeline (GitHub Actions)

File: `.github/workflows/ci.yml`

Steps:
1. Checkout + Python 3.13 + uv install
2. `uv pip install --system -r requirements/base.txt`
3. `ruff check .`
4. `ruff format --check .`
5. `mypy apps/ config/` (continue-on-error)
6. `python manage.py check`
7. `python manage.py migrate`
8. `pytest`

Postgres 16 service container spins up automatically.

---

## Dependency management

```bash
uv pip install <package>
uv pip freeze > requirements/base.txt    # always update lockfile after install
```
