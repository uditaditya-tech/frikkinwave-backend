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
from apps.events.services import MAX_ATTEMPTS


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
