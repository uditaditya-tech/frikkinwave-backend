"""
Transactional outbox behaviour.

The guarantee under test: a domain event and the state change it describes commit
together, and delivery is at-least-once with no permanent loss.
"""

import pathlib
from collections.abc import Callable
from typing import Any

import pytest
from django.core.management import call_command
from django.db import transaction
from django.utils import timezone

from apps.events.models import OutboxEvent
from apps.events.services import (
    MAX_ATTEMPTS,
    RETRY_BASE_SECONDS,
    RETRY_CAP_SECONDS,
    _retry_delay,
    publish,
    relay_pending,
)
from apps.social.models import Activity, FeedEntry, Follow
from apps.users.models import User

CHART_VALUES = (
    pathlib.Path(__file__).resolve().parents[3] / "infra" / "helm" / "frikkinwave" / "values.yaml"
)


def _make_user(suffix: str) -> User:
    return User.objects.create_user(
        email=f"{suffix}@example.com", username=suffix, password="StrongPass123!"
    )


@pytest.mark.django_db
class TestAtomicity:
    def test_publish_writes_a_pending_event(self) -> None:
        publish(topic="review.created", payload={"subject_user_id": "abc"})
        event = OutboxEvent.objects.get()
        assert event.topic == "review.created"
        assert event.published_at is None
        assert event.attempts == 0

    def test_rolled_back_transaction_discards_the_event(self) -> None:
        class Boom(Exception):
            pass

        with pytest.raises(Boom), transaction.atomic():
            publish(topic="review.created", payload={"subject_user_id": "abc"})
            raise Boom
        # The whole point: no event survives a rolled-back state change.
        assert OutboxEvent.objects.count() == 0

    def test_domain_write_and_event_commit_together(
        self, django_capture_on_commit_callbacks: Callable[..., Any]
    ) -> None:
        alice, bob = _make_user("alice"), _make_user("bob")
        with django_capture_on_commit_callbacks(execute=False):
            Follow.objects.create(follower=alice, followed=bob)
            publish(topic="follow.created", payload={"follower_id": "x", "followed_id": "y"})
        assert Follow.objects.count() == 1
        assert OutboxEvent.objects.filter(topic="follow.created").count() == 1


@pytest.mark.django_db
class TestRelay:
    def test_relay_dispatches_and_marks_published(self) -> None:
        publish(topic="review.created", payload={"subject_user_id": str(_make_user("s").pk)})
        assert relay_pending() == 1
        event = OutboxEvent.objects.get()
        assert event.published_at is not None
        assert event.attempts == 1

    def test_relay_is_idempotent_across_runs(self) -> None:
        publish(topic="review.created", payload={"subject_user_id": str(_make_user("s2").pk)})
        assert relay_pending() == 1
        assert relay_pending() == 0  # nothing left pending

    def test_an_unconsumed_topic_is_published_not_parked(self) -> None:
        """
        The producer no longer knows who consumes, so it cannot park an event for
        having no consumer — see KAFKA.md stage 4. Delivery is the broker's
        problem now; a topic nobody reads is a valid, if usually accidental,
        state.
        """
        event = publish(topic="nobody.listens", payload={"x": 1})

        assert relay_pending() == 1
        event.refresh_from_db()
        assert event.published_at is not None
        assert event.last_error == ""

    def test_exhausted_events_stop_being_retried(self) -> None:
        publish(topic="nope.unknown", payload={})
        OutboxEvent.objects.update(attempts=MAX_ATTEMPTS)
        assert relay_pending() == 0
        assert OutboxEvent.objects.get().attempts == MAX_ATTEMPTS  # not incremented again

    def test_sweep_command_runs(self) -> None:
        publish(topic="review.created", payload={"subject_user_id": str(_make_user("s3").pk)})
        call_command("relay_outbox")
        assert OutboxEvent.objects.get().published_at is not None


@pytest.mark.django_db
class TestRegistry:
    def test_every_subscribed_topic_resolves_to_a_callable(self) -> None:
        """
        Replaces the old "every registry entry names a real Celery task" check.
        The registry is gone; the equivalent breakage now is a consumers.py
        entry pointing at something that is not callable, which fails only when
        that topic is next delivered.
        """
        from apps.events.consumer import load_subscriptions

        for app in ("notifications", "search", "social", "reviews"):
            for topic, handler in load_subscriptions(app).items():
                assert callable(handler), f"{app}: {topic} -> {handler!r} is not callable"


