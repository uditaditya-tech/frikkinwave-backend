"""
Is the event system actually working?

    python manage.py check_outbox_lag [--max-seconds 300]

Exits non-zero when the oldest unpublished event is older than the threshold, or
when any event has exhausted its attempts.

**One number, many failures.** Outbox lag rises whether the relay is down, the
relay is wedged, Kafka is unreachable, the client certificate expired, an ACL is
missing, or a topic was never created. It does not care which — and that is the
point. Without it, every one of those is silent: `publish()` keeps succeeding,
the rows keep committing, and nothing anywhere reports that they stopped moving.

What it does NOT cover: consumer-side failures. A message that reached Kafka and
was never consumed leaves the outbox clean. That needs consumer-group lag, which
belongs with Prometheus (Phase 3).
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.events.models import OutboxEvent
from apps.events.services import MAX_ATTEMPTS


class Command(BaseCommand):
    help = "Fail if the outbox is not draining."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--max-seconds",
            type=int,
            default=300,
            help="How old the oldest unpublished event may be before this fails. "
            "Generous by default: the relay polls every second, so anything past "
            "a few minutes means it is not running, not that it is busy.",
        )

    def handle(self, *args: Any, **opts: Any) -> None:
        pending = OutboxEvent.objects.filter(published_at__isnull=True)

        # Exhausted events are a separate failure: they will never be retried
        # again, so they do not age the lag metric above once the rest drains.
        # They are the ones that need a human.
        exhausted = pending.filter(attempts__gte=MAX_ATTEMPTS).count()

        oldest = pending.exclude(attempts__gte=MAX_ATTEMPTS).order_by("created_at").first()
        lag = (timezone.now() - oldest.created_at).total_seconds() if oldest else 0.0

        self.stdout.write(f"pending={pending.count()} exhausted={exhausted} oldest_lag={lag:.1f}s")

        problems = []
        if lag > opts["max_seconds"]:
            problems.append(
                f"oldest unpublished event is {lag:.0f}s old (limit {opts['max_seconds']}s) "
                "— the relay is not draining the outbox"
            )
        if exhausted:
            problems.append(
                f"{exhausted} event(s) exhausted {MAX_ATTEMPTS} attempts and will never "
                "retry — inspect last_error on OutboxEvent"
            )

        if problems:
            raise CommandError("; ".join(problems))

        self.stdout.write(self.style.SUCCESS("outbox is draining"))
