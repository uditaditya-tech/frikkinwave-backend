"""
Kafka consumer runtime (KAFKA.md stage 4).

One process per consumer group. It resolves the group's subscriptions from that
app's own `consumers.py`, polls, dispatches, and commits — nothing here knows
which app it is running until it is told.

Three things Celery gave for free that this has to provide, and the reasons they
are not optional:

**A poison message blocks its PARTITION.** Under Celery a permanently failing
task was an isolated nuisance — it retried, gave up, and everything else kept
moving. Here, refusing to advance past a bad message halts every message behind
it on that partition. So a message that cannot be handled is sent to a
dead-letter topic and the offset is committed. Never blocking is the priority;
losing sight of the message is not acceptable, which is why it goes to the DLT
rather than being dropped.

**Offsets are committed manually, after the handler returns.** Auto-commit
acknowledges messages the handler may never have processed, turning
at-least-once into at-most-once silently. `enable.auto.commit` is off.

**Graceful shutdown.** A SIGTERM mid-batch must finish the message in flight and
commit it, or that work is redone on the next start. Kubernetes sends SIGTERM on
every rollout, so this is the common case, not the rare one.
"""

from __future__ import annotations

import importlib
import json
import logging
import signal
import time
from collections.abc import Callable
from types import FrameType
from typing import Any, Protocol, cast

from django.conf import settings

logger = logging.getLogger(__name__)

#: Topic used when a message arrives without one. Pathological, but the stubs
#: allow it and dropping such a message silently is exactly the behaviour this
#: module exists to avoid.
UNKNOWN_TOPIC = "unknown"


class MessageLike(Protocol):
    """The three accessors this runtime reads off a Kafka message."""

    def topic(self) -> str | None: ...

    def value(self) -> bytes | None: ...

    def error(self) -> Any: ...


class ConsumerLike(Protocol):
    """
    The four methods this runtime actually needs from a Kafka consumer.

    Structural rather than the concrete `confluent_kafka.Consumer`, so the tests
    can substitute a fake without the type checker objecting — and so the exact
    surface we depend on is written down rather than implied.
    """

    def subscribe(self, topics: list[str]) -> None: ...

    def poll(self, timeout: float) -> MessageLike | None: ...

    def commit(self, message: Any = ..., asynchronous: bool = ...) -> Any: ...

    def close(self) -> None: ...


class SubscriptionsNotFound(RuntimeError):
    """A group was named that no app declares subscriptions for."""


def load_subscriptions(group: str) -> dict[str, Callable[..., None]]:
    """
    Resolve `apps.<group>.consumers.SUBSCRIPTIONS`.

    By app label, deliberately: the group name IS the service name, so there is
    no lookup table mapping one to the other and therefore nothing to drift.
    """
    try:
        module = importlib.import_module(f"apps.{group}.consumers")
    except ModuleNotFoundError as exc:
        raise SubscriptionsNotFound(
            f"No apps.{group}.consumers module — a consumer group must match an "
            f"app that declares SUBSCRIPTIONS."
        ) from exc

    subscriptions = getattr(module, "SUBSCRIPTIONS", None)
    if not subscriptions:
        raise SubscriptionsNotFound(f"apps.{group}.consumers declares no SUBSCRIPTIONS.")
    return dict(subscriptions)


def _consumer_config(group: str) -> dict[str, Any]:
    """
    librdkafka consumer config, built from the same settings the producer uses so
    a security change (SCRAM -> mTLS) stays a configuration change.
    """
    from apps.events.kafka import KafkaUnavailableError, _producer_config

    config = _producer_config()
    # Producer-only keys; librdkafka rejects them on a consumer.
    for key in ("acks", "enable.idempotence"):
        config.pop(key, None)

    if not group:
        raise KafkaUnavailableError("A consumer group id is required.")

    config.update(
        {
            "group.id": f"{settings.KAFKA_CONSUMER_GROUP_PREFIX}.{group}",
            # Manual commits only — see the module docstring.
            "enable.auto.commit": False,
            # A new group starts from the beginning of the topic rather than
            # skipping everything published before it existed. Consumers are
            # idempotent, so replay is safe and losing history is not.
            "auto.offset.reset": "earliest",
        }
    )
    return config


def _dead_letter(*, topic: str, payload: bytes, group: str, error: str) -> None:
    """
    Park an unprocessable message so the partition can advance.

    Deliberately NOT a silent drop: the message, its origin and the error all go
    to `<topic>.dlt`, which is where you look when a consumer group is healthy
    and something is nonetheless missing.
    """
    from apps.events.kafka import produce

    produce(
        topic=f"{topic}{settings.KAFKA_DLT_SUFFIX}",
        payload={
            "original_topic": topic,
            "consumer_group": group,
            "error": error,
            "failed_at": time.time(),
            # Raw, because the reason it is here may well be that it would not
            # parse. Decoded leniently so the DLT record itself cannot fail.
            "payload": payload.decode("utf-8", errors="replace"),
        },
    )
    logger.error("event_dead_lettered", extra={"topic": topic, "group": group, "error": error})


