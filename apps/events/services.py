"""
Outbox publish + relay.

`publish()` is called by domain services **inside their transaction**.
`relay_pending()` is called by the relay task / management command afterwards.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.events.models import OutboxEvent

logger = logging.getLogger(__name__)

#: Give up on an event after this many failed dispatches. It stays in the table
#: (unpublished, with last_error) for inspection rather than retrying forever.
MAX_ATTEMPTS = 10

#: Safety valve so one sweep can't hold a transaction open indefinitely.
DEFAULT_BATCH = 100


def publish(
    *, topic: str, payload: dict[str, Any], event_id: uuid.UUID | None = None
) -> OutboxEvent:
    """
    Record a domain event. **Must be called inside the producer's transaction.**

    No `on_commit` here — that is the whole point. The event row and the state
    change commit together, so a rollback discards the event and a crash after
    COMMIT still leaves it durably recorded.

    Nothing is dispatched from here. The relay Deployment
    (`relay_outbox --loop`) picks the row up within a second; putting a
    synchronous Kafka produce in the request path would add up to
    KAFKA_FLUSH_TIMEOUT to every user-facing request during a broker outage.

    The one exception is `EVENT_RELAY_INLINE` (tests), where the on_commit hook
    relays inline so a test can drive an event end to end without a broker.
    """
    # An explicit id lets a producer put the event id *inside* the payload (so a
    # consumer can use it as an idempotency key) without a second write.
    event = OutboxEvent.objects.create(
        **({"id": event_id} if event_id else {}), topic=topic, payload=payload
    )

    if settings.EVENT_RELAY_INLINE:
        transaction.on_commit(relay_pending)

    logger.info("event_published", extra={"event_id": str(event.id), "topic": topic})
    return event


def relay_pending(*, limit: int = DEFAULT_BATCH) -> int:
    """
    Dispatch unpublished events to their consumers, oldest first.

    Returns the number published. Each event is claimed with
    `select_for_update(skip_locked=True)` so concurrent relays (the nudge and the
    sweep, or several workers) never double-dispatch the same row.

    Delivery is **at-least-once**: we mark published only after a successful
    hand-off, so a crash mid-dispatch redelivers. Consumers must be idempotent.
    """
    pending_ids = list(
        OutboxEvent.objects.filter(published_at__isnull=True, attempts__lt=MAX_ATTEMPTS)
        .order_by("created_at")
        .values_list("id", flat=True)[:limit]
    )

    published = 0
    for event_id in pending_ids:
        with transaction.atomic():
            event = (
                OutboxEvent.objects.select_for_update(skip_locked=True)
                .filter(id=event_id, published_at__isnull=True)
                .first()
            )
            if event is None:
                continue  # already claimed by another relay

            # No consumer lookup. The producer does not know who listens — that
            # is the point of stage 4, and there is no longer a registry to ask.
            # A topic nobody subscribes to is not an error; it is a topic nobody
            # has subscribed to yet.
            try:
                _dispatch(topic=event.topic, payload=event.payload)
            except Exception as exc:
                event.attempts += 1
                event.last_error = repr(exc)
                event.save(update_fields=["attempts", "last_error"])
                logger.exception("event_dispatch_failed", extra={"event_id": str(event.id)})
                continue

            event.published_at = timezone.now()
            event.attempts += 1
            event.save(update_fields=["published_at", "attempts"])
            published += 1

    if published:
        logger.info("outbox_relayed", extra={"published": published})
    return published


@dataclass(frozen=True)
class OutboxLag:
    """One reading of how far behind the outbox is."""

    #: Unpublished events, including the exhausted ones.
    pending: int
    #: Events past MAX_ATTEMPTS. They will never retry, so they do not age
    #: `oldest_age_seconds` above once everything retryable has drained — which
    #: is exactly why they are counted separately rather than folded in.
    exhausted: int
    #: Age of the oldest *retryable* unpublished event. 0.0 when there is none.
    oldest_age_seconds: float


def outbox_lag_snapshot() -> OutboxLag:
    """
    How far behind is the outbox, right now?

    **One number, many failures.** Lag rises whether the relay is down, the relay
    is wedged, Kafka is unreachable, the client certificate expired, an ACL is
    missing, or a topic was never created. It does not care which — that is the
    point.

    Lives here rather than in `check_outbox_lag` because there are now two
    readers: the command (which exits non-zero) and the relay loop (which
    exports it as a Prometheus gauge). Two copies of this query would drift, and
    an alert disagreeing with the command it replaced is worse than either alone.

    What it does NOT cover: consumer-side failures. A message that reached Kafka
    and was never consumed leaves the outbox clean — that needs consumer-group
    lag, which Strimzi's exporter provides.
    """
    pending = OutboxEvent.objects.filter(published_at__isnull=True)
    exhausted = pending.filter(attempts__gte=MAX_ATTEMPTS).count()

    oldest = pending.exclude(attempts__gte=MAX_ATTEMPTS).order_by("created_at").first()
    age = (timezone.now() - oldest.created_at).total_seconds() if oldest else 0.0

    return OutboxLag(pending=pending.count(), exhausted=exhausted, oldest_age_seconds=age)


def _dispatch(*, topic: str, payload: dict[str, Any]) -> None:
    """
    Produce the event to its Kafka topic.

    `publish()` and the outbox are unaffected by any of this — Kafka does not
    solve dual-write, so the event row still commits with the state change and
    the sweep still recovers anything stranded. Only the hand-off lives here.

    Raising propagates to `relay_pending`, which increments `attempts`, records
    `last_error`, and leaves the event unpublished for the next pass.
    """
    if settings.EVENT_RELAY_INLINE:
        # Tests only. Delivers to in-process subscribers instead of a broker —
        # see apps.events.consumer.deliver_inline. Imported from the consumer
        # RUNTIME, never from an app's consumers module, so the producer still
        # has no idea who listens.
        from apps.events.consumer import deliver_inline

        deliver_inline(topic=topic, payload=payload)
        return

    from apps.events.kafka import produce

    produce(topic=topic, payload=payload)
