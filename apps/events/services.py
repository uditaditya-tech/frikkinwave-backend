"""
Outbox publish + relay.

`publish()` is called by domain services **inside their transaction**.
`relay_pending()` is called by the relay task / management command afterwards.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from django.db import transaction
from django.utils import timezone

from apps.events.models import OutboxEvent
from apps.events.registry import EVENT_HANDLERS

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

    The relay nudge *is* fired on_commit: it is a pure latency optimisation and
    is allowed to be lost, because the periodic sweep re-picks anything missed.
    """
    # An explicit id lets a producer put the event id *inside* the payload (so a
    # consumer can use it as an idempotency key) without a second write.
    event = OutboxEvent.objects.create(
        **({"id": event_id} if event_id else {}), topic=topic, payload=payload
    )

    from apps.events.tasks import relay_outbox

    transaction.on_commit(lambda: relay_outbox.delay())

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

            task_name = EVENT_HANDLERS.get(event.topic)
            if task_name is None:
                event.attempts += 1
                event.last_error = f"No consumer registered for topic '{event.topic}'"
                event.save(update_fields=["attempts", "last_error"])
                logger.warning(
                    "event_no_consumer",
                    extra={"event_id": str(event.id), "topic": event.topic},
                )
                continue

            try:
                _dispatch(task_name=task_name, payload=event.payload)
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


def _dispatch(*, task_name: str, payload: dict[str, Any]) -> None:
    """
    Hand an event to its consumer, resolving the task **by name**.

    Deliberately not `send_task`: that pushes straight to the broker and bypasses
    `task_always_eager`, which would make every eager context (the test suite, the
    demo seeder) silently skip consumers. Resolving through the registry and
    calling `apply_async` keeps eager mode working while still avoiding any
    cross-app import — the registry stays the only coupling.
    """
    from config.celery import app as celery_app

    task = celery_app.tasks.get(task_name)
    if task is None:
        # Registry not populated yet (e.g. a web process that has imported no
        # tasks module). Autodiscovery does this at worker boot.
        celery_app.loader.import_default_modules()
        task = celery_app.tasks.get(task_name)
    if task is None:
        raise LookupError(f"Task '{task_name}' is not registered")

    task.apply_async(kwargs=payload)
