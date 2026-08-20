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
Strimzi's exporter provides.

**The relay exports this same reading as a Prometheus gauge**, which is what
alerts on it now. This command is kept for running by hand — it is the fastest
way to answer "is the outbox draining?" from a shell, with no port-forward.
Both read `outbox_lag_snapshot()`, so they cannot disagree.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError

from apps.events.services import MAX_ATTEMPTS, outbox_lag_snapshot


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
        snapshot = outbox_lag_snapshot()
        exhausted = snapshot.exhausted
        lag = snapshot.oldest_age_seconds

        self.stdout.write(f"pending={snapshot.pending} exhausted={exhausted} oldest_lag={lag:.1f}s")

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
