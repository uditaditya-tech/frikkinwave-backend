"""
Transactional outbox (see MICROSERVICES.md §5).

Data-shape only. This is a *platform* app, not a domain app: it owns no business
concepts, only the durable record of "something happened" that decouples a state
change from the reaction to it.
"""

import uuid

import uuid6
from django.db import models
from django.utils import timezone


def _new_uuid() -> uuid.UUID:
    return uuid6.uuid7()


class OutboxEvent(models.Model):
    """
    One domain event, written in the same transaction as the state change it
    describes.

    That co-commit is the entire point: either the row and the event both exist,
    or neither does. A relay publishes unsent rows afterwards, so a process that
    dies between COMMIT and publish loses nothing — the event is still here.
    """

    id = models.UUIDField(primary_key=True, default=_new_uuid, editable=False)
    topic = models.CharField(max_length=100)
    payload = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    # Delivery bookkeeping.
    published_at = models.DateTimeField(null=True, blank=True)
    attempts = models.PositiveIntegerField(default=0)
    last_error = models.TextField(blank=True)

    #: Earliest time the relay may try this event again.
    #:
    #: Without it, retries ran at the poll interval — one per second, no
    #: spacing — so MAX_ATTEMPTS was spent in about ten seconds and any broker
    #: outage longer than that stranded every pending event permanently. Proved
    #: by drill, not reasoned about: five events denied at the broker were all
    #: exhausted within 45 seconds while the relay sat there healthy.
    next_attempt_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["created_at"]
        indexes = [
            # The relay's only query: unpublished, due now, oldest first.
            # next_attempt_at leads because it is the filter; created_at follows
            # because it is the sort.
            models.Index(
                fields=["next_attempt_at", "created_at"],
                condition=models.Q(published_at__isnull=True),
                name="outbox_pending_idx",
            ),
        ]

    def __str__(self) -> str:
        state = "published" if self.published_at else f"pending({self.attempts})"
        return f"{self.topic} [{state}]"
