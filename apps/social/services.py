"""
Service layer for the social app — the follow graph.

All business logic lives here. Views call services; services call models.
Cross-app access (username → user) goes through apps.users.services — never a
model import. apps.users.models.User is referenced only under TYPE_CHECKING.
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING

import uuid6
from django.db import IntegrityError, transaction

from apps.social.models import Activity, FeedEntry, Follow
from apps.users.services import UserRef, get_user_ref

if TYPE_CHECKING:
    from django.db.models import QuerySet

    from apps.users.models import User

logger = logging.getLogger(__name__)

# Public verb vocabulary for producing apps. They reference activity verbs through
# the service layer (this alias), never by importing the Activity model — keeping
# the no-cross-app-model-import rule intact.
Verb = Activity.Verb


class UserNotFoundError(Exception):
    """No user exists for the given username."""


class SelfFollowError(Exception):
    """A user tried to follow themselves."""


def follow_user(*, follower: User, username: str) -> tuple[Follow, bool]:
    """
    Make `follower` follow the user identified by `username`.

    Idempotent: if the edge already exists it is returned rather than raising —
    re-following is a no-op success. Returns (edge, created) so the caller can
    distinguish a fresh follow (201) from a repeat (200).

    Raises:
        UserNotFoundError — no such user.
        SelfFollowError — follower tried to follow themselves.
    """
    target = get_user_ref(username=username)
    if target is None:
        raise UserNotFoundError
    if target.id == follower.pk:
        raise SelfFollowError

    try:
        # Savepoint so a unique-constraint hit rolls back only this INSERT,
        # leaving the surrounding transaction usable for the follow-up read.
        # FK by id — `target` is a DTO, not an ORM instance.
        with transaction.atomic():
            follow = Follow.objects.create(follower=follower, followed_id=target.id)
    except IntegrityError:
        # Unique edge already present — return it, keeping follow idempotent.
        return Follow.objects.get(follower=follower, followed_id=target.id), False

    # Backfill the new followee's recent activity into the follower's inbox so
    # the feed isn't empty until the followee next acts. Emitted post-commit so a
    # rolled-back follow never enqueues a fan-out. Local import avoids a cycle.
    from apps.events.services import publish

    follower_id, followed_id = str(follower.pk), str(target.id)
    publish(
        topic="follow.created",
        payload={"follower_id": follower_id, "followed_id": followed_id},
    )

    logger.info("follow_created", extra={"follower_id": follower_id, "followed_id": followed_id})
    return follow, True


def unfollow_user(*, follower: User, username: str) -> bool:
    """
    Remove `follower`'s follow edge to `username`.

    Returns True if an edge was deleted, False if there was nothing to unfollow.

    Raises UserNotFoundError if no such user exists.
    """
    target = get_user_ref(username=username)
    if target is None:
        raise UserNotFoundError

    deleted, _ = Follow.objects.filter(follower=follower, followed_id=target.id).delete()
    if deleted:
        # Prune the unfollowed user's entries from the inbox, keeping the feed
        # consistent with the follow graph. Emitted post-commit.
        from apps.events.services import publish

        follower_id, followed_id = str(follower.pk), str(target.id)
        publish(
            topic="follow.removed",
            payload={"follower_id": follower_id, "followed_id": followed_id},
        )
        logger.info(
            "follow_removed", extra={"follower_id": follower_id, "followed_id": followed_id}
        )
    return bool(deleted)


def list_following(*, user_id: str) -> QuerySet[Follow]:
    """Follow edges where the user is the follower (people they follow). Keyed by id."""
    return Follow.objects.select_related("followed").filter(follower_id=uuid.UUID(user_id))


def list_followers(*, user_id: str) -> QuerySet[Follow]:
    """Follow edges where the user is followed (their followers). Keyed by id."""
    return Follow.objects.select_related("follower").filter(followed_id=uuid.UUID(user_id))


def get_user_or_raise(*, username: str) -> UserRef:
    """Resolve a username to a `UserRef` for the public follower/following endpoints."""
    target = get_user_ref(username=username)
    if target is None:
        raise UserNotFoundError
    return target


# ---------------------------------------------------------------------------
# Activity feed (fan-out-on-write)
# ---------------------------------------------------------------------------
#
# Public entry point for *other apps*: a producer (listings, bands, …) calls
# `record_activity(...)` from its service layer after creating something. We
# don't write the Activity inline — we emit a post-commit Celery event so the
# fan-out (canonical Activity + a FeedEntry per follower) happens off the
# request, and a rolled-back action never produces a phantom activity.
#
# The `social` app stays ignorant of every producer's schema: the producer
# supplies a denormalized `summary` plus opaque `target_*` fields.


def record_activity(
    *,
    actor: User,
    verb: str,
    summary: str,
    target_type: str,
    target_id: str | None = None,
    target_slug: str = "",
) -> None:
    """
    Record that `actor` did something, fanning it out to their followers' feeds.

    Called by producing apps' services. Schedules the fan-out post-commit; does
    no DB write of its own, so it is safe to call inside the producer's action.
    """
    from apps.events.services import publish

    # Mint the event id up front so it can travel *inside* the payload: the
    # consumer uses it as the Activity's primary key, which is what makes the
    # fan-out idempotent under at-least-once delivery (see fan_out_activity).
    event_id = uuid6.uuid7()
    publish(
        topic="activity.recorded",
        event_id=event_id,
        payload={
            "actor_id": str(actor.pk),
            "verb": verb,
            "summary": summary,
            "target_type": target_type,
            "target_id": target_id,
            "target_slug": target_slug,
            "event_id": str(event_id),
        },
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
    """
    Create the canonical Activity and place a copy in every follower's inbox
    (plus the actor's own, so they see their own posts).

    **Fully idempotent**, which at-least-once delivery requires: the outbox
    `event_id` is used as the Activity's primary key, so a redelivered event
    finds the row already present instead of duplicating the post in every
    follower's feed. Inbox rows dedupe via `ignore_conflicts`.
    """
    actor_uuid = uuid.UUID(actor_id)
    activity, created = Activity.objects.get_or_create(
        id=uuid.UUID(event_id),
        defaults={
            "actor_id": actor_uuid,
            "verb": verb,
            "summary": summary,
            "target_type": target_type,
            "target_id": uuid.UUID(target_id) if target_id else None,
            "target_slug": target_slug,
        },
    )
    if not created:
        logger.info("activity_already_fanned_out", extra={"activity_id": str(activity.id)})
        return
    recipient_ids = set(
        Follow.objects.filter(followed_id=actor_uuid).values_list("follower_id", flat=True)
    )
    recipient_ids.add(activity.actor_id)
    FeedEntry.objects.bulk_create(
        [
            FeedEntry(owner_id=rid, activity=activity, created_at=activity.created_at)
            for rid in recipient_ids
        ],
        ignore_conflicts=True,
    )
    logger.info(
        "activity_fanned_out",
        extra={"activity_id": str(activity.id), "recipients": len(recipient_ids)},
    )


def backfill_feed(*, follower_id: str, followed_id: str, limit: int = 50) -> None:
    """
    Copy the followed user's most recent activities into the follower's inbox so
    a fresh follow yields a populated feed. Invoked by the `backfill_feed` task.
    """
    follower_uuid = uuid.UUID(follower_id)
    activities = list(
        Activity.objects.filter(actor_id=uuid.UUID(followed_id)).order_by("-created_at")[:limit]
    )
    FeedEntry.objects.bulk_create(
        [
            FeedEntry(owner_id=follower_uuid, activity=activity, created_at=activity.created_at)
            for activity in activities
        ],
        ignore_conflicts=True,
    )
    logger.info(
        "feed_backfilled",
        extra={"follower_id": follower_id, "followed_id": followed_id, "count": len(activities)},
    )


def prune_feed(*, follower_id: str, followed_id: str) -> None:
    """Remove the unfollowed user's activities from the follower's inbox."""
    deleted, _ = FeedEntry.objects.filter(
        owner_id=uuid.UUID(follower_id), activity__actor_id=uuid.UUID(followed_id)
    ).delete()
    logger.info(
        "feed_pruned",
        extra={"follower_id": follower_id, "followed_id": followed_id, "deleted": deleted},
    )


def get_feed(*, user: User) -> QuerySet[FeedEntry]:
    """Return the caller's feed inbox, newest first, with the activity + actor joined."""
    return FeedEntry.objects.select_related("activity", "activity__actor").filter(owner=user)
