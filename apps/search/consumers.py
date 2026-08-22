"""
Kafka subscriptions for the search service (KAFKA.md stage 4).

This is the inversion stage 4 exists for. Under Celery the producer's relay read
a central `EVENT_HANDLERS` table to decide who ran; here **search declares what
search listens to**, and no producer, and no shared file, knows about it.

Adding a second consumer of `profile.updated` is now a matter of another service
declaring its own subscription under its own group id — no change to the producer
and nothing to coordinate.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from apps.search import services


def index_profile(**payload: Any) -> None:
    """
    Index a profile from the facts the producer published.

    Every key is read strictly rather than with a default. A payload missing one
    is a *bug* — a producer that changed shape, or an event written before this
    handler did — and the useful outcome there is a raised KeyError, which the
    consumer turns into a bounded retry and then a dead letter. Defaulting the
    missing fields instead would index a blank document, quietly making that
    profile unfindable while reporting success.
    """
    services.index_profile(
        profile_id=payload["profile_id"],
        bio=payload["bio"],
        instruments=payload["instruments"],
        genres=payload["genres"],
        city=payload["city"],
        country=payload["country"],
        is_available=payload["is_available"],
    )


#: topic -> handler. The consumer runtime subscribes to exactly these keys.
SUBSCRIPTIONS: dict[str, Callable[..., None]] = {
    "profile.updated": index_profile,
}
