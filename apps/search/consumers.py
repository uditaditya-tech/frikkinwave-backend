"""
Kafka subscriptions for the search service (KAFKA.md stage 4).

This is the inversion stage 4 exists for. Under Celery the producer's relay read
a central `EVENT_HANDLERS` table to decide who ran; here **search declares what
search listens to**, and no producer, and no shared file, knows about it.

Adding a second consumer of `profile.updated` is now a matter of another service
declaring its own subscription under its own group id — no change to the producer
and nothing to coordinate.

The handlers are the same `services` calls the Celery tasks make. Nothing about
the business logic moves; only the thing that invokes it.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from apps.search import services


def index_profile(**payload: Any) -> None:
    """Embed and store a profile's text (see services.index_profile)."""
    services.index_profile(
        profile_id=payload["profile_id"],
        embedding_text=payload["embedding_text"],
        is_available=payload.get("is_available", True),
    )


#: topic -> handler. The consumer runtime subscribes to exactly these keys.
SUBSCRIPTIONS: dict[str, Callable[..., None]] = {
    "profile.updated": index_profile,
}
