"""
Transactional outbox (see MICROSERVICES.md §5).

Data-shape only. This is a *platform* app, not a domain app: it owns no business
concepts, only the durable record of "something happened" that decouples a state
change from the reaction to it.
"""

import uuid

import uuid6
from django.db import models


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

    class Meta:
        ordering = ["created_at"]
        indexes = [
            # The relay's only query: oldest unpublished first.
            models.Index(
                fields=["created_at"],
                condition=models.Q(published_at__isnull=True),
                name="outbox_pending_idx",
            ),
        ]

    def __str__(self) -> str:
        state = "published" if self.published_at else f"pending({self.attempts})"
        return f"{self.topic} [{state}]"