@pytest.mark.django_db
class TestConsumerIdempotency:
    def test_redelivered_activity_does_not_duplicate_the_feed(
        self, django_capture_on_commit_callbacks: Callable[..., Any]
    ) -> None:
        """At-least-once delivery must not post the same activity twice."""
        from apps.social.services import fan_out_activity

        actor, follower = _make_user("actor"), _make_user("follower")
        Follow.objects.create(follower=follower, followed=actor)

        payload: dict[str, Any] = {
            "actor_id": str(actor.pk),
            "verb": "posted_listing",
            "summary": "Drummer wanted",
            "target_type": "listing",
            "target_id": None,
            "target_slug": "",
            "event_id": "018f0000-0000-7000-8000-000000000001",
        }
        fan_out_activity(**payload)
        fan_out_activity(**payload)  # redelivery

        assert Activity.objects.count() == 1
        assert FeedEntry.objects.count() == 2  # actor + follower, not doubled


@pytest.mark.django_db
class TestRetryBackoff:
    """
    Why retries are spaced out.

    They used to run at the poll interval — one per second, unspaced — so
    MAX_ATTEMPTS was spent in about ten seconds. Any broker outage longer than
    that left every pending event exhausted: never retried again, needing a
    human. That was measured on a live cluster, not inferred: five events denied
    at the broker were all exhausted within 45 seconds while the relay sat there
    perfectly healthy.
    """

    def _failing_dispatch(self, monkeypatch: Any) -> None:
        def boom(**kwargs: Any) -> None:
            raise RuntimeError("broker unreachable")

        monkeypatch.setattr("apps.events.services._dispatch", boom)

    def test_a_failure_schedules_the_next_attempt_in_the_future(self, monkeypatch: Any) -> None:
        self._failing_dispatch(monkeypatch)
        event = OutboxEvent.objects.create(topic="follow.created", payload={})

        relay_pending()

        event.refresh_from_db()
        assert event.attempts == 1
        assert event.next_attempt_at > timezone.now()

    def test_an_event_not_yet_due_is_skipped(self, monkeypatch: Any) -> None:
        """
        The backoff itself. Without this the relay would retry on every poll and
        the spacing would be decorative.
        """
        self._failing_dispatch(monkeypatch)
        event = OutboxEvent.objects.create(topic="follow.created", payload={})

        relay_pending()
        relay_pending()
        relay_pending()

        event.refresh_from_db()
        assert event.attempts == 1, "a not-yet-due event must not be retried"

    def test_a_due_event_is_retried(self, monkeypatch: Any) -> None:
        self._failing_dispatch(monkeypatch)
        event = OutboxEvent.objects.create(topic="follow.created", payload={})
        relay_pending()

        OutboxEvent.objects.filter(pk=event.pk).update(next_attempt_at=timezone.now())
        relay_pending()

        event.refresh_from_db()
        assert event.attempts == 2

    def test_the_delay_grows_and_is_capped(self) -> None:
        delays = [_retry_delay(n).total_seconds() for n in range(MAX_ATTEMPTS)]

        assert delays[0] == RETRY_BASE_SECONDS
        assert delays == sorted(delays), "backoff must be monotonic"
        assert max(delays) == RETRY_CAP_SECONDS, "and bounded"

    def test_the_retry_window_outlasts_a_realistic_outage(self) -> None:
        """
        The number that matters, pinned so it cannot regress quietly.

        This is the outage an event survives before it exhausts and needs a
        human. It was TEN SECONDS. If a change drops it back near that, the
        durability claim in KAFKA.md becomes false again.
        """
        window = sum(_retry_delay(n).total_seconds() for n in range(MAX_ATTEMPTS))

        assert window > 20 * 60, f"retry window is only {window / 60:.1f} minutes"

    def test_backoff_warns_before_it_strands(self) -> None:
        """
        Ordering property, not a coincidence: OutboxNotDraining fires at 300s
        (+5m `for`), which must land BEFORE events exhaust — otherwise the alert
        arrives to tell you about something already unrecoverable.
        """
        import yaml

        values = yaml.safe_load(CHART_VALUES.read_text())
        alert_at = values["monitoring"]["outboxMaxAgeSeconds"] + 5 * 60
        window = sum(_retry_delay(n).total_seconds() for n in range(MAX_ATTEMPTS))

        assert alert_at < window, (
            f"alert fires at {alert_at}s but events strand at {window}s — "
            "you would be told only after it was too late"
        )
