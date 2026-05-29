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
│   │   ├── services.py            # register_user()
│   │   ├── urls.py                # /register/, /logout/
│   │   ├── views.py               # RegisterView, LogoutView
│   │   └── tests/
│   │       ├── __init__.py
│   │       ├── conftest.py        # users-app-specific fixtures (currently empty)
│   │       └── test_auth.py       # 15 tests: register, login, refresh, logout
│   │
│   └── musicians/                 # Musician profiles, instruments, genres
│       ├── admin.py
│       ├── apps.py                # name="apps.musicians", label="musicians"
│       ├── migrations/
│       │   ├── 0001_initial.py    # MusicianProfile
│       │   └── 0002_*.py          # Instrument, Genre, MusicianInstrument, M2M fields
│       ├── models.py              # Instrument, Genre, MusicianInstrument, MusicianProfile
│       ├── serializers.py         # Read + Write serializers for profiles
│       ├── services.py            # create_profile(), update_profile()
│       ├── urls.py                # /profile/, /profile/me/
│       ├── views.py               # ProfileCreateView, ProfileMeView
│       ├── management/
│       │   └── commands/
│       │       └── seed_music_data.py   # Seeds 44 instruments + 31 genres
│       └── tests/
│           ├── __init__.py
│           ├── conftest.py        # instrument, genre, profile fixtures
│           └── test_profile.py    # 13 tests: create, retrieve, update
│
├── config/                        # Django project config (not an app)
│   ├── __init__.py
│   ├── asgi.py
│   ├── wsgi.py
│   ├── urls.py                    # Root URL conf — all routes wired here
│   └── settings/
│       ├── base.py                # Shared — all envs inherit from here
│       ├── local.py               # Dev: DEBUG=True, CORS open, human logs
│       └── production.py          # Prod: HTTPS, ALLOWED_HOSTS from env, AWS ALB
│
├── .github/
│   └── workflows/
│       └── ci.yml                 # Lint + type-check + migrate + pytest on every push
│
├── requirements/
│   └── base.txt                   # All dependencies pinned (uv pip freeze)
│
├── conftest.py                    # Root pytest fixtures: api_client, user
├── .env                           # Git-ignored. Copy from .env.example.
├── .env.example                   # Committed template for all env vars.
├── .gitignore
├── .pre-commit-config.yaml        # Hooks: whitespace, yaml, detect-private-key, ruff
├── docker-compose.yml             # Postgres 16 + Redis 7 for local dev
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

| Method | URL | Auth | Description |
|---|---|---|---|
| GET | `/api/health/` | None | Health check (AWS ALB exempt) |
| POST | `/api/auth/register/` | None | Create account, returns token pair |
| POST | `/api/auth/token/` | None | Login, returns token pair |
| POST | `/api/auth/token/refresh/` | Refresh token | Rotate refresh token |
| POST | `/api/auth/logout/` | Bearer | Blacklist refresh token |
| POST | `/api/musicians/profile/` | Bearer | Create musician profile |
| GET | `/api/musicians/profile/me/` | Bearer | Retrieve own profile |
| PATCH | `/api/musicians/profile/me/` | Bearer | Partial update own profile |
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
