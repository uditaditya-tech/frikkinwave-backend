"""
Kafka subscriptions for the notifications service (KAFKA.md stage 4).

Like the Celery tasks it replaces, every handler forwards its payload straight to
`services.deliver()` keyed by topic — the payloads are self-contained by design,
so this service imports no other app and no models. That boundary is enforced by
tests/test_architecture.py and erodes silently if it is not.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from apps.notifications import services

#: Every topic this service delivers mail for.
TOPICS = (
    "contact_request.created",
    "contact_request.accepted",
    "engagement.requested",
    "engagement.accepted",
    "listing.application_created",
    "listing.application_accepted",
    "band.invite_created",
    "band.invite_accepted",
)


def _handler(topic: str) -> Callable[..., None]:
    def handler(**payload: Any) -> None:
        services.deliver(kind=topic, **payload)

    handler.__name__ = topic.replace(".", "_")
    return handler


SUBSCRIPTIONS: dict[str, Callable[..., None]] = {t: _handler(t) for t in TOPICS}
