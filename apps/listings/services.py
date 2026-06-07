"""
Service layer for the listings app.

All business logic lives here. Views call services; services call models.
apps.users.models.User is referenced only under TYPE_CHECKING (no runtime
cross-app model import).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from apps.listings.models import Listing

if TYPE_CHECKING:
    from django.db.models import QuerySet

    from apps.users.models import User

logger = logging.getLogger(__name__)


class ListingNotFoundError(Exception):
    """No listing exists for the given id, or it is not visible to the caller."""


class NotListingAuthorError(Exception):
    """A non-author tried to mutate a listing."""


def create_listing(*, author: User, **fields: Any) -> Listing:
    """Create a listing owned by `author`."""
    listing = Listing.objects.create(author=author, **fields)
    logger.info(
        "listing_created",
        extra={"listing_id": str(listing.id), "author_id": str(author.pk)},
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
