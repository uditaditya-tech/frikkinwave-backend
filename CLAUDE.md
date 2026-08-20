# CLAUDE.md — Working instructions for this repo

Read this file at the start of every session. It encodes all conventions and working rules for frikkinwave-backend.

---

## Project context

See `PROJECT.md` for what this is and why.
See `ROADMAP.md` for current phase and next sub-steps.
See `DATAMODEL.md` for current and planned data models.
See `CODEBASE.md` for directory structure and where things live.
See `MICROSERVICES.md` for the service-extraction target architecture and scaling path.
See `KAFKA.md` for the Kafka migration — **COMPLETE**. Celery is gone; Kafka is the only event transport.

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

4. **Events for async work, not direct calls.** Profile saved → `publish()` to the outbox → the relay produces to Kafka → a consumer group handles it. Never dispatch from a view or a service; `publish()` is the only entry point.

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
uv pip install -r requirements/dev.txt   # base.txt + tests/lint/types
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
- **Architectural rules are enforced by tests, not discipline.** `tests/test_architecture.py` fails the build on: a runtime cross-app model import, use of `get_user_by_username` outside the users app, a service dispatching directly instead of via `publish()`, a published topic nothing subscribes to, a consumer group with no Deployment running it, anything in the Kafka chart exposed publicly, or a mutable/non-serializable `UserRef`. If you're adding a legitimate exception, exempt it explicitly there with a reason — don't weaken the rule.
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
- **Events (KAFKA.md):** the outbox is unchanged by the migration — Kafka does not solve dual-write. Services call `apps.events.services.publish(topic=..., payload={...})` **inside their transaction**; the row and the state change commit together. Rules that follow:
  - **Never dispatch from a view or service.** `publish()` is the only entry point. The relay Deployment (`relay_outbox --loop`) moves events to Kafka.
  - **Payload keys must match the handler's kwargs** — the consumer passes `payload` straight through.
  - **Declare the subscription in the consuming app's `consumers.py`** (`SUBSCRIPTIONS: topic -> handler`). There is no central registry; the producer must not know who listens. A test fails the build if the producer side imports a `consumers` module.
  - **Retries are spaced, and that spacing is load-bearing.** A failed dispatch sets `next_attempt_at = now + min(2 × 2^attempts, 600)s`, so `MAX_ATTEMPTS` spans ~27 minutes. Without it retries ran at the 1s poll interval and any broker outage over ten seconds stranded every pending event permanently — measured on a live cluster, not inferred. Don't shorten the backoff or lower `MAX_ATTEMPTS` without re-checking that `OutboxNotDraining` (300s + 5m) still fires *before* events exhaust; a test enforces that ordering.
  - **Consumers must be idempotent** — delivery is at-least-once. Prefer recomputing from source; where a create is unavoidable, key it on the event id (see `fan_out_activity`, which uses `event_id` as the `Activity` PK).
  - **Notifications and search are extracted services — keep their payloads self-contained.** They import no other app and no models; producers publish the facts the handler needs, never an id to re-read. **Check nullability of every field you move into a payload** — building it in the producer runs it in the request path, so a field that used to crash harmlessly in a retry now 500s the user (exactly what `engagement.proposed_date` did).
  - **Every new topic needs a `KafkaTopic` AND a `.dlt` topic in `infra/helm/kafka/values.yaml`.** `authorization: simple` denies anything ungranted, so a missing entry is a denied produce — and a missing DLT turns a dead-letter into a stalled partition.
  - **In tests** the `db` fixture's outer transaction never commits, so wrap the action in `with django_capture_on_commit_callbacks(execute=True):` or call `relay_pending()` directly.
