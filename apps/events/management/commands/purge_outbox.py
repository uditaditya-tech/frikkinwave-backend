"""
Delete outbox rows that have already been published.

    python manage.py purge_outbox [--older-than-days 7] [--dry-run]

The outbox is a queue, not a ledger. A published row has done its job and Kafka
is the durable record from that point on — but nothing deleted them, so the
table grew without bound and carried recipient email addresses in `payload` for
as long as it lived. This is the retention policy that was missing.

**It never touches an unpublished row.** Pending and exhausted events have no
`published_at`, and nothing of theirs reached Kafka, so deleting one destroys
the event outright along with the `last_error` explaining what went wrong.
`purge_published` enforces that; this command only chooses the window.

Runs daily as a Helm CronJob. Safe to run by hand at any time, and safe to run
concurrently with the relay: the relay only reads rows with `published_at IS
NULL`, which is precisely the set this leaves alone.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.events.models import OutboxEvent
from apps.events.services import purge_published


class Command(BaseCommand):
    help = "Delete published outbox events older than the retention window."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--older-than-days",
            type=int,
            default=settings.EVENT_OUTBOX_RETENTION_DAYS,
            help=(
                "How long a published event is kept. Defaults to "
                "EVENT_OUTBOX_RETENTION_DAYS (currently "
                f"{settings.EVENT_OUTBOX_RETENTION_DAYS})."
            ),
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report how many rows would be deleted, and delete nothing.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        days: int = options["older_than_days"]
        window = timedelta(days=days)

        if options["dry_run"]:
            cutoff = timezone.now() - window
            count = OutboxEvent.objects.filter(
                published_at__isnull=False,
                published_at__lt=cutoff,
            ).count()
            self.stdout.write(f"Would delete {count} published event(s) older than {days}d.")
            return

        deleted = purge_published(older_than=window)
        self.stdout.write(
            self.style.SUCCESS(f"Deleted {deleted} published event(s) older than {days}d.")
        )
