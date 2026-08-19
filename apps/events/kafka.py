"""
Kafka producer for the outbox relay (KAFKA.md stage 3).

Mirrors `apps/ai/client.py`: the SDK is imported in exactly one module, and its
exception types are converted into a domain error so nothing else has to know
what library is underneath. The relay catches that error, parks the event with
`last_error`, and retries on the next sweep.

**Producing is synchronous here, deliberately.** `relay_pending()` marks an event
published only after a successful hand-off — that is what makes delivery
at-least-once. `confluent_kafka`'s `produce()` returns immediately and buffers in
memory, so producing asynchronously would mark the row published while the
message was still unsent, and a crash would lose it with the database claiming
otherwise. That is precisely the failure the outbox exists to prevent, so the
convenience is not available to us.
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from typing import TYPE_CHECKING, Any

from django.conf import settings

if TYPE_CHECKING:  # pragma: no cover
    from confluent_kafka import Producer

logger = logging.getLogger(__name__)


class KafkaUnavailableError(RuntimeError):
    """
    The broker could not be reached, or refused the message.

    A domain error, not the SDK's: callers degrade without importing
    `confluent_kafka`, exactly as services degrade around `OpenAIUnavailableError`.
    """


def _producer_config() -> dict[str, Any]:
    """
    Build librdkafka config from settings.

    Driven entirely by settings rather than hardcoded so that switching the
    cluster from SASL/SCRAM to mTLS is a configuration change and not a code
    change — only the keys that are set are sent.
    """
    if not settings.KAFKA_BOOTSTRAP_SERVERS:
        raise KafkaUnavailableError("EVENT_TRANSPORT=kafka but KAFKA_BOOTSTRAP_SERVERS is empty.")

    config: dict[str, Any] = {
        "bootstrap.servers": settings.KAFKA_BOOTSTRAP_SERVERS,
        "security.protocol": settings.KAFKA_SECURITY_PROTOCOL,
        # acks=all + the cluster's min.insync.replicas=2 is the pairing that
        # makes a produce durable: two replicas must have it before this
        # returns. acks=1 would return as soon as the leader had it, and lose
        # the message if that leader died before replicating.
        "acks": "all",
        # librdkafka retries internally; the relay retries on top. Both are
        # safe because consumers are idempotent — delivery is at-least-once
        # under Celery too, so this changes nothing about that contract.
        "enable.idempotence": True,
    }

    optional = {
        "sasl.mechanism": settings.KAFKA_SASL_MECHANISM,
        "sasl.username": settings.KAFKA_SASL_USERNAME,
        "sasl.password": settings.KAFKA_SASL_PASSWORD,
        "ssl.ca.location": settings.KAFKA_SSL_CA_LOCATION,
        "ssl.certificate.location": settings.KAFKA_SSL_CERTIFICATE_LOCATION,
        "ssl.key.location": settings.KAFKA_SSL_KEY_LOCATION,
    }
    config.update({k: v for k, v in optional.items() if v})
    return config


@lru_cache(maxsize=1)
def get_producer() -> Producer:
    """
    The process-wide producer.

    Cached because a producer owns TCP connections and internal threads;
    building one per event would be both slow and a connection leak. Tests patch
    this function, so CI needs no broker and makes no network calls.
    """
    try:
        from confluent_kafka import Producer
    except ImportError as exc:  # pragma: no cover - dependency is in base.txt
        raise KafkaUnavailableError("confluent-kafka is not installed") from exc

    return Producer(_producer_config())


def produce(*, topic: str, payload: dict[str, Any], key: str | None = None) -> None:
    """
    Publish one event and **wait for the broker to acknowledge it**.

    Raises `KafkaUnavailableError` on any failure so the relay parks the event
    and retries rather than marking it published. Never returns successfully
    for a message the broker has not acknowledged.
    """
    producer = get_producer()
    failures: list[str] = []

    def _on_delivery(err: Any, _msg: Any) -> None:
        # librdkafka reports the outcome here, not from produce(). Without
        # inspecting it, a rejected message is indistinguishable from a
        # delivered one and the event would be marked published regardless.
        if err is not None:
            failures.append(str(err))

    try:
        producer.produce(
            topic,
            key=key.encode() if key else None,
            value=json.dumps(payload).encode(),
            on_delivery=_on_delivery,
        )
        remaining = producer.flush(settings.KAFKA_FLUSH_TIMEOUT)
    except Exception as exc:
        raise KafkaUnavailableError(f"produce to '{topic}' failed: {exc!r}") from exc

    if remaining:
        # flush() returns the number of messages STILL in the queue. Anything
        # non-zero means the timeout expired with the message unacknowledged —
        # it may yet be delivered, which is fine: the relay retries and
        # consumers are idempotent.
        raise KafkaUnavailableError(
            f"produce to '{topic}' timed out with {remaining} message(s) unacknowledged"
        )
    if failures:
        raise KafkaUnavailableError(f"produce to '{topic}' rejected: {failures[0]}")

    logger.info("kafka_produced", extra={"topic": topic})