- **pgvector (Phase 2, done in 2.3):** RDS Postgres 16 supports it, but the `vector` extension must be enabled (`CREATE EXTENSION vector`) **before** any `VectorField` migration — `makemigrations` does NOT add this, so hand-edit the migration to put pgvector's `VectorExtension()` as the **first** operation (see `apps/musicians/migrations/0004_profileembedding.py`). The RDS master user can run it. `pgvector` is in requirements; docker-compose + CI both use the `pgvector/pgvector:pg16` image.
  - **`HnswIndex` needs `django.contrib.postgres`** in `INSTALLED_APPS` (already added) — without it `manage.py check` fails `postgres.E005`.
  - **Async-on-write vs sync-on-read:** embeddings (2.4) are generated by the `search` consumer group on profile *save* (a write event → background). Compatibility blurbs (2.6) are generated *synchronously* in the request on a cache miss, because the caller needs the text in the response and there's no write to react to — then cached in `CompatibilityBlurb` (canonical unordered pair). Don't reflexively make on-demand reads asynchronous.
  - **OpenAI access (2.4+):** all calls go through `apps/ai/client.py` (`OpenAIClient.embed` + `.complete`, cached `get_openai_client()`) — never `import openai` elsewhere. The client converts any `openai.OpenAIError` into a domain `OpenAIUnavailableError` so services degrade **without** importing the SDK's exception types: search → `[]`, compatibility → `None` (→ 503), coach → null `tip` (rules still returned). Treat "no key" and "API down/quota-exhausted" identically — an upstream failure must never 500 a user request. Tests patch `get_openai_client` to inject a fake, so CI needs **no key and makes no network calls**. `OPENAI_API_KEY` defaults to `""`; the embedding task **skips + logs** when it's empty (profiles still save). The embedding pipeline also **skips the OpenAI call when `embedding_text` is unchanged** (so toggling `is_available` costs nothing) — `build_embedding_text` must stay deterministic for that dedupe to hold.
  - **Search similarity floor (`SEARCH_SIMILARITY_THRESHOLD`, default 0.4):** `search_profiles` drops results scoring below it (similarity = 1 − cosine distance). **0.8 is unusable** for `text-embedding-3-small` — measured prod scores: strong matches ~0.72–0.78, moderate ~0.45–0.55, noise <0.3 (a near-verbatim bio query topped out ~0.55, because `build_embedding_text` blends bio+instruments+genres+city, diluting short queries). The eval runner passes `similarity_threshold=0.0` so recall measures ranking, not the gate. Don't raise the default toward 0.8 without re-measuring against the live model — it silently returns `[]`.
  - **Local collation mismatch after the image swap:** the old `postgres_data` volume was created by the `postgres:16` image, whose libc collation differs from the `pgvector/pgvector:pg16` image. Postgres then refuses to `CREATE DATABASE` (incl. the `test_*` DB), erroring `template database "template1" has a collation version mismatch`. Fix once, locally: `docker compose exec -T db psql -U postgres -d postgres -c 'ALTER DATABASE template1 REFRESH COLLATION VERSION;'` (repeat for `postgres` and `frikkinwave`). CI is unaffected — it builds a fresh DB from the pgvector image. Alternatively `docker compose down -v` to recreate the volume clean (drops local data).

---

## Infrastructure (AWS) — see `infra/README.md`

