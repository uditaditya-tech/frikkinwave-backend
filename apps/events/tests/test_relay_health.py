"""
Can we tell when the event system has stopped working?

This is the gap the migration left. The outbox means a broken relay loses
nothing — events queue up — but nothing anywhere *reports* that they stopped
moving. `publish()` keeps succeeding, rows keep committing, and the system looks
healthy from every angle a human normally checks.
"""

from __future__ import annotations

import pathlib
from datetime import timedelta
from typing import Any

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from apps.events.models import OutboxEvent
from apps.events.services import MAX_ATTEMPTS, OutboxLag, outbox_lag_snapshot


def _age(event: OutboxEvent, seconds: int) -> None:
    """Backdate an event — auto_now_add means created_at cannot be set directly."""
    OutboxEvent.objects.filter(pk=event.pk).update(
        created_at=timezone.now() - timedelta(seconds=seconds)
    )


@pytest.mark.django_db
class TestOutboxLagCheck:
    def test_a_draining_outbox_passes(self) -> None:
        call_command("check_outbox_lag", max_seconds=300)

    def test_a_published_backlog_is_not_lag(self) -> None:
        """Only *unpublished* events count. History must not trip the alarm."""
        e = OutboxEvent.objects.create(topic="follow.created", payload={})
        _age(e, 99999)
        OutboxEvent.objects.filter(pk=e.pk).update(published_at=timezone.now())

        call_command("check_outbox_lag", max_seconds=60)

    def test_a_stalled_outbox_fails(self) -> None:
        """
        The signal that matters. It rises whether the relay is down, wedged, or
        Kafka is unreachable — one number for every failure between publish()
        and the broker.
        """
        _age(OutboxEvent.objects.create(topic="follow.created", payload={}), 600)

        with pytest.raises(CommandError, match="not draining"):
            call_command("check_outbox_lag", max_seconds=300)

    def test_exhausted_events_fail_even_when_recent(self) -> None:
        """
        An event that burned through its attempts will never be retried, so it
        stops aging the lag number once everything else drains — it would go
        quiet precisely when it most needs a human.
        """
        OutboxEvent.objects.create(
            topic="follow.created", payload={}, attempts=MAX_ATTEMPTS, last_error="boom"
        )

        with pytest.raises(CommandError, match="exhausted"):
            call_command("check_outbox_lag", max_seconds=300)

    def test_a_fresh_pending_event_is_not_a_failure(self) -> None:
        """The relay polls every second; an event mid-flight is normal."""
        OutboxEvent.objects.create(topic="follow.created", payload={})
        call_command("check_outbox_lag", max_seconds=300)


class TestRelayHeartbeat:
    def test_the_loop_touches_the_heartbeat(self, settings: Any, tmp_path: pathlib.Path) -> None:
        """
        The liveness probe reads this file's mtime. Without it a wedged relay —
        process alive, loop stuck — is invisible to Kubernetes, which only
        checks that the process exists.
        """
        from apps.events.management.commands.relay_outbox import _touch_heartbeat

        beat = tmp_path / "relay-heartbeat"
        settings.EVENT_RELAY_HEARTBEAT_FILE = str(beat)

        assert not beat.exists()
        _touch_heartbeat()
        assert beat.exists()

    def test_an_unwritable_heartbeat_does_not_stop_the_relay(
        self, settings: Any, tmp_path: pathlib.Path
    ) -> None:
        """
        Delivering events matters more than reporting liveness. If the heartbeat
        cannot be written the probe will restart the pod, which is the right
        outcome — but the loop must not crash on the way there.
        """
        from apps.events.management.commands.relay_outbox import _touch_heartbeat

        settings.EVENT_RELAY_HEARTBEAT_FILE = str(tmp_path / "nope" / "deeper" / "beat")
        _touch_heartbeat()  # must not raise


@pytest.mark.django_db
class TestOutboxLagSnapshot:
    """
    The same reading `check_outbox_lag` exits on, exported as a gauge instead.
    One query, two readers — an alert that disagreed with the command it
    replaced would be worse than either signal alone.
    """

    def test_an_empty_outbox_reads_zero(self) -> None:
        snapshot = outbox_lag_snapshot()

        assert snapshot == OutboxLag(pending=0, exhausted=0, oldest_age_seconds=0.0)

    def test_the_age_tracks_the_oldest_unpublished_event(self) -> None:
        _age(OutboxEvent.objects.create(topic="follow.created", payload={}), 600)
        OutboxEvent.objects.create(topic="follow.created", payload={})

        snapshot = outbox_lag_snapshot()

        assert snapshot.pending == 2
        assert snapshot.oldest_age_seconds == pytest.approx(600, abs=10)

    def test_published_history_does_not_age_the_gauge(self) -> None:
        """
        Otherwise the metric would climb forever on a perfectly healthy system,
        and the alert would be permanently firing — which trains people to
        silence it.
        """
        old = OutboxEvent.objects.create(topic="follow.created", payload={})
        _age(old, 99999)
        OutboxEvent.objects.filter(pk=old.pk).update(published_at=timezone.now())

        snapshot = outbox_lag_snapshot()

        assert snapshot.pending == 0
        assert snapshot.oldest_age_seconds == 0.0

    def test_exhausted_events_are_counted_but_do_not_age_the_gauge(self) -> None:
        """
        They will never be retried, so they stop ageing once everything
        retryable drains — the outbox would read healthy with an event stuck in
        it forever. That is why they get their own gauge and their own alert.
        """
        _age(
            OutboxEvent.objects.create(
                topic="follow.created", payload={}, attempts=MAX_ATTEMPTS, last_error="boom"
            ),
            99999,
        )

        snapshot = outbox_lag_snapshot()

        assert snapshot.pending == 1
        assert snapshot.exhausted == 1
        assert snapshot.oldest_age_seconds == 0.0

    def test_the_relay_loop_exports_the_snapshot(self) -> None:
        """
        The gauges must reflect the database, not stay at their zero defaults —
        a metric wired to nothing looks identical to a healthy system.
        """
        from apps.events.management.commands.relay_outbox import (
            OUTBOX_EXHAUSTED,
            OUTBOX_OLDEST,
            OUTBOX_PENDING,
            _publish_metrics,
        )

        _age(OutboxEvent.objects.create(topic="follow.created", payload={}), 600)
        OutboxEvent.objects.create(
            topic="follow.created", payload={}, attempts=MAX_ATTEMPTS, last_error="boom"
        )

        _publish_metrics()

        assert OUTBOX_PENDING._value.get() == 2
        assert OUTBOX_EXHAUSTED._value.get() == 1
        assert OUTBOX_OLDEST._value.get() == pytest.approx(600, abs=10)
