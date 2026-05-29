# CLAUDE.md — Working instructions for this repo

Read this file at the start of every session. It encodes all conventions and working rules for frikkinwave-backend.

---

## Project context

See `PROJECT.md` for what this is and why.
See `ROADMAP.md` for current phase and next sub-steps.
See `DATAMODEL.md` for current and planned data models.
See `CODEBASE.md` for directory structure and where things live.

---

## Working rules

1. **Plan before code** for any change touching 3+ files. State the plan, wait for confirmation.
2. **Ask for a commit message** before every commit. Draft a suggestion; let the user edit or approve it.
3. **Never add `Co-Authored-By: Claude`** to any commit message.
4. **Tests with the feature** — not later. Happy path + at least one negative path per new endpoint/service.
5. **Commit + push + watch CI** after each green sub-step.
6. **Be honest about gaps** at every milestone. Don't paper over uncertainty.
7. **One concept per teaching response** — conceptual altitude, not line-by-line dissection.
8. **Course-correct without ceremony** — if the prior approach was wrong, say so and fix it.

---

## Commit workflow

1. Draft a commit message and show it to the user.
2. Wait for the user to approve or edit it.
3. Only then run `git commit`.
4. Never use `--amend` unless the user explicitly asks.
5. Never use `--no-verify`.

---

## Scale constraints (baked in from day one)

This project is built for eventual 100M users / 1M concurrent. The monolith ships first, but every decision respects these four rules so service extraction later is a refactor, not a rewrite:

1. **No cross-app model imports.** `apps/musicians` never imports from `apps/connections/models.py`. Apps communicate through service function calls only. Today that's a Python call; tomorrow it's a network call — the interface doesn't change.

2. **Structured JSON logging always.** All log output is JSON. Unstructured text logs are useless on EKS + CloudWatch/Datadog at scale. Never use `print()` for debugging — use `logger = logging.getLogger(__name__)`.

3. **Stateless Django always.** Never write to local disk. All file storage goes to S3. ECS/EKS tasks are ephemeral — any task can be killed and replaced at any moment.

4. **Events for async work, not direct calls.** Profile saved → emit an internal event → Celery task handles it. The event shape today becomes the Kafka message schema when we extract services. Wire Celery tasks as event handlers, not as inline function calls from views.

---

## Architecture conventions

### Three-layer rule (strictly enforced)

```
View (apps/<app>/views.py)
  └── calls Service (apps/<app>/services.py)
        └── calls Model (apps/<app>/models.py)
```

- Views: parse request → call service → return Response. Nothing else.
- Services: all business logic, DB queries, external API calls.
- Models: field definitions, `__str__`, `Meta`. No business logic methods.

### Adding a new Django app

```bash
python manage.py startapp <name> apps/<name>
```

Then in `apps/<name>/apps.py`:
```python
class <Name>Config(AppConfig):
    name = "apps.<name>"
    label = "<name>"
```

Add `"apps.<name>"` to `LOCAL_APPS` in `config/settings/base.py`.

### URL pattern

All API routes live under `/api/`. Specific paths before catch-alls. Example:
```python
path("api/users/me/", ...),      # specific first
path("api/users/<slug>/", ...),  # catch-all after
```

### UUIDv7 primary keys

Every model gets:
```python
import uuid
import uuid6

def _new_uuid() -> uuid.UUID:
    return uuid6.uuid7()

class MyModel(models.Model):
    id = models.UUIDField(primary_key=True, default=_new_uuid, editable=False)
```

### DRF authentication gotcha

On `AllowAny` views, DRF silently demotes 401 → 403 unless you explicitly set:
```python
authentication_classes = [JWTAuthentication]
permission_classes = [AllowAny]
```

---

## Local development

### Requirements

- Python 3.13
- Docker Desktop (for Postgres + Redis)
- uv (`brew install uv`)

### Setup

```bash
cd frikkinwave-backend
uv venv --python 3.13
source .venv/bin/activate
uv pip install -r requirements/base.txt
cp .env.example .env        # fill in DJANGO_SECRET_KEY
docker compose up -d        # starts Postgres + Redis
python manage.py migrate
python manage.py runserver
```

### Verify it's working

```
GET http://localhost:8000/api/health/     → {"status": "ok"}
GET http://localhost:8000/api/docs/       → Swagger UI
GET http://localhost:8000/api/schema/     → OpenAPI JSON
```

