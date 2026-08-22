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
See `TESTING.md` for the coverage audit, the remaining test gaps, and the **measured** capacity limits.

**Two services are extracted and live**: `apps/notifications` and `apps/search`. Each has its own
queue, its own Deployment, no cross-app model imports, and self-contained event payloads. They still
share the image — the *contract* is cut, the packaging is not.

`apps/search` no longer shares the database either: since the move to OpenSearch it owns a store
nothing else can reach, and has **no Django models at all**. That was not the plan — the plan was a
separate Postgres one day — but replacing the backend turned out to be the thing that finished the
extraction, and it worked precisely because the boundary was already `search(query=...) -> [(id,
score)]`. A complete backend replacement changed neither the contract nor a single caller. The groundwork
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
- Docker Desktop (for Postgres)
- uv (`brew install uv`)

### Setup

```bash
cd frikkinwave-backend
uv venv --python 3.13
source .venv/bin/activate
uv pip install -r requirements/dev.txt   # base.txt + tests/lint/types
cp .env.example .env        # fill in DJANGO_SECRET_KEY
docker compose up -d        # Postgres + OpenSearch. No Redis — see below.
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

**Search tests need a cluster, and skip without one.** `local.py` blanks
`OPENSEARCH_URL` under pytest so the suite never depends on a live cluster —
otherwise every profile-save test in the repo would make an HTTP call. The tests
that genuinely exercise OpenSearch opt in via a separate variable:

```bash
docker compose up -d opensearch
OPENSEARCH_TEST_URL=http://localhost:9200 pytest
```

Without it those ~20 tests **skip**, which is fine locally and would be a silent
hole in CI — so `tests/test_architecture.py` asserts the CI workflow sets it.

### Rebuilding the search index

The index is derived data and holds no source of truth, so it is rebuilt from
Postgres rather than restored:

```bash
python manage.py reindex_profiles --prune
```

`--prune` removes documents whose profile no longer exists. It only runs after a
complete pass — a rebuild that fails partway skips it rather than deleting every
profile it had not reached yet. The chart runs this as a **post-upgrade hook** on
every deploy; see the OpenSearch section under Known gotchas for why that is not
optional.

*(The Phase 2.8 matching evals — recall@k, MRR, blurb grounding — were deleted
with the embeddings they measured. There is no equivalent relevance harness for
BM25 yet, and the field boosts are reasoned rather than measured; see ROADMAP
Phase 2R.)*

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
- **Search is OpenSearch, and the index is DERIVED — it has no snapshot.** RDS restores itself from a snapshot on every rebuild; the search domain does not, because everything in it is rebuilt from Postgres. So a fresh stack comes up with a **healthy, empty cluster**: search returns `[]`, every probe stays green, and nothing anywhere reports a fault. The chart's post-upgrade `reindex_profiles` Job is what closes that window — don't disable it, and don't move it to a pre-upgrade hook (it indexes through the new image's payload builder against the migrated schema).
  - **The SDK is imported in exactly one module.** `apps/search/client.py` wraps `opensearch-py` and converts every `OpenSearchException` into a domain `SearchUnavailableError`, so nothing else knows what library is underneath — the same shape `apps/events/kafka.py` uses. A test fails the build on an `import opensearchpy` anywhere else.
  - **An empty `OPENSEARCH_URL` is a supported state, not a misconfiguration.** It is how local dev and CI run with no cluster: "not configured" and "cluster unreachable" are deliberately one case, and both degrade to `[]`. **The one exception is `reindex_profiles`**, which raises instead — a deploy hook that no-ops silently would print "Indexed 42 profiles." over an index it never wrote to.
  - **Reads degrade, writes retry.** `search()` swallows `SearchUnavailableError` and returns `[]`, because an upstream failure must never 500 a user. `index_profile()` lets it propagate, because it runs in a Kafka consumer where raising means bounded retry then the DLT — swallowing there would commit the offset and lose the update silently.
  - **The score is BM25, not a similarity.** Unbounded, and meaningful only for ordering *within one result set*. Never compare it against a constant. This is why `SEARCH_SIMILARITY_THRESHOLD` was deleted rather than retuned: it defaulted to 0.4, and a measured strong BM25 match scores ~0.4 too, so a surviving floor would have looked entirely plausible while cutting good results.
  - **Deletions are reconciled, not evented.** There is no delete endpoint anywhere in this project, so profiles leave through Django admin, a cascade from a deleted user, or the demo seeder's `--reset` — none of which publish anything, and the index has no FK to cascade through. `reindex_profiles --prune` sweeps them using an `indexed_at` watermark. `remove_profile()` exists and is tested but has **no caller**; wire it to a `profile.deleted` event if a delete endpoint is ever added.
  - **`delete_by_query` must use `conflicts="proceed"`.** It searches, then deletes each hit by the version it saw — and the rebuild rewrites every document immediately beforehand, so conflicts are routine. The default (`abort`) fails the whole sweep partway through. Skipping is also correct: a document whose version moved was just written, which is exactly what a stale-document sweep must not delete.
  - **The mapping is `dynamic: strict`.** A typo'd payload key is rejected rather than silently creating a field with an inferred type that then cannot be changed without a full reindex. `index_profile` calls `ensure_index` on **every** event for the same reason — if the index ever goes missing, OpenSearch would auto-create it with guessed mappings on the next write.
  - **Keep the client major and the engine major aligned.** `opensearch-py` is pinned to **3.0.0** deliberately: it is the newest 3.x that does *not* depend on `opensearch-protobufs`, which drags `grpcio` and `protobuf` into every pod for a REST client. A test ties it to `opensearch_engine_version` in Terraform.
  - **Local collation mismatch after the image swap — now in the OTHER direction.** compose has gone back to `postgres:16` (pgvector is removed), so an existing `postgres_data` volume written by `pgvector/pgvector:pg16` is now the mismatched one. Postgres refuses to `CREATE DATABASE` (incl. the `test_*` DB), erroring `template database "template1" has a collation version mismatch`. Fix once: `docker compose exec -T db psql -U postgres -d postgres -c 'ALTER DATABASE template1 REFRESH COLLATION VERSION;'` (repeat for `postgres` and `frikkinwave`). CI is unaffected — it builds a fresh DB every run. Alternatively `docker compose down -v` to recreate the volume clean (drops local data).
- **The AI is gone (2026-08-23).** No OpenAI, no embeddings, no pgvector, no `apps/ai`. Removed with it: the compatibility blurb endpoint and its table, the coach's LLM `tip` (the completeness score and suggestions stay — they were always the half doing the work), and the Phase 2.8 eval harness. Historical prose in migrations and docstrings still mentions OpenAI where it explains *why* something exists; that is accurate history, not a live dependency.

---

## Infrastructure (AWS) — see `infra/README.md`

- **Two Terraform stacks.** `infra/dns/` is PERSISTENT — **never `terraform destroy` it**. It holds everything that must outlive a teardown: the Route 53 zone + ACM cert (destroying them breaks the GoDaddy NS delegation), the budget alarm (which matters most *after* teardown, when orphaned resources bill), and the SNS alert topic (an email subscription needs a confirmation click, so a topic that died each session would need re-confirming each session). `infra/eks/` is the disposable app stack (VPC, EKS, RDS, ECR, load balancer controller, Alertmanager's IRSA role); destroy/apply freely. It discovers the zone, cert and topic via `data` sources — **apply the persistent stack first**, which `eks-up.sh` now checks before doing anything.
- **DEPLOYMENT STATE: TORN DOWN as of 2026-08-21 — $0/hr.** Verified against AWS rather than the script's own report: no clusters, RDS, load balancers, volumes, instances or NAT gateways. Data is preserved in `frikkinwave-prod-final-2780390a`, which the next `eks-up.sh` restores automatically — **three** manual snapshots now exist and only the newest is ever used, so prune the older two. **The persistent stack survived intact**: the SNS topic with its subscription still *confirmed* (no email re-click on the next rebuild), the Route 53 zone, the ACM cert and the budget alarm. Last live config, for when it returns: web x2, relay and 4 consumer Deployments on 3 ARM64 nodes across ap-south-1a/1b/1c behind an ALB, plus Strimzi 1.1.0 running Kafka 4.2.1 (KRaft, 3 brokers, RF 3 / ISR 2, 26 topics). Verify before assuming anything: `aws eks list-clusters --region ap-south-1`. Bring it back with `terraform -chdir=infra/dns apply` (idempotent, usually a no-op) then `./infra/scripts/eks-up.sh && ./infra/scripts/app-deploy.sh`. **This has not been run since the OpenSearch migration** — expect the first rebuild to be slower (~35 min: a domain takes 15-20 min to create, and as long again to delete on the way down) and to cost ~$0.30/hr rather than ~$0.26. The domain restores no data; `app-deploy.sh`'s post-upgrade Job rebuilds the index from Postgres. *(Update this bullet when the state changes — it is the first thing read each session, so a stale value here is worse than no value.)*
  - **On a rebuild the kubeconfig is stale, and you cannot fix it up front.** Each stack gets a new API endpoint while the *context name stays identical*, so the dead endpoint looks correct. `aws eks update-kubeconfig` cannot run before the cluster exists, so the honest sequence is: run `eks-up.sh`, let it fail at `terraform_data.wait_for_kafka_credentials` with `no such host` naming the **previous** endpoint, then `aws eks update-kubeconfig --name frikkinwave-prod --region ap-south-1` and re-run — it converges. Only the `local-exec` provisioners read the kubeconfig; the kubernetes/helm providers mint a token via `aws eks get-token` and are unaffected. Hit again on the 2026-08-21 rebuild.
  - **Two things survive `terraform destroy` and accumulate silently.** RDS keeps a manual snapshot per teardown and only the newest is ever restored (seven had piled up by 2026-08-20; six were deleted). And EKS creates `/aws/eks/<cluster>/cluster` with **no expiry** — the cluster goes, the logs stay, and every rebuild adds to the same group. `eks.tf` now declares that log group with 7-day retention *before* the cluster so EKS reuses it instead of making its own; don't remove that `depends_on`, it is what makes the retention apply.
  - **Any controller that recreates PVCs must be uninstalled BEFORE the PVC sweep in `eks-down.sh`.** True of Strimzi and, since Phase 3, of the Prometheus Operator — it owns Prometheus's PVC through a StatefulSet volumeClaimTemplate, so deleting the PVC while it lives just recreates it and the volume outlives `terraform destroy`. Caught by the script's own orphan check, which is why that check exits non-zero and names the resource.
  - **Teardown deletes the Route 53 alias**, so `api.frikkinwave.com` may look down from your own network afterwards while being healthy everywhere else — a cached negative answer, which home routers hold past its 600s TTL. `app-deploy.sh` detects this and says so; check `dig +short @1.1.1.1 api.frikkinwave.com` before debugging the cluster.
  - **KAFKA.md is COMPLETE (stages 0-5)**, including the security baseline, mTLS, health signals and a verified failure drill. **Phase 3 observability is deployed and confirmed live**: Alertmanager routes to the SNS topic (sigv4, ap-south-1) with Watchdog/InfoInhibitor black-holed, the relay's three outbox gauges are scraped through its PodMonitor, and Strimzi's kafka-exporter is scraped for **consumer-group lag**. Two things to know before alerting on lag: `kafka_consumergroup_lag` does not exist until a group has **committed offsets** (a freshly built cluster reports nothing, and that is not a fault), and kafka-exporter emits **`-1` for every partition with no committed offset**, so a bare `sum()` goes negative — use `sum(clamp_min(kafka_consumergroup_lag, 0))`. **No lag alert exists yet** — the metric is available, nothing watches it.
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
  - **Redis is GONE.** Removed with Celery in KAFKA.md stage 5 — no ElastiCache, no compose service, no dependency, no reference anywhere in code or infra. If the read-through cache in MICROSERVICES.md §3 is ever built, it starts from nothing rather than from a parked instance.
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
- `OPENSEARCH_URL` — the search cluster, credentials embedded (`https://user:pass@host:443`), assembled in Terraform and delivered through the Kubernetes Secret so the generated password never travels through Helm values. **Empty is valid** and means search degrades to `[]`; it is blanked under pytest.
- `OPENSEARCH_INDEX` / `OPENSEARCH_TIMEOUT` — non-secret, so they live in the chart's `config` map and are tunable with `helm upgrade --reset-then-reuse-values --set config.OPENSEARCH_TIMEOUT=N`, no image rebuild. **This only takes effect because the chart stamps `checksum/config` on the pod templates** — `envFrom` values are injected at container start, so a ConfigMap change alone updates nothing in a running pod and `helm upgrade` still reports success. Don't remove that annotation. Keep the timeout short: search runs synchronously in the request path, so a slow cluster behind a generous timeout becomes a worker-exhaustion problem rather than a search one.
- `KAFKA_BOOTSTRAP_SERVERS` / `KAFKA_SSL_*` — the broker and the mTLS client certificate. Read by the relay and the consumer Deployments only; web pods get none.
- `EVENT_RELAY_INLINE` — **False in production.** True under pytest (set in `config/settings/local.py`), where `_dispatch` delivers to in-process subscribers instead of a broker. Never enable it in a deployed config: it would put a synchronous Kafka produce in the request path.
- `EVENT_RELAY_INTERVAL` — seconds the relay loop sleeps when idle (default `1.0`). The upper bound on event latency; a full batch skips the sleep so a backlog drains at speed.
