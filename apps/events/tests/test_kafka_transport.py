"""
EVENT_TRANSPORT=kafka (KAFKA.md stage 3).

What matters here is not that a message reaches Kafka — it is that the outbox's
guarantee survives the change of transport. An event may only be marked
published once the broker has acknowledged it; anything else silently converts
"at-least-once" into "usually".

No broker is involved: the producer is faked, so CI needs no Kafka and makes no
network calls.
"""

from __future__ import annotations

from typing import Any

import pytest

from apps.events.kafka import KafkaUnavailableError
from apps.events.models import OutboxEvent
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


@pytest.mark.django_db
class TestTransportSelection:
    def test_the_default_transport_is_celery(self, settings: Any) -> None:
        """
        The safe state. Merging stage 3 must change nothing until the flag is
        flipped deliberately — the event backbone works today and is not worth
        betting on one deploy.
        """
        assert settings.EVENT_TRANSPORT == "celery"

    def test_kafka_transport_produces_to_the_event_topic(
        self, settings: Any, fake_produce: FakeProducer
    ) -> None:
        settings.EVENT_TRANSPORT = "kafka"
        publish(topic=TOPIC, payload=PAYLOAD)

        assert relay_pending() == 1
        assert fake_produce.produced == [(TOPIC, PAYLOAD)]

    def test_celery_transport_does_not_touch_kafka(
        self, settings: Any, fake_produce: FakeProducer
    ) -> None:
        settings.EVENT_TRANSPORT = "celery"
        publish(topic=TOPIC, payload=PAYLOAD)
        relay_pending()

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
    def test_kafka_publishes_a_topic_no_celery_task_consumes(
        self, settings: Any, fake_produce: FakeProducer
    ) -> None:
        """
        Under Celery an unregistered topic is parked — nothing would ever run it.
        Under Kafka it is simply a topic nobody has subscribed to yet, which is
        the decoupling stage 4 is built on. Parking it would reintroduce exactly
        the producer-knows-its-consumers coupling Kafka is meant to remove.
        """
        settings.EVENT_TRANSPORT = "kafka"
        publish(topic="nobody.listens", payload={"x": 1})

        assert relay_pending() == 1
        assert fake_produce.produced == [("nobody.listens", {"x": 1})]

    def test_celery_still_parks_an_unregistered_topic(self, settings: Any) -> None:
        settings.EVENT_TRANSPORT = "celery"
        publish(topic="nobody.listens", payload={"x": 1})

        assert relay_pending() == 0
        event = OutboxEvent.objects.get(topic="nobody.listens")
        assert event.published_at is None
        assert "No consumer registered" in event.last_error


class TestProducerConfiguration:
    """Config is settings-driven so SCRAM -> mTLS is configuration, not code."""

    def test_missing_bootstrap_servers_is_a_domain_error(self, settings: Any) -> None:
        from apps.events.kafka import _producer_config

        settings.KAFKA_BOOTSTRAP_SERVERS = ""
        with pytest.raises(KafkaUnavailableError):
            _producer_config()

    def test_acks_all_and_idempotence_are_set(self, settings: Any) -> None:
        """
        acks=all pairs with the cluster's min.insync.replicas=2: two replicas
        must hold the message before a produce returns. acks=1 would return once
        the leader had it and lose it if that leader died before replicating.
        """
        from apps.events.kafka import _producer_config

        settings.KAFKA_BOOTSTRAP_SERVERS = "broker:9093"
        config = _producer_config()
        assert config["acks"] == "all"
        assert config["enable.idempotence"] is True

    def test_unset_credentials_are_omitted_not_sent_empty(self, settings: Any) -> None:
        """librdkafka rejects empty strings for these keys rather than ignoring them."""
        from apps.events.kafka import _producer_config

        settings.KAFKA_BOOTSTRAP_SERVERS = "broker:9093"
        settings.KAFKA_SASL_USERNAME = ""
        settings.KAFKA_SSL_CERTIFICATE_LOCATION = ""
        config = _producer_config()
        assert "sasl.username" not in config
        assert "ssl.certificate.location" not in config