- **Two Terraform stacks.** `infra/dns/` is PERSISTENT — **never `terraform destroy` it**. It holds everything that must outlive a teardown: the Route 53 zone + ACM cert (destroying them breaks the GoDaddy NS delegation), the budget alarm (which matters most *after* teardown, when orphaned resources bill), and the SNS alert topic (an email subscription needs a confirmation click, so a topic that died each session would need re-confirming each session). `infra/eks/` is the disposable app stack (VPC, EKS, RDS, ECR, load balancer controller, Alertmanager's IRSA role); destroy/apply freely. It discovers the zone, cert and topic via `data` sources — **apply the persistent stack first**, which `eks-up.sh` now checks before doing anything.
- **DEPLOYMENT STATE: TORN DOWN as of 2026-08-20 — $0/hr.** Verified: no clusters, no RDS, no volumes, no load balancers. AWS holds exactly three things: the snapshot `frikkinwave-prod-final-002d5da3`, the Route 53 zone and the ACM cert. The database is preserved in that snapshot, which the next `eks-up.sh` restores automatically. Verify before assuming anything: `aws eks list-clusters --region ap-south-1`. Bring it back with `./infra/scripts/eks-up.sh && ./infra/scripts/app-deploy.sh` (~20 min, ~$0.26/hr). *(Update this bullet when the state changes.)*
  - **On a rebuild, `aws eks update-kubeconfig` FIRST.** The stack gets a new API endpoint every time and the local kubeconfig still names the dead one, so `eks-up.sh` fails partway with `no such host` naming the *previous* cluster. Then a plain `terraform apply` converges.
  - **Two things survive `terraform destroy` and accumulate silently.** RDS keeps a manual snapshot per teardown and only the newest is ever restored (seven had piled up by 2026-08-20; six were deleted). And EKS creates `/aws/eks/<cluster>/cluster` with **no expiry** — the cluster goes, the logs stay, and every rebuild adds to the same group. `eks.tf` now declares that log group with 7-day retention *before* the cluster so EKS reuses it instead of making its own; don't remove that `depends_on`, it is what makes the retention apply.
  - **Any controller that recreates PVCs must be uninstalled BEFORE the PVC sweep in `eks-down.sh`.** True of Strimzi and, since Phase 3, of the Prometheus Operator — it owns Prometheus's PVC through a StatefulSet volumeClaimTemplate, so deleting the PVC while it lives just recreates it and the volume outlives `terraform destroy`. Caught by the script's own orphan check, which is why that check exits non-zero and names the resource.
  - **Teardown deletes the Route 53 alias**, so `api.frikkinwave.com` may look down from your own network afterwards while being healthy everywhere else — a cached negative answer, which home routers hold past its 600s TTL. `app-deploy.sh` detects this and says so; check `dig +short @1.1.1.1 api.frikkinwave.com` before debugging the cluster.
  - **Last live config**, for when it comes back: web x2, relay, and 4 consumer Deployments on 3 ARM64 nodes across ap-south-1a/1b/1c behind an ALB, plus Strimzi 1.1.0 running Kafka 4.2.1 (KRaft, 3 brokers, RF 3 / ISR 2, 26 topics) in the `kafka` namespace.
  - **NEXT SESSION: the two open gaps have a written plan in `KAFKA.md` ("NEXT SESSION — closing the two gaps")** — relay self-reported metrics first, then an Alertmanager route, then load. Start there rather than re-deriving it. **Nothing pages today**, and a stalled relay is invisible to every alert that currently exists.
  - **KAFKA.md is COMPLETE (stages 0-5)**, including the security baseline, mTLS, health signals and a verified failure drill. **NEXT UP: Phase 3 observability** — Prometheus/Grafana, whose first job is **consumer-group lag**, the one failure neither current signal can see.
  - **Kafka consumers are declared PER APP** in `apps/<app>/consumers.py` (`SUBSCRIPTIONS: topic -> handler`), never in a central table — the producer must not know who listens. Run one with `python manage.py consume_events --group <app>`; the group name *is* the app label. A test fails the build if the producer side imports a `consumers` module.
  - **Every failure path in a consumer must end in a committed offset.** A poison message blocks its whole *partition*, unlike a stuck Celery task which blocked only itself. Handler failure → bounded retry → `<topic>.dlt`; malformed JSON → straight to the DLT with no retry. Never "fix" a consumer by skipping the commit.
  - **A topic you publish but nobody subscribes to is silent.** It is legal under Kafka, so `tests/test_architecture.py` flags it instead — a misspelled topic name looks exactly like this and reports nothing.
  - **KAFKA.md is COMPLETE (stages 0-5).** Kafka is the only transport. `EVENT_RELAY_INLINE` is False in production and True under pytest, where `_dispatch` delivers to in-process subscribers instead of a broker — the direct descendant of `CELERY_TASK_ALWAYS_EAGER`, and what lets 67 `django_capture_on_commit_callbacks` sites keep working.
  - **Every event topic needs a `.dlt` topic AND its ACL.** `authorization: simple` denies anything ungranted, so a missing DLT turns a dead-letter produce into a denied produce, a raised handler, and a stalled partition — the safety valve becoming the outage.
  - **Kafka is TLS + mTLS + ACLs, and `authorization: simple` DENIES BY DEFAULT.** The plaintext 9092 listener is gone; clients use 9093. Any new client needs a `KafkaUser` with explicit ACLs or it can do nothing — that is intended. `KafkaUser/frikkinwave-app` is what the app authenticates as; Strimzi issues and renews its certificate into a cluster Secret, **never into git, helm values, or Terraform state** (this repo is public).
  - **Declaring a `KafkaUser` requires the User Operator.** With only `topicOperator` enabled, a KafkaUser is inert in the worst way: the object exists, `kubectl get kafkauser` prints its ACLs, nothing errors, and no credential is ever created in Kafka. A test asserts this now.
  - **NetworkPolicy enforcement had to be turned on explicitly** (`enableNetworkPolicy` on the `vpc-cni` addon). It was off, so every policy on the cluster — including Strimzi's own — was inert while looking like protection. It is **defence in depth, not the access control**: network location is not an authorization signal, and enforcement alone would have left port 9092 open because Strimzi leaves a listener unrestricted unless the Kafka CR sets `networkPolicyPeers`.
  - **There is no Kafka console.** AKHQ was added and removed on 2026-08-19. Read topics with `kafka-console-consumer.sh` via `kubectl exec` — you now need `--command-config` with SCRAM + the cluster CA truststore (recipe in `KAFKA.md`).
  - **Teardown order in `eks-down.sh` is load-bearing: `kafkatopic`/`kafkauser` FIRST.** Their finalizers are removed only by the Entity Operator, which dies with the Kafka resource — delete Kafka first and every topic strands in `Terminating` forever, blocking `helm uninstall` and `terraform destroy`.
  - **Any Terraform reference to a Strimzi-generated Secret needs `try(..., "")`.** `terraform destroy` re-evaluates data sources *after* the script has deleted the Kafka cluster, so the lookup returns null and the whole destroy aborts with "Attempt to index null value" — leaving the EKS control plane and RDS billing while the script reports nothing wrong. Verified end to end 2026-08-19: no orphans, snapshot preserved, $0/hr.
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
- `KAFKA_BOOTSTRAP_SERVERS` / `KAFKA_SSL_*` — the broker and the mTLS client certificate. Read by the relay and the consumer Deployments only; web pods get none.
- `EVENT_RELAY_INLINE` — **False in production.** True under pytest (set in `config/settings/local.py`), where `_dispatch` delivers to in-process subscribers instead of a broker. Never enable it in a deployed config: it would put a synchronous Kafka produce in the request path.
- `EVENT_RELAY_INTERVAL` — seconds the relay loop sleeps when idle (default `1.0`). The upper bound on event latency; a full batch skips the sleep so a backlog drains at speed.
