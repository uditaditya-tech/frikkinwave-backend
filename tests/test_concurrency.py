"""
Concurrency guarantees (TESTING.md, Gap B).

These need `transaction=True`: the default `db` fixture wraps each test in a
transaction that never commits, so a second thread would never see the first
thread's rows and `select_for_update` would have nothing to contend over. That
makes the ordinary fixture actively useless here — it would pass without
exercising anything.

The relay runs at 1 replica today, so none of this is load-bearing yet. It
becomes load-bearing the moment someone scales it, which is exactly when nobody
will re-derive whether `skip_locked` was doing its job.
"""

from __future__ import annotations

import threading
import uuid
from typing import Any

import pytest
from django.db import IntegrityError, connections, transaction

from apps.events import services as event_services
from apps.events.models import OutboxEvent
from apps.reviews.models import Review
from apps.users.models import User

RELAY_THREADS = 4
EVENT_COUNT = 40


def _make_user(suffix: str) -> User:
    return User.objects.create_user(
        email=f"{suffix}@example.com", username=suffix, password="StrongPass123!"
    )


def _run_in_threads(target: Any, count: int) -> list[BaseException]:
    """
    Run `target` in `count` threads, started together, and collect exceptions.

    Every thread closes its own DB connection: Django opens one per thread and
    leaves it open, which strands the test database at teardown.
    """
    errors: list[BaseException] = []
    barrier = threading.Barrier(count)

    def wrapped() -> None:
        try:
            barrier.wait()  # widen the race window - start all threads together
            target()
        except BaseException as exc:
            errors.append(exc)
        finally:
            connections.close_all()

    threads = [threading.Thread(target=wrapped) for _ in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    return errors


@pytest.mark.django_db(transaction=True)
class TestConcurrentRelays:
    def test_concurrent_relays_publish_each_event_exactly_once(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """
        B1 — `select_for_update(skip_locked=True)` is what makes >1 relay safe.

        Without it, two relays reading the same `pending_ids` both dispatch every
        row: the outbox would look perfectly clean while every consumer saw each
        event N times.
        """
        markers = [str(uuid.uuid4()) for _ in range(EVENT_COUNT)]
        OutboxEvent.objects.bulk_create(
            [OutboxEvent(topic="review.created", payload={"marker": m}) for m in markers]
        )

        dispatched: list[str] = []
        lock = threading.Lock()

        def _record(*, topic: str, payload: dict[str, Any]) -> None:
            with lock:
                dispatched.append(payload["marker"])

        monkeypatch.setattr(event_services, "_dispatch", _record)

        errors = _run_in_threads(
            lambda: event_services.relay_pending(limit=EVENT_COUNT), RELAY_THREADS
        )
        assert not errors, f"a relay thread raised: {errors!r}"

        assert sorted(dispatched) == sorted(markers), (
            f"expected each of {EVENT_COUNT} events dispatched exactly once, got "
            f"{len(dispatched)} dispatches ({len(set(dispatched))} distinct)"
        )
        assert OutboxEvent.objects.filter(published_at__isnull=True).count() == 0

    def test_a_dispatch_that_never_gets_marked_published_is_redelivered(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """
        B2 — delivery is at-least-once, and this is the case that makes it so.

        The broker accepts the event and the relay dies before writing
        `published_at`. The row is still pending, so the next pass sends it
        again. The duplicate is not a bug to fix; it is the contract, and it is
        why every consumer has to be idempotent.
        """
        OutboxEvent.objects.create(topic="review.created", payload={"marker": "once"})

        deliveries: list[str] = []
        crash = {"armed": True}

        def _deliver_then_maybe_crash(*, topic: str, payload: dict[str, Any]) -> None:
            deliveries.append(payload["marker"])
            if crash["armed"]:
                crash["armed"] = False
                raise RuntimeError("relay died after the broker accepted the event")

        monkeypatch.setattr(event_services, "_dispatch", _deliver_then_maybe_crash)

        event_services.relay_pending()
        assert deliveries == ["once"]
        event = OutboxEvent.objects.get()
        assert event.published_at is None, "a crashed dispatch must stay pending"

        # The backoff is real, so the retry is not due yet. Make it due rather
        # than sleeping through it.
        OutboxEvent.objects.update(next_attempt_at=event.created_at)

        event_services.relay_pending()
        assert deliveries == ["once", "once"], "the event must be redelivered"
        assert OutboxEvent.objects.get().published_at is not None


@pytest.mark.django_db(transaction=True)
class TestConcurrentWrites:
    def test_one_review_per_author_per_context_survives_a_race(self) -> None:
        """
        B3 — the uniqueness of a review is enforced by the database, not by a
        read-then-write check in the service.

        Two requests that both pass an "already reviewed?" check still commit,
        so only a constraint can be the arbiter. This asserts the constraint is
        actually the thing holding the line.
        """
        subject = _make_user("race-subject")
        author = _make_user("race-author")
        context_id = uuid.uuid4()
        attempts = 6

        def _create() -> None:
            try:
                with transaction.atomic():
                    Review.objects.create(
                        author=author,
                        subject=subject,
                        rating=5,
                        comment="Raced.",
                        context_type=Review.Context.ENGAGEMENT,
                        context_id=context_id,
                    )
            except IntegrityError:
                pass  # the constraint did its job - exactly what we want

        errors = _run_in_threads(_create, attempts)
        assert not errors, f"unexpected failure: {errors!r}"
        assert Review.objects.filter(author=author, context_id=context_id).count() == 1
