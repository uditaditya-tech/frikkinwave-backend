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
4. **"Commit" means commit + push.** When the user approves a commit, run `git commit` then `git push` automatically — no need to ask separately.
5. Never use `--amend` unless the user explicitly asks.
6. Never use `--no-verify`.

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

### Running the matching evals (Phase 2.8)

Quality measurement against the real model — **needs a real key, makes live API
calls, costs a little**. Not in CI (the deterministic harness test covers wiring
there). Seeds a golden set, embeds + searches + blurbs, prints a JSON report,
and rolls the DB back so nothing persists:

```bash
OPENAI_API_KEY=sk-... python manage.py eval_matching
# → {"retrieval": {"cases": 7, "recall@1": ..., "recall@3": ..., "mrr": ...},
#    "blurbs": {"pairs": 2, "grounding_rate": ...}}
```

Golden dataset + metrics live in `apps/musicians/evals/`. The CI test in
`tests/test_evals.py` runs the same `run_matching_eval()` with a deterministic
fake embedder (token-overlap vectors) so retrieval ranking is meaningful without
a key.

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
- **AWS ECS health check:** `SECURE_REDIRECT_EXEMPT = [r"^api/health/$"]` is in production.py — keeps the ALB health check from being 301'd. Also: the ALB health check hits the container with the **task's private IP** as the Host header, so production.py appends that IP (from the ECS metadata endpoint) to `ALLOWED_HOSTS`, and sets `SECURE_PROXY_SSL_HEADER` so HTTPS-behind-ALB doesn't redirect-loop. Don't remove either.
- **Celery (Phase 2):** The app lives in `config/celery.py` and is imported in `config/__init__.py`; tasks go in each app's `tasks.py` (auto-discovered). Two gotchas:
  - **Eager mode in tests:** Celery reads `CELERY_TASK_ALWAYS_EAGER` from Django settings *once at finalize*, and Django settings load before any conftest body runs — so setting `app.conf.task_always_eager` from a fixture (or `os.environ` in conftest) is too late, and `.delay().get()` blocks on the live broker with no worker (hangs the suite). Instead `config/settings/local.py` flips eager on when `PYTEST_VERSION` is set (pytest exports it at startup). Plain `runserver` keeps eager off and uses the real Redis broker. Don't try to toggle eager from conftest/fixtures.
  - **mypy:** `@app.task` / `@shared_task` are untyped in the stubs, tripping `untyped-decorator` under strict mode. Suppressed via a `disable_error_code = ["untyped-decorator", "misc"]` override scoped to `config.celery` + `apps.*.tasks` in pyproject.toml. Task bodies stay typed.
  - **Emit events with `transaction.on_commit`:** services enqueue tasks via `transaction.on_commit(lambda: my_task.delay(...))`, not a bare `.delay()` — so a rolled-back transaction never enqueues a task pointing at a phantom row. **In tests** the `db` fixture's outer transaction never commits, so those callbacks don't fire on their own: wrap the action in `with django_capture_on_commit_callbacks(execute=True):` to run them. Import the task *inside* the service function (local import) to avoid a `tasks ↔ services` import cycle.
- **pgvector (Phase 2, done in 2.3):** RDS Postgres 16 supports it, but the `vector` extension must be enabled (`CREATE EXTENSION vector`) **before** any `VectorField` migration — `makemigrations` does NOT add this, so hand-edit the migration to put pgvector's `VectorExtension()` as the **first** operation (see `apps/musicians/migrations/0004_profileembedding.py`). The RDS master user can run it. `pgvector` is in requirements; docker-compose + CI both use the `pgvector/pgvector:pg16` image.
  - **`HnswIndex` needs `django.contrib.postgres`** in `INSTALLED_APPS` (already added) — without it `manage.py check` fails `postgres.E005`.
  - **Async-on-write vs sync-on-read:** embeddings (2.4) are generated by a Celery task on profile *save* (a write event → background). Compatibility blurbs (2.6) are generated *synchronously* in the request on a cache miss, because the caller needs the text in the response and there's no write to react to — then cached in `CompatibilityBlurb` (canonical unordered pair). Don't reflexively Celery-ify on-demand reads.
  - **OpenAI access (2.4+):** all calls go through `apps/musicians/openai_client.py` (`OpenAIClient.embed` + `.complete`, cached `get_openai_client()`) — never `import openai` elsewhere. The client converts any `openai.OpenAIError` into a domain `OpenAIUnavailableError` so services degrade **without** importing the SDK's exception types: search → `[]`, compatibility → `None` (→ 503), coach → null `tip` (rules still returned). Treat "no key" and "API down/quota-exhausted" identically — an upstream failure must never 500 a user request. Tests patch `get_openai_client` to inject a fake, so CI needs **no key and makes no network calls**. `OPENAI_API_KEY` defaults to `""`; the embedding task **skips + logs** when it's empty (profiles still save). The embedding pipeline also **skips the OpenAI call when `embedding_text` is unchanged** (so toggling `is_available` costs nothing) — `build_embedding_text` must stay deterministic for that dedupe to hold.
  - **Search similarity floor (`SEARCH_SIMILARITY_THRESHOLD`, default 0.4):** `search_profiles` drops results scoring below it (similarity = 1 − cosine distance). **0.8 is unusable** for `text-embedding-3-small` — measured prod scores: strong matches ~0.72–0.78, moderate ~0.45–0.55, noise <0.3 (a near-verbatim bio query topped out ~0.55, because `build_embedding_text` blends bio+instruments+genres+city, diluting short queries). The eval runner passes `similarity_threshold=0.0` so recall measures ranking, not the gate. Don't raise the default toward 0.8 without re-measuring against the live model — it silently returns `[]`.
  - **Local collation mismatch after the image swap:** the old `postgres_data` volume was created by the `postgres:16` image, whose libc collation differs from the `pgvector/pgvector:pg16` image. Postgres then refuses to `CREATE DATABASE` (incl. the `test_*` DB), erroring `template database "template1" has a collation version mismatch`. Fix once, locally: `docker compose exec -T db psql -U postgres -d postgres -c 'ALTER DATABASE template1 REFRESH COLLATION VERSION;'` (repeat for `postgres` and `frikkinwave`). CI is unaffected — it builds a fresh DB from the pgvector image. Alternatively `docker compose down -v` to recreate the volume clean (drops local data).

