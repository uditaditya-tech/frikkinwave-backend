"""
Outbox sweep — the component that makes delivery guaranteed.

`publish()` fires a best-effort relay nudge after commit; this command is the
backstop that picks up anything the nudge lost (worker restart, broker blip).
Run it on a schedule — a Kubernetes CronJob, or ECS scheduled task.

    python manage.py relay_outbox [--limit 100]
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand

from apps.events.services import DEFAULT_BATCH, relay_pending


class Command(BaseCommand):
    help = "Dispatch pending transactional-outbox events to their consumers."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--limit", type=int, default=DEFAULT_BATCH)

    def handle(self, *args: Any, **opts: Any) -> None:
        count = relay_pending(limit=opts["limit"])
        self.stdout.write(self.style.SUCCESS(f"Relayed {count} event(s)."))
