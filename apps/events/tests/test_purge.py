"""
Outbox retention.

The outbox is a queue, not a ledger, so published rows are deleted after a
window. What these tests mostly guard is the *other* half of that sentence:
which rows must survive regardless of age.

An unpublished row is the only copy of its event — nothing reached Kafka — so
deleting one destroys the event outright, and for an exhausted one it also
destroys the `last_error` that explains what went wrong. Age alone must never be
enough. Most of the assertions below are about rows that should still be there.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.core.management import call_command
from django.utils import timezone

from apps.events.models import OutboxEvent
from apps.events.services import MAX_ATTEMPTS, purge_published

RETENTION = timedelta(days=7)


def _event(*, published_days_ago: float | None = None, attempts: int = 0) -> OutboxEvent:
    """Create an outbox row, optionally already published and backdated."""
    event = OutboxEvent.objects.create(
        topic="profile.updated",
        payload={"profile_id": "x", "recipient_email": "someone@example.com"},
        attempts=attempts,
    )
    if published_days_ago is not None:
        OutboxEvent.objects.filter(pk=event.pk).update(
            published_at=timezone.now() - timedelta(days=published_days_ago)
        )
    return event


@pytest.mark.django_db
class TestPurgeDeletesWhatItShould:
    def test_published_rows_past_the_window_are_deleted(self) -> None:
        _event(published_days_ago=30)
        _event(published_days_ago=8)

        assert purge_published(older_than=RETENTION) == 2
        assert OutboxEvent.objects.count() == 0

    def test_it_reports_how_many_it_removed(self) -> None:
        for _ in range(3):
            _event(published_days_ago=10)
        _event(published_days_ago=1)

        assert purge_published(older_than=RETENTION) == 3
        assert OutboxEvent.objects.count() == 1


@pytest.mark.django_db
class TestPurgeKeepsWhatItMust:
    def test_unpublished_rows_are_never_deleted_however_old(self) -> None:
        """
        The safety property.

        A pending row has not reached Kafka, so the table is the only place the
        event exists. Purging on age alone would silently destroy it — and it
        would look like successful cleanup.
        """
        old_pending = _event()
        OutboxEvent.objects.filter(pk=old_pending.pk).update(
            created_at=timezone.now() - timedelta(days=365)
        )

        assert purge_published(older_than=RETENTION) == 0
        assert OutboxEvent.objects.filter(pk=old_pending.pk).exists()

    def test_exhausted_rows_survive_too(self) -> None:
        """
        An event that gave up is evidence, not garbage.

        It carries `last_error` and is the only record of what could not be
        delivered. It is also unpublished, so the same rule protects it — this
        asserts that explicitly, because "clean up dead events" is exactly the
        kind of change someone would later think this command should make.
        """
        dead = _event(attempts=MAX_ATTEMPTS)
        OutboxEvent.objects.filter(pk=dead.pk).update(
            created_at=timezone.now() - timedelta(days=99)
        )

        assert purge_published(older_than=RETENTION) == 0
        assert OutboxEvent.objects.filter(pk=dead.pk).exists()

    def test_published_rows_inside_the_window_survive(self) -> None:
        recent = _event(published_days_ago=1)

        assert purge_published(older_than=RETENTION) == 0
        assert OutboxEvent.objects.filter(pk=recent.pk).exists()


@pytest.mark.django_db
class TestPurgeCommand:
    def test_dry_run_deletes_nothing(self) -> None:
        _event(published_days_ago=30)

        call_command("purge_outbox", "--dry-run")

        assert OutboxEvent.objects.count() == 1

    def test_command_honours_the_window(self) -> None:
        _event(published_days_ago=5)
        _event(published_days_ago=30)

        call_command("purge_outbox", "--older-than-days", "10")

        remaining = OutboxEvent.objects.get()
        assert remaining.published_at is not None
