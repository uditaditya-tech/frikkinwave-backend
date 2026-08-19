"""
Celery tasks for the social app — activity-feed fan-out (Phase 5, Block B).

Event handlers, not inline calls. Producing apps publish `activity.recorded` to
the transactional outbox — ``publish()`` inside the caller's transaction, so the
event and the row it describes commit together (scale rule #4 in CLAUDE.md).
Follow/unfollow publish `follow.created` / `follow.removed` the same way.

The topic + payload is the message contract that becomes a Kafka schema when
social is extracted.

Tasks stay thin: they delegate to the service layer, which owns the fan-out
logic. Delivery is at-least-once, so consumers must be idempotent —
`fan_out_activity` keys the Activity on the event id for exactly that reason.
"""

from __future__ import annotations

from celery import shared_task

from apps.social import services


@shared_task(
    name="social.fan_out_activity",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=3,
)
def fan_out_activity(
    *,
    actor_id: str,
    verb: str,
    summary: str,
    target_type: str,
    target_id: str | None,
    target_slug: str,
    event_id: str,
) -> None:
    """Create the canonical Activity and fan it out to followers' inboxes."""
    services.fan_out_activity(
        actor_id=actor_id,
        verb=verb,
        summary=summary,
        target_type=target_type,
        target_id=target_id,
        target_slug=target_slug,
        event_id=event_id,
    )


@shared_task(
    name="social.backfill_feed",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=3,
)
def backfill_feed(*, follower_id: str, followed_id: str) -> None:
    """Copy a newly-followed user's recent activities into the follower's inbox."""
    services.backfill_feed(follower_id=follower_id, followed_id=followed_id)


@shared_task(
    name="social.prune_feed",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=3,
)
def prune_feed(*, follower_id: str, followed_id: str) -> None:
    """Remove an unfollowed user's activities from the follower's inbox."""
    services.prune_feed(follower_id=follower_id, followed_id=followed_id)
