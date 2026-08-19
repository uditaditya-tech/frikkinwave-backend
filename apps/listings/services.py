"""
Service layer for the listings app.

All business logic lives here. Views call services; services call models.
apps.users.models.User is referenced only under TYPE_CHECKING (no runtime
cross-app model import).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from django.db import IntegrityError
from django.db.models import Q

from apps.listings.models import Listing, ListingApplication

if TYPE_CHECKING:
    from django.db.models import QuerySet

    from apps.users.models import User

logger = logging.getLogger(__name__)


class ListingNotFoundError(Exception):
    """No listing exists for the given id, or it is not visible to the caller."""


class NotListingAuthorError(Exception):
    """A non-author tried to mutate a listing."""


class SelfApplicationError(Exception):
    """An author tried to apply to their own listing."""


class DuplicateApplicationError(Exception):
    """This applicant already applied to this listing."""


class NotPendingError(Exception):
    """An accept/decline was attempted on an application that is not pending."""


def create_listing(*, author: User, **fields: Any) -> Listing:
    """Create a listing owned by `author`."""
    listing = Listing.objects.create(author=author, **fields)
    logger.info(
        "listing_created",
        extra={"listing_id": str(listing.id), "author_id": str(author.pk)},
    )

    # Record a feed activity (cross-app service call, never a model import).
    from apps.social.services import Verb, record_activity

    record_activity(
        actor=author,
        verb=Verb.POSTED_LISTING,
        summary=listing.title,
        target_type="listing",
        target_id=str(listing.id),
    )
    return listing


def get_listing(*, listing_id: str) -> Listing | None:
    """Return a single active listing by id, or None."""
    return Listing.objects.select_related("author").filter(id=listing_id, is_active=True).first()


def list_listings(
    *,
    listing_type: str | None = None,
    city: str | None = None,
    country: str | None = None,
) -> QuerySet[Listing]:
    """Return active listings, optionally filtered by type / city / country."""
    queryset = Listing.objects.select_related("author").filter(is_active=True)
    if listing_type:
        queryset = queryset.filter(listing_type=listing_type)
    if city:
        queryset = queryset.filter(city__iexact=city)
    if country:
        queryset = queryset.filter(country__iexact=country)
    return queryset


def update_listing(*, author: User, listing_id: str, **fields: Any) -> Listing:
    """
    Partially update a listing. Only the author may update it.

    Raises:
        ListingNotFoundError — no active listing with that id.
        NotListingAuthorError — the caller does not own the listing.
    """
    listing = _get_owned_listing(author=author, listing_id=listing_id)
    for name, value in fields.items():
        setattr(listing, name, value)
    listing.save(update_fields=[*fields.keys(), "updated_at"])
    logger.info("listing_updated", extra={"listing_id": str(listing.id)})
    return listing


def deactivate_listing(*, author: User, listing_id: str) -> None:
    """
    Soft-delete a listing (is_active=False). Only the author may do so.

    Raises ListingNotFoundError / NotListingAuthorError (see update_listing).
    """
    listing = _get_owned_listing(author=author, listing_id=listing_id)
    listing.is_active = False
    listing.save(update_fields=["is_active", "updated_at"])
    logger.info("listing_deactivated", extra={"listing_id": str(listing.id)})


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _get_owned_listing(*, author: User, listing_id: str) -> Listing:
    listing = Listing.objects.filter(id=listing_id, is_active=True).first()
    if listing is None:
        raise ListingNotFoundError
    if listing.author_id != author.pk:
        raise NotListingAuthorError
    return listing


# ---------------------------------------------------------------------------
# Applications
# ---------------------------------------------------------------------------


def apply_to_listing(
    *,
    applicant: User,
    listing_id: str,
    message: str = "",
) -> ListingApplication:
    """
    Create a pending application from `applicant` to the listing.

    Raises:
        ListingNotFoundError — no active listing with that id.
        SelfApplicationError — the applicant owns the listing.
        DuplicateApplicationError — this applicant already applied.
    """
    listing = Listing.objects.filter(id=listing_id, is_active=True).first()
    if listing is None:
        raise ListingNotFoundError

    if listing.author_id == applicant.pk:
        raise SelfApplicationError

    try:
        application = ListingApplication.objects.create(
            listing=listing,
            applicant=applicant,
            message=message,
        )
    except IntegrityError as exc:
        raise DuplicateApplicationError from exc

    # Emit the "new application" event once the row commits, so a rolled-back
    # transaction never enqueues a task pointing at a phantom row. Local import
    # avoids a tasks ↔ services import cycle.
    from apps.events.services import publish

    # Self-contained payload: the consumer is a separate service and must not
    # need a database read to send this.
    publish(
        topic="listing.application_created",
        payload={
            "recipient_email": listing.author.email,
            "applicant_username": applicant.username,
            "listing_title": listing.title,
            "message": application.message,
        },
    )

    logger.info(
        "listing_application_created",
        extra={"application_id": str(application.id), "listing_id": str(listing.id)},
    )
    return application


def list_applications(*, user: User, box: str) -> QuerySet[ListingApplication]:
    """
    Return the user's applications for the given box.

    box="incoming" → applications to listings the user authored.
    box="outgoing" → applications the user submitted.
    """
    queryset = ListingApplication.objects.select_related("applicant", "listing", "listing__author")
    if box == "outgoing":
        queryset = queryset.filter(applicant=user)
    else:
        queryset = queryset.filter(listing__author=user)
    return queryset


def get_application(*, user: User, application_id: str) -> ListingApplication | None:
    """
    Return a single application if `user` is a party to it (applicant or the
    listing's author), else None — so its existence is never leaked to outsiders.
    """
    return (
        ListingApplication.objects.select_related("applicant", "listing", "listing__author")
        .filter(id=application_id)
        .filter(Q(applicant=user) | Q(listing__author=user))
        .first()
    )


def accept_application(*, user: User, application_id: str) -> ListingApplication | None:
    """Accept a pending application. Only the listing author may accept."""
    return _resolve_application(
        user=user, application_id=application_id, new_status=ListingApplication.Status.ACCEPTED
    )


def decline_application(*, user: User, application_id: str) -> ListingApplication | None:
    """Decline a pending application. Only the listing author may decline."""
    return _resolve_application(
        user=user, application_id=application_id, new_status=ListingApplication.Status.DECLINED
    )


def _resolve_application(
    *, user: User, application_id: str, new_status: str
) -> ListingApplication | None:
    application = (
        ListingApplication.objects.select_related("applicant", "listing", "listing__author")
        .filter(id=application_id, listing__author=user)
        .first()
    )
    if application is None:
        return None
    if application.status != ListingApplication.Status.PENDING:
        raise NotPendingError
    application.status = new_status
    application.save(update_fields=["status", "updated_at"])

    # Notify the applicant only when accepted (decline is silent).
    if new_status == ListingApplication.Status.ACCEPTED:
        from apps.events.services import publish

        publish(
            topic="listing.application_accepted",
            payload={
                "recipient_email": application.applicant.email,
                "author_username": application.listing.author.username,
                "author_email": application.listing.author.email,
                "listing_title": application.listing.title,
            },
        )

    logger.info(
        "listing_application_resolved",
        extra={"application_id": str(application.id), "status": new_status},
    )
    return application
