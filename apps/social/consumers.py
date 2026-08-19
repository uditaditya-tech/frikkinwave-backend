"""
Kafka subscriptions for the social app (KAFKA.md stage 4).

See apps/search/consumers.py for why these live per-app rather than in a central
table.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from apps.social import services


def fan_out_activity(**payload: Any) -> None:
    """
    Create the canonical Activity and fan it out to followers' inboxes.

    Idempotent on `event_id`, which is the Activity's primary key — redelivery
    cannot duplicate a post in every follower's feed. That mattered under Celery
    and matters identically here: Kafka is at-least-once too.
    """
    services.fan_out_activity(
        actor_id=payload["actor_id"],
        verb=payload["verb"],
        summary=payload["summary"],
        target_type=payload["target_type"],
        target_id=payload.get("target_id"),
        target_slug=payload["target_slug"],
        event_id=payload["event_id"],
    )


def backfill_feed(**payload: Any) -> None:
    """Copy a newly-followed user's recent activities into the follower's inbox."""
    services.backfill_feed(follower_id=payload["follower_id"], followed_id=payload["followed_id"])


def prune_feed(**payload: Any) -> None:
    """Remove an unfollowed user's activities from the follower's inbox."""
    services.prune_feed(follower_id=payload["follower_id"], followed_id=payload["followed_id"])


SUBSCRIPTIONS: dict[str, Callable[..., None]] = {
    "activity.recorded": fan_out_activity,
    "follow.created": backfill_feed,
    "follow.removed": prune_feed,
}
