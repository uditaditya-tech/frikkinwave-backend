"""
Sub-step 2.1 — Celery + Redis foundation.

Verifies the Celery app is instantiated, exported from the config package, and
configured from Django settings, and that a task runs to completion (eager mode
is forced by the autouse fixture in the root conftest).
"""

from config import celery_app as exported_app
from config.celery import app as celery_app
from config.celery import debug_task


def test_config_package_exports_the_app() -> None:
    assert exported_app is celery_app
    assert celery_app.main == "frikkinwave"


def test_settings_are_loaded_from_django() -> None:
    assert celery_app.conf.broker_url
    assert celery_app.conf.task_serializer == "json"
    assert "json" in celery_app.conf.accept_content


def test_debug_task_runs() -> None:
    result = debug_task.delay()
    assert result.get() == "ok"