def _handle(
    *,
    message: MessageLike,
    handlers: dict[str, Callable[..., None]],
    group: str,
) -> None:
    """
    Run one message through its handler, retrying briefly before dead-lettering.

    The retry is bounded and in-process: it covers the transient failure Celery's
    `retry_backoff` covered (a database blip, a slow upstream). It is NOT a
    substitute for tiered retry topics — the partition is stalled for its
    duration, so the bound has to be small.
    """
    topic = message.topic() or UNKNOWN_TOPIC
    handler = handlers.get(topic)
    if handler is None:
        # Subscribed to a topic with no handler: a wiring mistake, not a data
        # problem, so it must not be retried.
        _dead_letter(
            topic=topic,
            payload=message.value() or b"",
            group=group,
            error="no handler declared for this topic",
        )
        return

    raw = message.value() or b""
    try:
        payload = json.loads(raw)
    except (ValueError, TypeError) as exc:
        # Unparseable now is unparseable forever. Retrying blocks the partition
        # for no possible benefit.
        _dead_letter(topic=topic, payload=raw, group=group, error=f"malformed json: {exc!r}")
        return

    last_error = ""
    for attempt in range(1, settings.KAFKA_CONSUMER_MAX_ATTEMPTS + 1):
        try:
            handler(**payload)
            logger.info(
                "event_consumed",
                extra={"topic": topic, "group": group, "attempt": attempt},
            )
            return
        except Exception as exc:
            last_error = repr(exc)
            logger.warning(
                "event_handler_failed",
                extra={"topic": topic, "group": group, "attempt": attempt, "error": last_error},
            )
            if attempt < settings.KAFKA_CONSUMER_MAX_ATTEMPTS:
                time.sleep(settings.KAFKA_CONSUMER_RETRY_BACKOFF * attempt)

    _dead_letter(topic=topic, payload=raw, group=group, error=last_error)


class _Shutdown:
    """SIGTERM/SIGINT flag. Kubernetes sends SIGTERM on every rollout."""

    def __init__(self) -> None:
        self.requested = False
        signal.signal(signal.SIGTERM, self._request)
        signal.signal(signal.SIGINT, self._request)

    def _request(self, signum: int, frame: FrameType | None) -> None:
        logger.info("consumer_shutdown_requested", extra={"signal": signum})
        self.requested = True


def run(
    *, group: str, consumer: ConsumerLike | None = None, max_messages: int | None = None
) -> int:
    """
    Poll and dispatch until shutdown. Returns the number of messages handled.

    `consumer` and `max_messages` exist for tests and for smoke-testing a
    deployment — the runtime is otherwise a loop that never returns, which is
    not something a test can assert against. In that bounded mode an idle poll
    ends the run rather than waiting for more.
    """
    handlers = load_subscriptions(group)
    topics = sorted(handlers)

    client: ConsumerLike
    if consumer is None:  # pragma: no cover - needs a real broker
        from confluent_kafka import Consumer as KafkaConsumer

        client = cast("ConsumerLike", KafkaConsumer(_consumer_config(group)))
    else:
        client = consumer

    client.subscribe(topics)
    logger.info("consumer_started", extra={"group": group, "topics": topics})

    shutdown = _Shutdown()
    handled = 0
    try:
        while not shutdown.requested:
            if max_messages is not None and handled >= max_messages:
                break

            message = client.poll(settings.KAFKA_CONSUMER_POLL_TIMEOUT)
            if message is None:
                # An idle poll is normal and means "nothing right now".
                #
                # In BOUNDED mode it means the stream is drained, so stop — a
                # bounded run that spun forever on an empty topic would be a
                # smoke test that never returns, and a test suite that hangs.
                if max_messages is not None:
                    break
                continue
            if message.error():
                logger.error("consumer_poll_error", extra={"error": str(message.error())})
                continue

            _handle(message=message, handlers=handlers, group=group)
            # AFTER the handler. Committing earlier would acknowledge work that
            # may never have happened.
            client.commit(message=message, asynchronous=False)
            handled += 1
    finally:
        # Leaves the group cleanly so the broker rebalances immediately instead
        # of waiting for the session timeout to expire.
        client.close()
        logger.info("consumer_stopped", extra={"group": group, "handled": handled})

    return handled


def deliver_inline(*, topic: str, payload: dict[str, Any]) -> int:
    """
    Deliver an event to every in-process subscriber. **Tests only.**

    A stand-in for the broker, and the direct descendant of
    `CELERY_TASK_ALWAYS_EAGER`: it lets a test drive an event from the producing
    service all the way through its handler without a Kafka cluster, so the 60+
    `django_capture_on_commit_callbacks(execute=True)` call sites keep working
    unchanged.

    Deliberately mirrors real delivery semantics rather than being convenient:

    - **every** subscribing app runs, not just the first, because that is what
      separate consumer groups do;
    - a topic nobody subscribes to is a **no-op**, not an error — under Kafka a
      produce to an unconsumed topic succeeds;
    - handler exceptions **propagate**, so a test asserting a failure path sees
      the real error instead of a dead-lettered shrug.

    Returns the number of handlers invoked.
    """
    import importlib

    from django.apps import apps as django_apps

    invoked = 0
    for config in django_apps.get_app_configs():
        if not config.name.startswith("apps."):
            continue
        try:
            module = importlib.import_module(f"{config.name}.consumers")
        except ModuleNotFoundError:
            continue
        handler = getattr(module, "SUBSCRIPTIONS", {}).get(topic)
        if handler is not None:
            handler(**payload)
            invoked += 1
    return invoked
