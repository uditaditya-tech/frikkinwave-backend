"""
Service layer for the reviews app.

All business logic lives here. Views call services; services call models.
Cross-app access goes through other apps' *services*, never their models:
  - username → user via apps.users.services
  - the review gate (a completed engagement) via apps.engagements.services
Concrete model types from other apps appear only under TYPE_CHECKING.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from django.db import IntegrityError
from django.db.models import Avg, Count

from apps.engagements.services import parties_of_completed_engagement
from apps.reviews.models import Review
from apps.users.services import get_user_by_username

if TYPE_CHECKING:
    from django.db.models import QuerySet

    from apps.users.models import User

logger = logging.getLogger(__name__)


class SubjectNotFoundError(Exception):
    """No user exists for the given subject username."""


class NotEligibleError(Exception):
    """The author may not review the subject — no completed engagement ties them."""


class DuplicateReviewError(Exception):
    """The author already reviewed the subject for this interaction."""


def create_review(
    *,
    author: User,
    subject_username: str,
    engagement_id: str,
    rating: int,
    comment: str = "",
) -> Review:
    """
    Create a review of `subject_username` by `author`, gated on a completed
    engagement between the two.

    Raises:
        SubjectNotFoundError — no such subject user.
        NotEligibleError — no completed engagement links author + subject, or the
            author tried to review themselves.
        DuplicateReviewError — author already reviewed this engagement.
    """
    subject = get_user_by_username(username=subject_username)
    if subject is None:
        raise SubjectNotFoundError
    if subject.pk == author.pk:
        raise NotEligibleError

    parties = parties_of_completed_engagement(engagement_id=engagement_id)
    if parties is None or {author.pk, subject.pk} != parties:
        raise NotEligibleError

    try:
        review = Review.objects.create(
            author=author,
            subject=subject,
            rating=rating,
            comment=comment,
            context_type=Review.Context.ENGAGEMENT,
            context_id=engagement_id,
        )
    except IntegrityError as exc:
        raise DuplicateReviewError from exc

    logger.info(
        "review_created",
        extra={
            "review_id": str(review.id),
            "author_id": str(author.pk),
            "subject_id": str(subject.pk),
            "rating": rating,
        },
    )
    return review


def list_reviews_for(*, subject: User) -> QuerySet[Review]:
    """Return the reviews a user has received, newest first."""
    return Review.objects.select_related("author").filter(subject=subject)


def rating_summary(*, subject: User) -> dict[str, object]:
    """Return {'average_rating': float | None, 'count': int} for a user."""
    agg = Review.objects.filter(subject=subject).aggregate(avg=Avg("rating"), count=Count("id"))
    average = round(agg["avg"], 2) if agg["avg"] is not None else None
    return {"average_rating": average, "count": agg["count"]}


def get_user_or_raise(*, username: str) -> User:
    """Resolve a username to a user for the public review endpoints."""
    subject = get_user_by_username(username=username)
    if subject is None:
        raise SubjectNotFoundError
    return subject
