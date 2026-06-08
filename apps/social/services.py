"""
Service layer for the social app — the follow graph.

All business logic lives here. Views call services; services call models.
Cross-app access (username → user) goes through apps.users.services — never a
model import. apps.users.models.User is referenced only under TYPE_CHECKING.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from django.db import IntegrityError, transaction

from apps.social.models import Follow
from apps.users.services import get_user_by_username

if TYPE_CHECKING:
    from django.db.models import QuerySet

    from apps.users.models import User

logger = logging.getLogger(__name__)


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
    target = get_user_by_username(username=username)
    if target is None:
        raise UserNotFoundError
    if target.pk == follower.pk:
        raise SelfFollowError

    try:
        # Savepoint so a unique-constraint hit rolls back only this INSERT,
        # leaving the surrounding transaction usable for the follow-up read.
        with transaction.atomic():
            follow = Follow.objects.create(follower=follower, followed=target)
    except IntegrityError:
        # Unique edge already present — return it, keeping follow idempotent.
        return Follow.objects.get(follower=follower, followed=target), False

    logger.info(
        "follow_created",
        extra={"follower_id": str(follower.pk), "followed_id": str(target.pk)},
    )
    return follow, True


def unfollow_user(*, follower: User, username: str) -> bool:
    """
    Remove `follower`'s follow edge to `username`.

    Returns True if an edge was deleted, False if there was nothing to unfollow.

    Raises UserNotFoundError if no such user exists.
    """
    target = get_user_by_username(username=username)
    if target is None:
        raise UserNotFoundError

    deleted, _ = Follow.objects.filter(follower=follower, followed=target).delete()
    if deleted:
        logger.info(
            "follow_removed",
            extra={"follower_id": str(follower.pk), "followed_id": str(target.pk)},
        )
    return bool(deleted)


def list_following(*, user: User) -> QuerySet[Follow]:
    """Return the follow edges where `user` is the follower (people they follow)."""
    return Follow.objects.select_related("followed").filter(follower=user)


def list_followers(*, user: User) -> QuerySet[Follow]:
    """Return the follow edges where `user` is followed (their followers)."""
    return Follow.objects.select_related("follower").filter(followed=user)


def get_user_or_raise(*, username: str) -> User:
    """Resolve a username to a user for the public follower/following endpoints."""
    target = get_user_by_username(username=username)
    if target is None:
        raise UserNotFoundError
    return target


def follow_counts(*, user: User) -> dict[str, int]:
    """Return {'followers': N, 'following': M} for a user."""
    return {
        "followers": Follow.objects.filter(followed=user).count(),
        "following": Follow.objects.filter(follower=user).count(),
    }
