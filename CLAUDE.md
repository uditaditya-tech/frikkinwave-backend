# CLAUDE.md — Working instructions for this repo

Read this file at the start of every session. It encodes all conventions and working rules for frikkinwave-backend.

---

## Project context

See `PROJECT.md` for what this is and why.
See `ROADMAP.md` for current phase and next sub-steps.
See `DATAMODEL.md` for current and planned data models.
See `CODEBASE.md` for directory structure and where things live.
See `MICROSERVICES.md` for the service-extraction target architecture and scaling path.
See `KAFKA.md` for the in-progress Kafka migration — **stages 0-2 DONE, 3-5 deferred**.

**Two services are extracted and live**: `apps/notifications` and `apps/search`. Each has its own
queue, its own Deployment, no cross-app model imports, and self-contained event payloads. They still
share the image and the database — the *contract* is cut, the packaging is not. The groundwork
underneath them (transactional outbox in `apps/events`, the `UserRef` DTO boundary, the denormalized
rating rollup, the guardrail tests in `tests/test_architecture.py`) is all in place.

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

3. **Stateless Django always.** Never write to local disk. All file storage goes to S3. Pods are ephemeral — any pod can be killed and replaced at any moment.

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
`apps/musicians/tests/test_evals.py` runs the same `run_matching_eval()` with a deterministic
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
- **Cross-app identity lookups return a DTO, not a model.** Use `apps.users.services.get_user_ref(username=...)` → a frozen `UserRef(id, username, email)`. `get_user_by_username()` returns the `User` **model** and is now internal to the users app — other apps must not call it. Assign foreign keys **by id** (`member_id=ref.id`), and compare identity with `ref.id == other.pk`. Rationale: an ORM instance cannot cross a service boundary, so the contract has to be serializable from day one. Views import `User` under `TYPE_CHECKING` and use `cast("User", request.user)` (string form, so the name is never evaluated at runtime).
- **Architectural rules are enforced by tests, not discipline.** `tests/test_architecture.py` fails the build on: a runtime cross-app model import, use of `get_user_by_username` outside the users app, a service calling `.delay()`/`.apply_async()` instead of `publish()`, a registered topic with no consumer task, or a mutable/non-serializable `UserRef`. If you're adding a legitimate exception, exempt it explicitly there with a reason — don't weaken the rule.
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
- **Health-check settings are load-bearing.** `SECURE_REDIRECT_EXEMPT = [r"^api/health/$"]` in production.py keeps probes from being 301'd, and `SECURE_PROXY_SSL_HEADER` stops HTTPS-behind-ALB redirect-looping. Don't remove either. The Host-header half is `POD_IP` — see the infrastructure section.
- **Celery (Phase 2):** The app lives in `config/celery.py` and is imported in `config/__init__.py`; tasks go in each app's `tasks.py` (auto-discovered). Two gotchas:
  - **Eager mode in tests:** Celery reads `CELERY_TASK_ALWAYS_EAGER` from Django settings *once at finalize*, and Django settings load before any conftest body runs — so setting `app.conf.task_always_eager` from a fixture (or `os.environ` in conftest) is too late, and `.delay().get()` blocks on the live broker with no worker (hangs the suite). Instead `config/settings/local.py` flips eager on when `PYTEST_VERSION` is set (pytest exports it at startup). Plain `runserver` keeps eager off and uses the real Redis broker. Don't try to toggle eager from conftest/fixtures.
  - **mypy:** `@app.task` / `@shared_task` are untyped in the stubs, tripping `untyped-decorator` under strict mode. Suppressed via a `disable_error_code = ["untyped-decorator", "misc"]` override scoped to `config.celery` + `apps.*.tasks` in pyproject.toml. Task bodies stay typed.
  - **Emit events through the transactional outbox — NOT `.delay()`.** Services call `apps.events.services.publish(topic=..., payload={...})` **inside their transaction**; they must never call `my_task.delay(...)` directly. The event row and the state change then commit together, so a rollback discards the event and a crash after COMMIT still leaves it durably recorded (the old `on_commit(... .delay())` pattern lost the event in that window). A relay dispatches pending events: `publish()` fires a best-effort nudge on commit, and `manage.py relay_outbox` (a scheduled sweep) is what makes delivery *guaranteed*. Rules that follow from this:
    - **Payload keys must exactly match the handler task's kwargs** — the relay passes `payload` straight through as `kwargs`.
    - **Register the topic** in `apps/events/registry.py` (`topic -> Celery task name`). The relay resolves handlers **by name**, so no app ever imports another app's tasks. A topic with no consumer is parked with `last_error`, never silently dropped.
    - **Consumers must be idempotent** — delivery is at-least-once. Prefer recomputing from source; where a create is unavoidable, key it on the event id (see `fan_out_activity`, which uses `event_id` as the `Activity` PK so redelivery cannot duplicate a post in every follower's feed).
    - **Notifications are an extracted service — keep their payloads self-contained.** `apps/notifications` consumes 8 topics and imports **no other app and no models**; producers publish the facts the email needs (recipient address, names, titles), never an id for the consumer to re-read. A test enforces the no-import rule, because this boundary erodes silently. When adding a notification: add a renderer in `apps/notifications/renderers.py` keyed by topic, a task in its `tasks.py`, and publish the full payload from the producer. **Check nullability of every field you move into a payload** — building it in the producer runs it in the request path, so a field that used to crash harmlessly in a Celery retry now 500s the user (this is exactly what `engagement.proposed_date` did).
    - **Search is an extracted service — it returns ids, never ORM objects.** `apps/search` owns `ProfileEmbedding` and the vector query; `search_profiles` in musicians calls it, gets `[(profile_id, similarity)]`, and hydrates from its own tables preserving order. `profile_id` is a **bare UUID, not a ForeignKey** — a FK is a promise both rows share a database, which is exactly the coupling an extraction cannot keep. Consequences that follow:
      - **`is_available` is REPLICATED onto the embedding row** and is eventually consistent. The availability filter must run inside the same query as the nearest-neighbour scan; filter afterwards and a caller asking for 20 results silently gets 9.
      - **No FK means no cascade delete.** A search hit can reference a profile that no longer exists; `search_profiles` skips and logs those. Deleting a profile must call `apps.search.services.remove_profile` — **there is no profile-deletion path today**, so that call site does not exist yet; the function is tested and waiting for whoever adds one.
      - **`build_embedding_text` stays in musicians** (it needs the instruments/genres relations) and must stay deterministic — the re-embed skip compares it, so drift means paying OpenAI on every save. Only its *output* crosses, on the `profile.updated` payload.
      - **Two modules resolve an OpenAI client now** (`apps.search.services` and `apps.musicians.services`). A test that patches only one lets the other make a **real API call** — this happened during the extraction and only surfaced because the fake key 401'd. With a real key in the environment it would have silently spent money in CI. Patch every module that imports `get_openai_client`.
      - **`VectorExtension()` lives in `apps/search/migrations/0001`,** not inherited from musicians. Migration order across apps follows the dependency graph, so nothing guaranteed musicians ran first and a fresh database failed with `type "vector" does not exist`. Owning it is also correct for the day search gets its own database.
    - **Celery queues are split, and a mis-routed task fails silently.** `CELERY_TASK_ROUTES` sends `notifications.*` to the `notifications` queue (its own Deployment); everything else runs on `celery` (the general worker). A task routed to a queue no worker consumes is enqueued and **never executed, with no error anywhere** — so `tests/test_architecture.py` asserts every registered handler lands on a queue some Deployment in the Helm chart is started with. Adding a queue means adding a worker to the chart, not just a route.
    - **In tests** the `db` fixture's outer transaction never commits, so the nudge doesn't fire on its own: wrap the action in `with django_capture_on_commit_callbacks(execute=True):`, or call `relay_pending()` directly.
    - **The relay must not use `celery_app.send_task`** — it bypasses `task_always_eager` and would make every eager context (the test suite, `seed_demo_phase5`) silently skip consumers. Dispatch via the task registry + `apply_async` (see `apps/events/services._dispatch`).
- **pgvector (Phase 2, done in 2.3):** RDS Postgres 16 supports it, but the `vector` extension must be enabled (`CREATE EXTENSION vector`) **before** any `VectorField` migration — `makemigrations` does NOT add this, so hand-edit the migration to put pgvector's `VectorExtension()` as the **first** operation (see `apps/musicians/migrations/0004_profileembedding.py`). The RDS master user can run it. `pgvector` is in requirements; docker-compose + CI both use the `pgvector/pgvector:pg16` image.
  - **`HnswIndex` needs `django.contrib.postgres`** in `INSTALLED_APPS` (already added) — without it `manage.py check` fails `postgres.E005`.
  - **Async-on-write vs sync-on-read:** embeddings (2.4) are generated by a Celery task on profile *save* (a write event → background). Compatibility blurbs (2.6) are generated *synchronously* in the request on a cache miss, because the caller needs the text in the response and there's no write to react to — then cached in `CompatibilityBlurb` (canonical unordered pair). Don't reflexively Celery-ify on-demand reads.
  - **OpenAI access (2.4+):** all calls go through `apps/ai/client.py` (`OpenAIClient.embed` + `.complete`, cached `get_openai_client()`) — never `import openai` elsewhere. The client converts any `openai.OpenAIError` into a domain `OpenAIUnavailableError` so services degrade **without** importing the SDK's exception types: search → `[]`, compatibility → `None` (→ 503), coach → null `tip` (rules still returned). Treat "no key" and "API down/quota-exhausted" identically — an upstream failure must never 500 a user request. Tests patch `get_openai_client` to inject a fake, so CI needs **no key and makes no network calls**. `OPENAI_API_KEY` defaults to `""`; the embedding task **skips + logs** when it's empty (profiles still save). The embedding pipeline also **skips the OpenAI call when `embedding_text` is unchanged** (so toggling `is_available` costs nothing) — `build_embedding_text` must stay deterministic for that dedupe to hold.
  - **Search similarity floor (`SEARCH_SIMILARITY_THRESHOLD`, default 0.4):** `search_profiles` drops results scoring below it (similarity = 1 − cosine distance). **0.8 is unusable** for `text-embedding-3-small` — measured prod scores: strong matches ~0.72–0.78, moderate ~0.45–0.55, noise <0.3 (a near-verbatim bio query topped out ~0.55, because `build_embedding_text` blends bio+instruments+genres+city, diluting short queries). The eval runner passes `similarity_threshold=0.0` so recall measures ranking, not the gate. Don't raise the default toward 0.8 without re-measuring against the live model — it silently returns `[]`.
  - **Local collation mismatch after the image swap:** the old `postgres_data` volume was created by the `postgres:16` image, whose libc collation differs from the `pgvector/pgvector:pg16` image. Postgres then refuses to `CREATE DATABASE` (incl. the `test_*` DB), erroring `template database "template1" has a collation version mismatch`. Fix once, locally: `docker compose exec -T db psql -U postgres -d postgres -c 'ALTER DATABASE template1 REFRESH COLLATION VERSION;'` (repeat for `postgres` and `frikkinwave`). CI is unaffected — it builds a fresh DB from the pgvector image. Alternatively `docker compose down -v` to recreate the volume clean (drops local data).

---

## Infrastructure (AWS) — see `infra/README.md`

- **Two Terraform stacks.** `infra/dns/` is PERSISTENT (Route 53 zone + ACM cert) — **never `terraform destroy` it** or the GoDaddy NS delegation breaks. `infra/eks/` is the disposable app stack (VPC, EKS, RDS, ECR, load balancer controller); destroy/apply freely. It discovers the zone + cert via `data` sources.
- **DEPLOYMENT STATE: live on EKS, last verified 2026-08-19.** `https://api.frikkinwave.com/api/health/` returns 200 from Kubernetes: **web x2, worker, notifications, search, redis** on **3** ARM64 nodes across ap-south-1a/1b/1c, behind an ALB, plus **Strimzi + a 3-broker Kafka cluster** in the `kafka` namespace. Running image `frikkinwave-prod:383f352`, helm revision 6. **~$0.26/hr — tear down between sessions** with `./infra/scripts/eks-down.sh`. Verify before assuming: `aws eks list-clusters --region ap-south-1`. *(Update this bullet when the state changes.)*
  - **KAFKA.md stages 0-2 are DONE.** Storage works (EBS CSI + a default gp3 class in `infra/eks/ebs-csi.tf`); nodes are 3× t4g.medium/large; Strimzi 1.1.0 runs Kafka 4.2.1 in KRaft, 3 brokers one per node and per AZ, RF 3 / `min.insync.replicas` 2, verified with a produce/consume round-trip. **The app is still entirely on Celery** — nothing produces to or consumes from Kafka. **NEXT UP: stages 3-5**, the first work that touches application code.
  - **Kafka infra invariants are enforced by `tests/test_infrastructure.py`**, on the same principle as the queue guardrails: a StorageClass the chart names but Terraform does not create, a replication factor quietly dropped to 1, or more brokers than nodes are all *silent* at runtime. Don't weaken those tests to make a change pass.
  - **Use `helm upgrade --reset-then-reuse-values`, never `--reuse-values`.** The latter reuses the previous release's coalesced values and does **not** re-read chart defaults, so any key added to `values.yaml` in the same change renders empty and the field is silently dropped — `helm upgrade` still reports success.
  - **Kafka client auth is mTLS.** The credential is a mounted `user.crt`/`user.key`, never an env var. Secret volumes are root-owned and the image runs as uid 10001, so the mount needs `defaultMode: 0440` **plus** `fsGroup: 10001`; with `0400` librdkafka fails with `SSL routines::system lib`, which says nothing about permissions. Don't "fix" it with `0444` — that world-readables a private key.
  - **Kafka is TLS + mTLS + ACLs, and `authorization: simple` DENIES BY DEFAULT.** The plaintext 9092 listener is gone; clients use 9093. Any new client needs a `KafkaUser` with explicit ACLs or it can do nothing — that is intended. `KafkaUser/frikkinwave-app` is what the app authenticates as; Strimzi issues and renews its certificate into a cluster Secret, **never into git, helm values, or Terraform state** (this repo is public).
  - **Declaring a `KafkaUser` requires the User Operator.** With only `topicOperator` enabled, a KafkaUser is inert in the worst way: the object exists, `kubectl get kafkauser` prints its ACLs, nothing errors, and no credential is ever created in Kafka. A test asserts this now.
  - **NetworkPolicy enforcement had to be turned on explicitly** (`enableNetworkPolicy` on the `vpc-cni` addon). It was off, so every policy on the cluster — including Strimzi's own — was inert while looking like protection. It is **defence in depth, not the access control**: network location is not an authorization signal, and enforcement alone would have left port 9092 open because Strimzi leaves a listener unrestricted unless the Kafka CR sets `networkPolicyPeers`.
  - **There is no Kafka console.** AKHQ was added and removed on 2026-08-19. Read topics with `kafka-console-consumer.sh` via `kubectl exec` — you now need `--command-config` with SCRAM + the cluster CA truststore (recipe in `KAFKA.md`).
  - **Teardown order in `eks-down.sh` is load-bearing: `kafkatopic`/`kafkauser` FIRST.** Their finalizers are removed only by the Entity Operator, which dies with the Kafka resource — delete Kafka first and every topic strands in `Terminating` forever, blocking `helm uninstall` and `terraform destroy`.
  - **Bump `infra/helm/kafka/Chart.yaml`'s version on every chart change.** Terraform's `helm_release` diffs a *local* chart on its version, not its contents — edit a template without bumping and `terraform apply` reports "0 changed" and deploys nothing.
  - **Anything stateful needs the gp3 class, and the probe needs a consumer pod.** Both storage classes are `WaitForFirstConsumer` (EBS volumes are zonal), so a PVC with no pod sits `Pending` even when storage is perfectly healthy — a bare-PVC probe reports failure after a successful fix.
  - **Redis is PARKED.** It is broker-only today and nothing caches. It is kept for the read-through cache in MICROSERVICES.md §3, not because it is in use. If that work never happens, delete it.
  - **The database restores itself.** Teardown takes a final snapshot; the next apply discovers the newest one automatically. **Never hand-edit `db_snapshot_identifier` on a running stack** — it is `ForceNew`, so changing it destroys and recreates the database rather than re-restoring it. `lifecycle.ignore_changes` now blocks that; don't remove it.
  - **Bring it up:** `./infra/scripts/eks-up.sh` (~15 min, Terraform: cluster/RDS/ECR/LB controller) then `./infra/scripts/app-deploy.sh` (~5 min, build + push + `helm upgrade` + Route 53 + verify). Terraform owns AWS; Helm owns the app.
  - **Read `infra/eks/README.md` before touching this.** It records four traps already paid for: the EKS-version 6x extended-support billing trap, the access-entry 409, that a **restored snapshot predating a denormalization needs `backfill_profile_ratings` run by hand** (migrate gives you the schema, never the derived data), and negative DNS caching making a healthy deploy look broken.
  - **`POD_IP` is load-bearing on Kubernetes.** `production.py` appends the pod IP (downward API) to `ALLOWED_HOSTS`. Both kubelet probes and ip-mode ALB health checks send the pod IP as the `Host` header; without it every readiness probe 400s and no pod reaches Ready.
  - **Helm templates are excluded from pre-commit's `check-yaml`** — they are Go templates, not YAML, until rendered. `helm lint` + `helm template` cover them.
- **Region** `ap-south-1` (Mumbai). **Migrations never run on container start** — concurrent replicas would race the same migration; they are a Helm `pre-upgrade` hook Job that must succeed before the Deployments roll. **Images** are `linux/arm64` (Graviton). **Secrets:** Terraform writes a Kubernetes Secret the pods read via `envFrom`, and mirrors the values to SSM for Phase 3's External Secrets.

---

## Environment variables

See `.env.example` for the full list. Never commit `.env`.

Critical ones:
- `DJANGO_SECRET_KEY` — required in all environments
- `DATABASE_URL` — postgres connection string
- `DJANGO_SETTINGS_MODULE` — set to `config.settings.local` for dev, `config.settings.production` for prod
- `SEARCH_SIMILARITY_THRESHOLD` — semantic-search relevance floor (default `0.4`, `0` disables). Tunable via the chart's `config` map (`helm upgrade --reset-then-reuse-values --set config.SEARCH_SIMILARITY_THRESHOLD=N`), no image rebuild. **This only takes effect because the chart stamps `checksum/config` on the pod templates** — `envFrom` values are injected at container start, so a ConfigMap change alone updates nothing in a running pod and `helm upgrade` still reports success. Don't remove that annotation.
- `EVENT_TRANSPORT` — `celery` (default) or `kafka`. Chooses how the outbox relay hands events off; `publish()` and the outbox are unaffected. Flip live with `helm upgrade --reset-then-reuse-values --set config.EVENT_TRANSPORT=kafka` and flip straight back if anything looks wrong. See `KAFKA.md` stage 3.
