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


# ---------------------------------------------------------------------------
# Kafka consumer groups (KAFKA.md stage 4).
#
# The Celery tests above stay while EVENT_TRANSPORT defaults to celery; stage 5
# deletes them. These are their Kafka equivalents, guarding the same class of
# failure: work that is never done, with nothing anywhere reporting an error.
#
# What CHANGES under Kafka is the direction. A topic with no subscriber is no
# longer a bug — that decoupling is the entire point of stage 4. What is still a
# bug is a declared subscription nobody runs, and a topic that had a consumer
# under Celery losing it under Kafka.
# ---------------------------------------------------------------------------


def _kafka_subscriptions() -> dict[str, set[str]]:
    """topic -> the set of app labels subscribing to it."""
    import importlib

    out: dict[str, set[str]] = {}
    for path in sorted(APPS_DIR.glob("*/consumers.py")):
        app = path.parent.name
        module = importlib.import_module(f"apps.{app}.consumers")
        for topic in getattr(module, "SUBSCRIPTIONS", {}):
            out.setdefault(topic, set()).add(app)
    return out


def test_every_celery_consumed_topic_also_has_a_kafka_subscription() -> None:
    """
    The flip-safety guardrail, and the reason stage 4 is riskier than it looks.

    While both transports exist, a topic handled under Celery but not declared in
    any consumers.py is processed today and silently stops being processed the
    moment EVENT_TRANSPORT is flipped to kafka. Nothing errors: the producer
    publishes happily, the message sits in a topic, and no consumer group ever
    reads it.
    """
    from apps.events.registry import EVENT_HANDLERS

    subscribed = _kafka_subscriptions()
    missing = sorted(set(EVENT_HANDLERS) - set(subscribed))
    assert not missing, (
        f"These topics have a Celery consumer but no Kafka subscription: {missing}. "
        "Flipping EVENT_TRANSPORT=kafka would stop processing them, with no error "
        "anywhere. Declare them in the owning app's consumers.py."
    )


def test_no_subscription_exists_for_a_topic_nothing_publishes() -> None:
    """
    The reverse drift: a handler wired to a topic no producer writes to. Harmless
    at runtime and therefore invisible — it just never runs, and reads in review
    as working code.
    """
    from apps.events.registry import EVENT_HANDLERS

    orphans = {
        topic: sorted(apps)
        for topic, apps in _kafka_subscriptions().items()
        if topic not in EVENT_HANDLERS
    }
    assert not orphans, f"These subscriptions listen to topics nothing publishes: {orphans}."


def test_a_consumer_group_maps_to_an_app_that_declares_subscriptions() -> None:
    """
    The group name IS the app label — `--group search` resolves
    apps/search/consumers.py. A group naming no such app starts, subscribes to
    nothing, and looks healthy forever, so the runtime raises instead. This pins
    that contract.
    """
    from apps.events.consumer import SubscriptionsNotFound, load_subscriptions

    for app in {a for apps in _kafka_subscriptions().values() for a in apps}:
        assert load_subscriptions(app), f"apps/{app}/consumers.py declares nothing"

    import pytest

    with pytest.raises(SubscriptionsNotFound):
        load_subscriptions("definitely-not-an-app")


def test_notifications_consumers_import_no_other_app() -> None:
    """
    The extracted-service boundary, restated for the Kafka path. It held for the
    Celery tasks and erodes silently if it is not asserted for their replacement.
    """
    tree = ast.parse((APPS_DIR / "notifications" / "consumers.py").read_text())
    for node in _runtime_imports(tree):
        mod = node.module or ""
        if mod.startswith("apps.") and not mod.startswith("apps.notifications"):
            raise AssertionError(
                f"apps/notifications/consumers.py imports {mod}; notifications is an "
                "extracted service and its payloads must be self-contained."
            )


def _deployed_consumer_groups() -> set[str]:
    """Every group the chart actually starts a Deployment for."""
    import yaml

    values = yaml.safe_load(CHART_VALUES.read_text())
    consumers = values.get("consumers") or {}
    if not consumers.get("enabled", False):
        return set()
    return set(consumers.get("groups") or {})


def test_every_declared_consumer_group_has_a_deployment() -> None:
    """
    The Kafka analogue of the Celery queue test, guarding the identical failure.

    An app declares SUBSCRIPTIONS and nothing runs them: the topic fills, no
    group ever reads it, and there is no error anywhere — same shape as a task
    routed to a queue no worker consumes. If this fails, add the group to
    `consumers.groups` in the chart, don't delete the assertion.
    """
    declared = {app for apps in _kafka_subscriptions().values() for app in apps}
    deployed = _deployed_consumer_groups()
    assert deployed, "No consumer groups found in the Helm values — parsing broke."

    stranded = sorted(declared - deployed)
    assert not stranded, (
        f"These apps declare subscriptions but no Deployment runs them: {stranded}. "
        f"Deployed groups: {sorted(deployed)}."
    )


def test_no_deployment_runs_a_group_that_declares_nothing() -> None:
    """
    The reverse: a Deployment whose group has no consumers.py starts, fails to
    resolve subscriptions, and CrashLoops. Better caught here than at 3am.
    """
    declared = {app for apps in _kafka_subscriptions().values() for app in apps}
    orphans = sorted(_deployed_consumer_groups() - declared)
    assert not orphans, (
        f"These Deployments run groups that declare no subscriptions: {orphans}. "
        "consume_events raises on start, so they will CrashLoop."
    )


def test_the_dead_letter_suffix_matches_the_kafka_chart() -> None:
    """
    The consumer produces to `<topic><suffix>`, and `authorization: simple`
    denies any topic not granted. If the app's suffix and the chart's disagree,
    every dead-letter produce is DENIED — the handler raises, and the partition
    stalls on exactly the message the DLT exists to move past. The safety valve
    becomes the outage.
    """
    import yaml
    from django.conf import settings

    kafka_values = yaml.safe_load(
        (APPS_DIR.parent / "infra" / "helm" / "kafka" / "values.yaml").read_text()
    )
    assert kafka_values["dltSuffix"] == settings.KAFKA_DLT_SUFFIX


def test_the_consumer_group_prefix_matches_the_acl() -> None:
    """
    Group ids are `<prefix>.<app>` and the KafkaUser grants the prefix as a
    prefix ACL. A mismatch fails authorization at join time, which looks like a
    consumer that starts cleanly and reads nothing.
    """
    import yaml
    from django.conf import settings

    kafka_values = yaml.safe_load(
        (APPS_DIR.parent / "infra" / "helm" / "kafka" / "values.yaml").read_text()
    )
    assert kafka_values["consumerGroupPrefix"] == settings.KAFKA_CONSUMER_GROUP_PREFIX