---

## Infrastructure (AWS) — see `infra/README.md`

- **Two Terraform stacks.** `infra/dns/` is PERSISTENT (Route 53 zone + ACM cert) — **never `terraform destroy` it** or the GoDaddy NS delegation breaks. `infra/terraform/` is the disposable app stack (VPC, ALB, ECS/Fargate, RDS, SSM secrets); destroy/apply freely. The app stack discovers the zone + cert via `data` sources.
- **The app stack is currently DEPLOYED and live** — **Phase 5 Blocks A–C are in prod** (`https://api.frikkinwave.com/api/health/` → 200 over HTTPS), running image `668662f` (= `main`; deployed 2026-06-09 via the rolling "Updating the running app" flow — Phase 5 A–C went out as `b21ba83`, then no-migration follow-ups `46ed564` (review-rating profile embed) and `668662f` (the `seed_demo_phase5` demo-data command, behavior-neutral); flow is `push-image.sh <sha>` → `terraform apply -var image_tag=<sha>` → `run-migrations.sh` only when there are new migrations; RDS untouched, only the web+worker task defs got new revisions — currently web rev 18, worker rev 12): web + Celery worker + ElastiCache Redis + RDS with pgvector. Live & verified: gig/audition board (`/api/listings/`), bands + member rosters (`/api/bands/`), session-musician marketplace (`/api/engagements/` + profile `?open_to_session`), venue profiles (`/api/venues/`), **the social follow graph (`/api/social/follow/…`, `/following/`, `/followers/`), the activity feed (`/api/social/feed/` — fan-out-on-write; the worker handles `social.fan_out_activity`/`backfill_feed`/`prune_feed`), and ratings+reviews (`/api/reviews/`, gated on completed engagements)**. Phase 5 Block D (real-time messaging) is NOT built or deployed. Migrations applied through `social.0002` + `reviews.0001`. DB holds reference seed data, a few real musician profiles with embeddings (e.g. `jazzcat`, `udit94`), **plus a `demo-*` Phase 5 demo dataset** (30 musicians, 870 follows, 360 completed engagements, 720 reviews — every Phase 5 list paginates; any `demo-NN` logs in with `DemoPass123!`) seeded via `python manage.py seed_demo_phase5` (a one-off ECS task; `--reset` wipes & reseeds the `demo-*` namespace). Semantic search returns both real and demo profiles. There is **no CD** (decision recorded in infra/README): the destroy↔restore cycle is **scripted** — `./infra/scripts/bring-up.sh` (ECR→push→apply→migrate→verify; auto-tags current git HEAD + auto-restores the latest manual snapshot; `--fresh` for an empty DB) and `./infra/scripts/teardown.sh` (destroy + final RDS snapshot by default; `--wipe` to skip the snapshot). Both are idempotent — re-run after a transient mid-apply failure. Manual equivalents and the `db_snapshot_identifier` restore var are documented in infra/README. A fresh RDS has no schema, so a from-scratch bring-up **always** runs `run-migrations.sh` (the script does this). DNS + cert always survive a teardown. Retained snapshots accumulate per teardown (e.g. `frikkinwave-prod-final-<rand>`). *(Update this line when the deployment state changes.)*
- **Region** `ap-south-1` (Mumbai). **Migrations run as a one-off ECS task** (`infra/scripts/run-migrations.sh`), never on container start. **Secrets** live in SSM Parameter Store, injected via the task def `secrets` block. **Images** are `linux/arm64` (Graviton Fargate).

---

## Environment variables

See `.env.example` for the full list. Never commit `.env`.

Critical ones:
- `DJANGO_SECRET_KEY` — required in all environments
- `DATABASE_URL` — postgres connection string
- `DJANGO_SETTINGS_MODULE` — set to `config.settings.local` for dev, `config.settings.production` for prod
- `SEARCH_SIMILARITY_THRESHOLD` — semantic-search relevance floor (default `0.4`, `0` disables). Live-tunable in prod via the ECS task-def env (`terraform apply -var search_similarity_threshold=N`), no image rebuild.
