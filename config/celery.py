"""
Celery application for frikkinwave.

Async work (email notifications, embedding generation) runs through Celery
with a Redis broker. Per the "Events for async work" scale rule in CLAUDE.md,
tasks are wired as event handlers — never called inline from a view.

This module instantiates the single Celery app. It is imported in
``config/__init__.py`` so Django and the worker share one configured instance,
and so ``@shared_task`` decorators bind to it. Task modules live in each app's
``tasks.py`` and are auto-discovered from ``INSTALLED_APPS``.
"""

import os

from celery import Celery

# Mirror manage.py's default so a bare `celery -A config worker` works in dev.
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")

app = Celery("frikkinwave")

# All CELERY_* keys in Django settings configure the app (namespace strips the
# prefix: CELERY_BROKER_URL → broker_url).
app.config_from_object("django.conf:settings", namespace="CELERY")

# Discover tasks.py in every installed app.
app.autodiscover_tasks()


@app.task(name="config.debug_task")
def debug_task() -> str:
    """Trivial task to verify the Celery wiring end-to-end."""
    return "ok"
