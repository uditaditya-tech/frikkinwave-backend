# Codebase guide — frikkinwave backend

Read this to understand where things live and how to navigate the repo.

---

## Directory structure

```
frikkinwave-backend/
│
├── apps/                          # All Django apps live here
│   ├── __init__.py
│   └── users/                     # Custom auth user model
│       ├── admin.py
│       ├── apps.py                # AppConfig — name="apps.users", label="users"
│       ├── migrations/
│       │   └── 0001_initial.py
│       ├── models.py              # User model only
│       ├── services.py            # Business logic (to be created)
│       ├── tests.py
│       └── views.py
│
├── config/                        # Django project config (not an app)
│   ├── __init__.py
│   ├── asgi.py                    # defaults to production settings
│   ├── wsgi.py                    # defaults to production settings
│   ├── urls.py                    # root URL conf
│   └── settings/
│       ├── __init__.py
│       ├── base.py                # Shared settings — all envs inherit from here
│       ├── local.py               # Dev overrides (DEBUG=True, SQLite ok, CORS open)
│       └── production.py          # Prod overrides (HTTPS, ALLOWED_HOSTS from env)
│
├── .github/
│   └── workflows/
│       └── ci.yml                 # Lint + type-check + migrate + pytest
│
├── requirements/
│   └── base.txt                   # All dependencies pinned (uv pip freeze)
│
├── .env                           # Git-ignored. Copy from .env.example.
├── .env.example                   # Committed. Template for all env vars.
├── .gitignore
├── .pre-commit-config.yaml        # Hooks: whitespace, yaml, detect-private-key, ruff
├── docker-compose.yml             # Postgres 16 + Redis 7 for local dev
├── manage.py                      # defaults to config.settings.local
├── pyproject.toml                 # ruff + mypy + pytest config
│
├── PROJECT.md                     # What this project is and why
├── CLAUDE.md                      # Working instructions for Claude (this project's rules)
├── DATAMODEL.md                   # All models — current and planned
├── ROADMAP.md                     # Phase plan and current status
└── CODEBASE.md                    # This file
```

---

## Settings system

`manage.py` defaults to `config.settings.local`.
`wsgi.py` / `asgi.py` default to `config.settings.production`.
CI sets `DJANGO_SETTINGS_MODULE=config.settings.local` via env var.

**To override on the command line:**
```bash
python manage.py migrate --settings=config.settings.production
```

**Settings load order:**
1. `base.py` runs first — reads `.env` via `django-environ`
2. `local.py` or `production.py` imports `from .base import *` and overrides as needed

---

## Adding a new app

```bash
python manage.py startapp <name> apps/<name>
```

Then:
1. In `apps/<name>/apps.py` set `name = "apps.<name>"` and `label = "<name>"`
2. Add `"apps.<name>"` to `LOCAL_APPS` in `config/settings/base.py`
3. Create `apps/<name>/services.py` (empty is fine)
4. Add URL include to `config/urls.py`
5. Update `DATAMODEL.md` with the new models
6. Update `ROADMAP.md` sub-steps

---

## Adding a new endpoint

Pattern: **URL → View → Service → Model**

1. `apps/<app>/views.py` — parse request, call service, return Response
2. `apps/<app>/services.py` — business logic, DB queries
3. `config/urls.py` — register the URL (specific paths before catch-alls)
4. `apps/<app>/serializers.py` — DRF serializer for request/response shape
5. Test in `apps/<app>/tests.py` — happy path + at least one negative path

---

## Key conventions

### Authentication
All endpoints require JWT by default (set in `REST_FRAMEWORK` in `base.py`).
To make an endpoint public:
```python
authentication_classes = [JWTAuthentication]
permission_classes = [AllowAny]
```
(Both must be set — see CLAUDE.md gotchas.)

### UUIDv7 primary keys
Every model uses the `_new_uuid` pattern from `apps/users/models.py`.
Import `uuid6` and define `_new_uuid()` in each models file.

### Services pattern
```python
# apps/users/services.py

def get_user_by_email(email: str) -> User:
    return User.objects.get(email=email)
```

Views call services. Services call models. Never skip a layer.

---

## Running locally

```bash
docker compose up -d          # Postgres + Redis
source .venv/bin/activate
python manage.py migrate
python manage.py runserver

# Verify:
# http://localhost:8000/api/health/   → {"status": "ok"}
# http://localhost:8000/api/docs/     → Swagger UI
```

---

## CI pipeline (GitHub Actions)

File: `.github/workflows/ci.yml`

Steps in order:
1. Checkout + setup Python 3.13 + install uv
2. `uv pip install --system -r requirements/base.txt`
3. `ruff check .`
4. `ruff format --check .`
5. `mypy apps/ config/` (continue-on-error until stubs fully configured)
6. `python manage.py check`
7. `python manage.py migrate`
8. `pytest`

Postgres 16 service spins up as a GitHub Actions service container.

---

## Dependency management

Using `uv` (not pip directly):
```bash
uv pip install <package>           # install
uv pip freeze > requirements/base.txt   # update lockfile
```

After any install, always update `requirements/base.txt`.
