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
