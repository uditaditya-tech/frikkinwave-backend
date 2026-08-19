"""
The Kafka transport (KAFKA.md stages 3 and 5).

What matters here is not that a message reaches Kafka — it is that the outbox's
guarantee holds. An event may only be marked published once the broker has
acknowledged it; anything else silently converts "at-least-once" into "usually".

Every test here sets `EVENT_RELAY_INLINE = False` to exercise the real dispatch
path. It is True under pytest by default so that the rest of the suite can drive
events through their handlers without a broker.

No broker is involved: the producer is faked, so CI needs no Kafka and makes no
network calls.
"""

from __future__ import annotations

from typing import Any

import pytest

from apps.events.kafka import KafkaUnavailableError
from apps.events.services import publish, relay_pending

TOPIC = "follow.created"
PAYLOAD = {"follower_id": "a", "followed_id": "b"}


class FakeProducer:
    """Records what was produced; optionally fails the way the real one would."""

    def __init__(self, fail: bool = False) -> None:
        self.produced: list[tuple[str, dict[str, Any]]] = []
        self.fail = fail

    def __call__(self, *, topic: str, payload: dict[str, Any], key: str | None = None) -> None:
        if self.fail:
            raise KafkaUnavailableError("broker down")
        self.produced.append((topic, payload))


@pytest.fixture
def fake_produce(monkeypatch: pytest.MonkeyPatch) -> FakeProducer:
    fake = FakeProducer()
    monkeypatch.setattr("apps.events.kafka.produce", fake)
    return fake


@pytest.fixture(autouse=True)
def _use_the_real_dispatch_path(settings: Any) -> None:
    """Exercise the producer rather than the in-process test stand-in."""
    settings.EVENT_RELAY_INLINE = False


@pytest.mark.django_db
class TestDispatch:
    def test_the_relay_produces_to_the_event_topic(self, fake_produce: FakeProducer) -> None:
        publish(topic=TOPIC, payload=PAYLOAD)

        assert relay_pending() == 1
        assert fake_produce.produced == [(TOPIC, PAYLOAD)]

    def test_publish_alone_dispatches_nothing(self, fake_produce: FakeProducer) -> None:
        """
        publish() records the event and returns. A synchronous produce in the
        request path would add up to KAFKA_FLUSH_TIMEOUT to every user-facing
        request during a broker outage — a Kafka problem becoming a site problem.
        The relay Deployment does the dispatching.
        """
        publish(topic=TOPIC, payload=PAYLOAD)
        assert fake_produce.produced == []


@pytest.mark.django_db
class TestTheOutboxGuaranteeSurvives:
    """The reason this transport swap is riskier than its diff suggests."""

    def test_a_produce_failure_leaves_the_event_unpublished(
        self, settings: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """
        If a broker failure marked the event published, the event would be lost
        with the database claiming otherwise — exactly the failure the outbox
        exists to prevent.
        """
        settings.EVENT_TRANSPORT = "kafka"
        monkeypatch.setattr("apps.events.kafka.produce", FakeProducer(fail=True))
        event = publish(topic=TOPIC, payload=PAYLOAD)

        assert relay_pending() == 0

        event.refresh_from_db()
        assert event.published_at is None
        assert "broker down" in event.last_error
        assert event.attempts == 1

    def test_a_failed_event_is_retried_and_can_succeed_later(
        self, settings: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A broker outage must delay delivery, never cancel it."""
        settings.EVENT_TRANSPORT = "kafka"
        monkeypatch.setattr("apps.events.kafka.produce", FakeProducer(fail=True))
        event = publish(topic=TOPIC, payload=PAYLOAD)
        relay_pending()

        working = FakeProducer()
        monkeypatch.setattr("apps.events.kafka.produce", working)
        assert relay_pending() == 1

        event.refresh_from_db()
        assert event.published_at is not None
        assert working.produced == [(TOPIC, PAYLOAD)]

    def test_a_published_event_is_not_produced_twice(
        self, settings: Any, fake_produce: FakeProducer
    ) -> None:
        settings.EVENT_TRANSPORT = "kafka"
        publish(topic=TOPIC, payload=PAYLOAD)
        relay_pending()
        relay_pending()

        assert len(fake_produce.produced) == 1


@pytest.mark.django_db
class TestTopicsWithoutConsumers:
    def test_a_topic_nobody_consumes_is_still_published(self, fake_produce: FakeProducer) -> None:
        """
        Under Celery an unregistered topic was parked with "No consumer
        registered" — the producer knew its consumers and could tell. It no
        longer does, and that is the point of stage 4: this is simply a topic
        nobody has subscribed to yet. Parking it would reintroduce exactly the
        coupling the migration removed.
        """
        publish(topic="nobody.listens", payload={"x": 1})

        assert relay_pending() == 1
        assert fake_produce.produced == [("nobody.listens", {"x": 1})]
