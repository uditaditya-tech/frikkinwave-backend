"""
Service layer for the venues app.

All business logic lives here. Views call services; services call models.
apps.users.models.User is referenced only under TYPE_CHECKING (no runtime
cross-app model import).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from django.utils.text import slugify

from apps.venues.models import Venue

if TYPE_CHECKING:
    from django.db.models import QuerySet

    from apps.users.models import User

logger = logging.getLogger(__name__)


class VenueNotFoundError(Exception):
    """No venue exists for the given slug, or it is not visible to the caller."""


class NotVenueOwnerError(Exception):
    """A non-owner tried to mutate a venue."""


def create_venue(*, owner: User, name: str, **fields: Any) -> Venue:
    """Create a venue owned by `owner`, deriving a unique slug from the name."""
    venue = Venue.objects.create(owner=owner, name=name, slug=_unique_slug(name), **fields)
    logger.info("venue_created", extra={"venue_id": str(venue.id), "owner_id": str(owner.pk)})
    return venue


def get_venue(*, slug: str) -> Venue | None:
    """Return a single active venue by slug, or None."""
    return Venue.objects.select_related("owner").filter(slug=slug, is_active=True).first()


def list_venues(
    *,
    city: str | None = None,
    country: str | None = None,
) -> QuerySet[Venue]:
    """Return active venues, optionally filtered by city / country."""
    queryset = Venue.objects.select_related("owner").filter(is_active=True)
    if city:
        queryset = queryset.filter(city__iexact=city)
    if country:
        queryset = queryset.filter(country__iexact=country)
    return queryset


def update_venue(*, owner: User, slug: str, **fields: Any) -> Venue:
    """
    Partially update a venue. Only the owner may update it.

    Raises VenueNotFoundError / NotVenueOwnerError.
    """
    venue = _get_owned_venue(owner=owner, slug=slug)
    for name, value in fields.items():
        setattr(venue, name, value)
    venue.save(update_fields=[*fields.keys(), "updated_at"])
    logger.info("venue_updated", extra={"venue_id": str(venue.id)})
    return venue


def deactivate_venue(*, owner: User, slug: str) -> None:
    """Soft-delete a venue (is_active=False). Only the owner may do so."""
    venue = _get_owned_venue(owner=owner, slug=slug)
    venue.is_active = False
    venue.save(update_fields=["is_active", "updated_at"])
    logger.info("venue_deactivated", extra={"venue_id": str(venue.id)})


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _get_owned_venue(*, owner: User, slug: str) -> Venue:
    venue = Venue.objects.filter(slug=slug, is_active=True).first()
    if venue is None:
        raise VenueNotFoundError
    if venue.owner_id != owner.pk:
        raise NotVenueOwnerError
    return venue


def _unique_slug(name: str) -> str:
    """Slugify `name` and disambiguate with a numeric suffix if already taken."""
    base = slugify(name) or "venue"
    base = base[:120]
    candidate = base
    suffix = 2
    while Venue.objects.filter(slug=candidate).exists():
        tail = f"-{suffix}"
        candidate = f"{base[: 120 - len(tail)]}{tail}"
        suffix += 1
    return candidate
