"""
Architectural guardrails for service extraction (see MICROSERVICES.md).

These are the rules that make the eventual split a refactor rather than a
rewrite. They are easy to violate accidentally and invisible in review, so they
are asserted mechanically rather than trusted to discipline.
"""

from __future__ import annotations

import ast
import pathlib

APPS_DIR = pathlib.Path(__file__).resolve().parent.parent / "apps"


def _app_modules() -> list[tuple[str, pathlib.Path]]:
    """Every non-test .py file under apps/, paired with its owning app label."""
    out = []
    for path in APPS_DIR.rglob("*.py"):
        if "/tests/" in str(path) or path.name == "__init__.py":
            continue
        app = path.relative_to(APPS_DIR).parts[0]
        out.append((app, path))
    return out


def _runtime_imports(tree: ast.Module) -> list[ast.ImportFrom]:
    """
    Imports that execute at runtime — i.e. everything **except** those guarded by
    `if TYPE_CHECKING:`, which exist only for annotations and create no coupling.
    """
    type_only: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and "TYPE_CHECKING" in ast.dump(node.test):
            for child in ast.walk(node):
                type_only.add(id(child))
    return [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.ImportFrom) and n.module and id(n) not in type_only
    ]


class TestNoCrossAppModelImports:
    """An app may never import another app's models at runtime — a FK across a
    service boundary cannot exist, so the coupling must not either."""

    def test_models_are_never_imported_across_apps_at_runtime(self) -> None:
        violations: list[str] = []
        for app, path in _app_modules():
            tree = ast.parse(path.read_text())
            for node in _runtime_imports(tree):
                mod = node.module or ""
                if not mod.startswith("apps."):
                    continue
                parts = mod.split(".")
                target_app = parts[1] if len(parts) > 1 else ""
                is_models = mod.endswith(".models")
                if is_models and target_app != app:
                    violations.append(f"{path.relative_to(APPS_DIR.parent)}:{node.lineno} → {mod}")
        # Exempt tooling: management commands (reconciliation / seeding) and the
        # eval harness legitimately construct other apps' rows directly. They are
        # not part of any request path, so they carry no extraction risk.
        violations = [v for v in violations if "/management/" not in v and "/evals/" not in v]
        assert not violations, "Cross-app model imports:\n  " + "\n  ".join(violations)


class TestIdentityBoundary:
    """Outside the users app, identity lookups must return a DTO, not an ORM
    object — you cannot send a Django model over a network."""

    def test_other_apps_use_get_user_ref_not_the_model_lookup(self) -> None:
        violations = [
            f"{path.relative_to(APPS_DIR.parent)}"
            for app, path in _app_modules()
            if app != "users" and "get_user_by_username" in path.read_text()
        ]
        assert not violations, (
            "These use the model lookup instead of get_user_ref():\n  " + "\n  ".join(violations)
        )


class TestOutboxDiscipline:
    """Domain services publish to the outbox; they never enqueue Celery directly,
    which is what could silently lose an event between COMMIT and enqueue."""

    def test_services_do_not_call_delay_directly(self) -> None:
        violations: list[str] = []
        for app, path in _app_modules():
            if app == "events":  # the relay's own nudge is the one legitimate .delay()
                continue
            if path.name != "services.py":
                continue
            for lineno, line in enumerate(path.read_text().splitlines(), 1):
                if ".delay(" in line or ".apply_async(" in line:
                    violations.append(f"{path.relative_to(APPS_DIR.parent)}:{lineno}")
        assert not violations, (
            "Services must publish() to the outbox, not enqueue Celery directly:\n  "
            + "\n  ".join(violations)
        )

    def test_every_registered_topic_has_a_consumer(self) -> None:
        from apps.events.registry import EVENT_HANDLERS
        from config.celery import app as celery_app

        celery_app.loader.import_default_modules()
        missing = [t for t, name in EVENT_HANDLERS.items() if name not in celery_app.tasks]
        assert not missing, f"Topics with no registered consumer task: {missing}"


class TestUserRefContract:
    def test_user_ref_is_immutable_and_serializable(self) -> None:
        import dataclasses
        import json

        from apps.users.services import UserRef

        assert dataclasses.is_dataclass(UserRef)
        ref = UserRef(id=__import__("uuid").uuid4(), username="u", email="u@example.com")
        with __import__("pytest").raises(dataclasses.FrozenInstanceError):
            ref.username = "changed"  # type: ignore[misc]
        # Everything on the DTO must survive a JSON round trip (i.e. a network hop).
        json.dumps({**dataclasses.asdict(ref), "id": str(ref.id)})


# ---------------------------------------------------------------------------
# Queue routing must match what the cluster actually consumes.
#
# Splitting notifications onto their own queue introduced a failure mode that is
# completely silent: route a task to a queue no worker is started with and it is
# never executed, never retried, and never logged as an error. It simply sits in
# Redis. Nothing in Celery, Kubernetes, or the outbox notices.
#
# This ties the two halves together — Celery's routing table on one side, the
# Helm chart's worker commands on the other — so the drift fails the build.
# ---------------------------------------------------------------------------

