"""
Transactional outbox behaviour.

The guarantee under test: a domain event and the state change it describes commit
together, and delivery is at-least-once with no permanent loss.
"""

from collections.abc import Callable
from typing import Any

import pytest
from django.core.management import call_command
from django.db import transaction

from apps.events.models import OutboxEvent
from apps.events.registry import EVENT_HANDLERS
from apps.events.services import MAX_ATTEMPTS, publish, relay_pending
from apps.social.models import Activity, FeedEntry, Follow
from apps.users.models import User


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

    def test_unknown_topic_is_parked_not_lost(self) -> None:
        publish(topic="nope.unknown", payload={})
        assert relay_pending() == 0
        event = OutboxEvent.objects.get()
        assert event.published_at is None  # retained for inspection
        assert event.attempts == 1
        assert "No consumer registered" in event.last_error

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
    def test_every_registered_topic_maps_to_a_real_task(self) -> None:
        from config.celery import app as celery_app

        # Importing tasks registers them; autodiscovery does this at worker boot.
        celery_app.loader.import_default_modules()
        for topic, task_name in EVENT_HANDLERS.items():
            assert task_name in celery_app.tasks, f"{topic} -> missing task {task_name}"


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