### Running tests

```bash
pytest                          # all tests
pytest apps/users/              # specific app
pytest -k "test_login"          # specific test
```

---

## Tooling

### Ruff (lint + format)

```bash
ruff check .          # lint
ruff check --fix .    # lint + auto-fix
ruff format .         # format
```

Config lives in `pyproject.toml` under `[tool.ruff]`.

### Mypy

```bash
mypy apps/ config/
```

Config lives in `pyproject.toml` under `[tool.mypy]`.
**Pinned:** `mypy<2.0`, `django-stubs<6.0` — do not upgrade without checking compatibility.

### Pre-commit

Hooks run automatically on every commit: trailing whitespace, end-of-file, yaml check, detect-private-key, ruff lint, ruff format.

Manual run: `pre-commit run --all-files`

---

## Known gotchas

- **mypy + django-stubs version pins:** `mypy<2.0` and `django-stubs<6.0`. mypy 2.0 + django-stubs 6.x are incompatible. Do not upgrade.
- **RUF012 is globally ignored — do NOT add `ClassVar` to satisfy it.** The rule (mutable class defaults must be `ClassVar`) fires on Django `Meta` (`ordering`/`constraints`), `REQUIRED_FIELDS`, DRF view `authentication_classes`/`permission_classes`, admin attrs, and `ModelSerializer.Meta.fields` — all framework-defined slots, not accidental shared state. Annotating them added noise for zero safety, and `ClassVar` on `ModelSerializer.Meta.fields` actually crashes the django-stubs plugin. So `RUF012` lives in the top-level `ignore` list in `pyproject.toml`; leave these attributes as plain assignments.
  - Unrelated to RUF012 but nearby: `ModelAdmin[T]` generic is stubs-only — not subscriptable at runtime; suppressed via `disable_error_code = ["type-arg"]` mypy override for `apps.*.admin`.
- **Cross-app type references in services:** Import concrete model types under `TYPE_CHECKING` guard only (`if TYPE_CHECKING: from apps.users.models import User`). Use the type in annotations freely — no runtime coupling. In views, use `cast(User, request.user)` since `IsAuthenticated` guarantees a concrete user.
- **environ missing stubs:** `django-environ` has no type stubs. Add `environ.*` to `ignore_missing_imports` in pyproject.toml.
- **M2M with through model — model ordering:** Define the through model (e.g. `MusicianInstrument`) *before* the model that declares the M2M field. Use a string FK (`"MusicianProfile"`) in the through model to avoid a circular reference. This lets the M2M field use a direct `through=MusicianInstrument` reference and mypy resolves the type cleanly — no `Any` needed.
- **`Model.save()` override + django-stubs:** Overriding `save()` with `*args, **kwargs` causes `arg-type` errors because django-stubs types `save()` with concrete keyword parameters. Avoid save() overrides where possible — set slugs / defaults explicitly in serializers and management commands instead.
- **factory-boy + mypy strict:** Use typed wrapper helpers with `cast()` — bare factory calls fail strict mode.
- **DRF 401→403:** See architecture conventions above.
- **URL ordering:** Specific paths before catch-alls always.
- **UUIDv7:** `uuid.uuid7()` is Python 3.14+. We use the `uuid6` backport. Upgrade to stdlib when on 3.14+.
- **WhiteNoise + collectstatic:** Must run `collectstatic` at Dockerfile build time with placeholder env vars.
- **JWT refresh rotation:** Requires `rest_framework_simplejwt.token_blacklist` in `INSTALLED_APPS` AND its migrations applied. Both are already wired.
- **Custom User model migrations:** Never add a FK to `AUTH_USER_MODEL` before the users migration exists. Always `makemigrations users` first.
- **AWS ECS health check:** `SECURE_REDIRECT_EXEMPT = [r"^api/health/$"]` is in production.py — keeps the ALB health check from being 301'd.

---

## Environment variables

See `.env.example` for the full list. Never commit `.env`.

Critical ones:
- `DJANGO_SECRET_KEY` — required in all environments
- `DATABASE_URL` — postgres connection string
- `DJANGO_SETTINGS_MODULE` — set to `config.settings.local` for dev, `config.settings.production` for prod