CHART_VALUES = APPS_DIR.parent / "infra" / "helm" / "frikkinwave" / "values.yaml"


def _queues_consumed_by_the_cluster() -> set[str]:
    """Every queue named by a worker Deployment in the chart's values."""
    import yaml

    values = yaml.safe_load(CHART_VALUES.read_text())
    consumed: set[str] = set()
    # Every entry under `workers` becomes a Deployment started with --queues.
    for block in (values.get("workers") or {}).values():
        if block.get("enabled", True) and block.get("queues"):
            consumed.update(q.strip() for q in str(block["queues"]).split(","))
    return consumed


def _queue_for_task(task_name: str) -> str:
    """Resolve a task to its queue exactly as Celery would at publish time."""
    from django.conf import settings

    from config.celery import app as celery_app

    route = celery_app.amqp.router.route({}, task_name)
    queue = route.get("queue")
    # No matching route means the default queue.
    return getattr(queue, "name", None) or settings.CELERY_TASK_DEFAULT_QUEUE


def test_every_registered_handler_lands_on_a_consumed_queue() -> None:
    """
    A task routed to a queue no Deployment consumes is silently never run.

    If this fails, either add the queue to a worker's `queues` in the chart, or
    fix CELERY_TASK_ROUTES. Do not "fix" it by deleting the assertion — the
    whole point is that the runtime symptom is invisible.
    """
    from apps.events.registry import EVENT_HANDLERS

    consumed = _queues_consumed_by_the_cluster()
    assert consumed, "No worker queues found in the Helm values — parsing broke."

    stranded = {
        task: _queue_for_task(task)
        for task in EVENT_HANDLERS.values()
        if _queue_for_task(task) not in consumed
    }
    assert not stranded, (
        f"These tasks route to queues no worker consumes {sorted(consumed)}: {stranded}. "
        "They would be enqueued and never executed, with no error anywhere."
    )


def test_the_outbox_relay_itself_is_consumed() -> None:
    """
    The relay is the backstop that makes delivery guaranteed. If *it* were
    stranded on an unconsumed queue, every event would silently stop being
    dispatched — the one failure that breaks all the others at once.
    """
    assert _queue_for_task("events.relay_outbox") in _queues_consumed_by_the_cluster()


# ---------------------------------------------------------------------------
# Kafka credentials reach only the components that run the outbox relay.
#
# EVENT_TRANSPORT=kafka needs broker credentials in the worker Deployment and the
# relay CronJob. Nothing else: web pods write the outbox row and nudge Celery,
# and the notifications/search workers consume their own queues. Handing them a
# credential they never use widens the blast radius of any one pod being
# compromised, for no functionality — and it is the kind of thing that spreads
# quietly, because mounting a secret never breaks anything.
# ---------------------------------------------------------------------------


def test_kafka_credentials_are_scoped_to_the_relay() -> None:
    import yaml

    values = yaml.safe_load(CHART_VALUES.read_text())
    relay_workers = {
        name
        for name, cfg in (values.get("workers") or {}).items()
        if cfg.get("enabled", True) and cfg.get("runsOutboxRelay")
    }
    assert relay_workers == {"worker"}, (
        f"Expected only the general worker to carry Kafka credentials, got "
        f"{sorted(relay_workers)}. Every extra one is a pod holding a broker "
        "credential it never uses."
    )


def test_the_relay_worker_consumes_the_queue_the_relay_task_routes_to() -> None:
    """
    The worker flagged `runsOutboxRelay` must actually be the one that runs it.
    Flag the wrong Deployment and the credentials land on a pod that never
    produces, while the pod that does produce has none — which fails only once
    the transport is flipped, in production.
    """
    import yaml

    values = yaml.safe_load(CHART_VALUES.read_text())
    relay_queue = _queue_for_task("events.relay_outbox")
    flagged = {
        name for name, cfg in (values.get("workers") or {}).items() if cfg.get("runsOutboxRelay")
    }
    for name in flagged:
        queues = {q.strip() for q in str(values["workers"][name]["queues"]).split(",")}
        assert relay_queue in queues, (
            f"Worker '{name}' is flagged runsOutboxRelay but consumes {sorted(queues)}, "
            f"not '{relay_queue}' where events.relay_outbox is routed."
        )


def test_the_default_event_transport_is_celery() -> None:
    """
    Deploying stage 3 must change nothing until the flag is flipped
    deliberately. The event backbone works today; it is not worth betting on
    one deploy.
    """
    import yaml

    values = yaml.safe_load(CHART_VALUES.read_text())
    assert values["config"]["EVENT_TRANSPORT"] == "celery"
