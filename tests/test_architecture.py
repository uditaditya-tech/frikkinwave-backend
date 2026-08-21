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
    """Domain services publish to the outbox; they never dispatch directly,
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
            "Services must publish() to the outbox, not dispatch directly:\n  "
            + "\n  ".join(violations)
        )

    def test_every_published_topic_has_a_subscriber(self) -> None:
        """
        A topic nobody consumes is legal under Kafka — that decoupling is the
        point of stage 4 — but it is almost never intentional here. Flagging it
        catches the typo'd topic name, which otherwise publishes happily into a
        topic no group reads and reports nothing.

        Exempt a topic explicitly below if it really is fire-and-forget.
        """
        deliberately_unconsumed: set[str] = set()

        orphans = sorted(
            _published_topics() - set(_kafka_subscriptions()) - deliberately_unconsumed
        )
        assert not orphans, (
            f"These topics are published but nothing subscribes: {orphans}. "
            "A misspelled topic looks exactly like this and is silent."
        )


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
# Deployment wiring must match what the code declares.
#
# The failure these guard is silent by nature: work that is never done, with
# nothing in the application, Kubernetes, or the outbox reporting it. That was
# true of Celery queues nobody consumed and it is true of consumer groups nobody
# runs — only the mechanism changed.
# ---------------------------------------------------------------------------
CHART_VALUES = APPS_DIR.parent / "infra" / "helm" / "frikkinwave" / "values.yaml"


# ---------------------------------------------------------------------------
# Kafka credentials reach only the components that run the outbox relay.
#
# The relay and the consumer Deployments are the Kafka clients and need the
# credentials. Nothing else does: web pods only write the outbox row. Handing a
# credential to a pod that never uses it widens the blast radius of that pod being
# compromised, for no functionality — and it is the kind of thing that spreads
# quietly, because mounting a secret never breaks anything.
# ---------------------------------------------------------------------------


def test_the_relay_is_a_single_writer() -> None:
    """
    Concurrent relays are SAFE — rows are claimed with
    select_for_update(skip_locked=True) — but there is nothing to gain, and one
    writer keeps the logs readable during a deploy. More importantly this pins
    that a relay exists at all: with publish() no longer dispatching, it is the
    only path from the outbox to Kafka, and nothing is delivered without it.
    """
    import yaml

    values = yaml.safe_load(CHART_VALUES.read_text())
    assert "relay" in values, "No relay config in the chart — nothing would deliver events."

    template = (
        APPS_DIR.parent / "infra" / "helm" / "frikkinwave" / "templates" / "deployment-relay.yaml"
    ).read_text()
    assert "replicas: 1" in template
    assert "relay_outbox" in template and "--loop" in template


def test_nothing_dispatches_from_the_request_path() -> None:
    """
    publish() must not produce to Kafka inline. The produce is synchronous, so
    during a broker outage it would add up to KAFKA_FLUSH_TIMEOUT to every
    user-facing request — a Kafka problem becoming a website problem.

    EVENT_RELAY_INLINE exists only so tests can drive an event end to end; it
    must default to off.
    """
    import yaml
    from django.conf import settings

    values = yaml.safe_load(CHART_VALUES.read_text())
    assert values["config"].get("EVENT_RELAY_INLINE") in (None, False, "false"), (
        "EVENT_RELAY_INLINE must not be enabled in the deployed config."
    )
    # True under pytest by design (config/settings/local.py); the check above is
    # the one that matters, since production.py never sets it.
    assert settings.EVENT_RELAY_INLINE is True


# ---------------------------------------------------------------------------
# Kafka consumer groups (KAFKA.md stage 4).
#
# Kafka is the only transport; there is no registry left to compare against, so
# the source of truth is the code itself — what producers publish, and what apps
# subscribe to. These guard work that is never done with nothing reporting it.
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


def _published_topics() -> set[str]:
    """
    Every topic any service publishes, read off the `publish(topic=...)` call
    sites.

    Derived from the code rather than a hand-maintained table, because the table
    is what stage 5 deleted — and because this checks the real runtime
    requirement: `authorization: simple` denies any topic not granted, so a
    topic missing from the chart is a DENIED produce, not a warning.
    """
    topics: set[str] = set()
    for _app, path in _app_modules():
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = getattr(fn, "id", None) or getattr(fn, "attr", None)
            if name != "publish":
                continue
            for kw in node.keywords:
                if kw.arg == "topic" and isinstance(kw.value, ast.Constant):
                    topics.add(kw.value.value)
    return topics


def test_publish_call_sites_use_literal_topics() -> None:
    """
    The scan above can only see literal topic names. A computed one would be
    invisible to every guardrail here — silently unchecked against the chart's
    topics and ACLs, and therefore a produce that is denied in production and
    fine in tests.
    """
    assert len(_published_topics()) >= 13, (
        "Fewer literal publish(topic=...) call sites than expected — a topic is "
        "probably being built dynamically, which no guardrail here can see."
    )


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
    Celery tasks and erodes just as silently now, so it is asserted here too.
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


def test_the_relay_heartbeat_path_matches_the_probe() -> None:
    """
    The loop writes this file; the liveness probe reads it. A mismatch means the
    probe never sees a fresh heartbeat and restarts a perfectly healthy relay
    every failureThreshold — an outage manufactured by the thing meant to
    prevent one, and one that looks like a crash-looping app.
    """
    import yaml
    from django.conf import settings

    values = yaml.safe_load(CHART_VALUES.read_text())
    assert values["relay"]["heartbeatFile"] == settings.EVENT_RELAY_HEARTBEAT_FILE

    template = (
        APPS_DIR.parent / "infra" / "helm" / "frikkinwave" / "templates" / "deployment-relay.yaml"
    ).read_text()
    assert ".Values.relay.heartbeatFile" in template, (
        "The probe must read the path from values, not hardcode it."
    )


def test_the_relay_metrics_port_matches_the_scrape_target() -> None:
    """
    The loop binds this port; the PodMonitor scrapes it. A mismatch is silent in
    the worst way — the relay runs, the Deployment is Ready, and Prometheus
    simply has no series for it. `OutboxRelayDown` then fires permanently (a
    target that never came up) or not at all, depending on which side is wrong.
    """
    import yaml
    from django.conf import settings

    values = yaml.safe_load(CHART_VALUES.read_text())
    assert values["relay"]["metricsPort"] == settings.EVENT_RELAY_METRICS_PORT

    template = (
        APPS_DIR.parent / "infra" / "helm" / "frikkinwave" / "templates" / "deployment-relay.yaml"
    ).read_text()
    assert ".Values.relay.metricsPort" in template, (
        "The container port must come from values, not be hardcoded."
    )

    # The PodMonitor selects by port NAME, so the name is the actual contract.
    monitor = (
        APPS_DIR.parent / "infra" / "helm" / "frikkinwave" / "templates" / "monitoring.yaml"
    ).read_text()
    assert "port: metrics" in monitor
    assert "name: metrics" in template


def test_the_heartbeat_tolerance_exceeds_the_poll_interval() -> None:
    """
    If the probe's tolerance were tighter than the loop's idle sleep, a relay
    with nothing to do would look wedged and be restarted continuously.
    """
    import yaml
    from django.conf import settings

    values = yaml.safe_load(CHART_VALUES.read_text())
    assert values["relay"]["heartbeatMaxAgeSeconds"] > settings.EVENT_RELAY_INTERVAL * 5


def test_database_connections_are_reused_across_requests() -> None:
    """
    CONN_MAX_AGE=0 (Django's default) opens a fresh connection per request.

    Measured on RDS: the handshake costs 16.9ms while the query it exists to run
    costs 0.58ms, so reverting this silently spends ~29x the request's real work
    on getting a socket. That is invisible in review and invisible in tests —
    only a load test shows it — so it is asserted here.

    CONN_HEALTH_CHECKS is half the fix: without it a connection the server closed
    while idle resurfaces as an OperationalError instead of a reconnect.
    """
    from django.conf import settings

    default = settings.DATABASES["default"]
    assert default.get("CONN_MAX_AGE", 0) > 0, (
        "Persistent DB connections are load-bearing — see TESTING.md section 4.1."
    )
    assert default.get("CONN_HEALTH_CHECKS") is True, (
        "Reusing connections without health checks trades latency for flakiness."
    )


# A GET that fetches a collection but never paginates it returns the whole table
# once the table is big. These four do it deliberately; everything else must not.
UNPAGINATED_BY_DESIGN = {
    ("musicians", "InstrumentListView"): "reference data - a fixed, small vocabulary",
    ("musicians", "GenreListView"): "reference data - a fixed, small vocabulary",
    ("musicians", "ProfileSearchView"): "bounded by its own ?limit= (see _parse_limit)",
    ("bands", "BandDetailView"): "members nested in a detail response, bounded by band size",
}


def test_every_list_endpoint_paginates() -> None:
    """
    A view that fetches a collection must hand it to a paginator.

    Cursor pagination is already used consistently (page_size 20, no client
    override), but nothing enforced it: a new endpoint returning a bare queryset
    looks identical in review and only misbehaves once the table grows. Add a
    genuine exception to UNPAGINATED_BY_DESIGN with a reason rather than
    weakening this.
    """
    offenders = []
    for path in sorted((APPS_DIR).glob("*/views.py")):
        app = path.parent.name
        for cls in [
            n for n in ast.walk(ast.parse(path.read_text())) if isinstance(n, ast.ClassDef)
        ]:
            for fn in [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "get"]:
                calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call)]
                fetches_list = any(
                    isinstance(c.func, ast.Name)
                    and (c.func.id.startswith("list_") or c.func.id.startswith("search_"))
                    for c in calls
                )
                paginates = any(
                    isinstance(c.func, ast.Attribute) and c.func.attr == "paginate_queryset"
                    for c in calls
                )
                if fetches_list and not paginates and (app, cls.name) not in UNPAGINATED_BY_DESIGN:
                    offenders.append(f"{app}.{cls.name}.get")

    assert not offenders, (
        "These GET handlers fetch a collection without paginating it: " + ", ".join(offenders)
    )
