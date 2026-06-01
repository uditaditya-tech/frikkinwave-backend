"""
Service layer for the musicians app.

All business logic lives here. Views call services; services call models.
apps.users.models.User is imported only under TYPE_CHECKING — no runtime coupling.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from django.db.models import QuerySet

    from apps.users.models import User

from apps.musicians.models import Genre, Instrument, MusicianInstrument, MusicianProfile

logger = logging.getLogger(__name__)


class ProfileAlreadyExistsError(Exception):
    """Raised when a user tries to create a second MusicianProfile."""


def list_instruments() -> QuerySet[Instrument]:
    """Return all instruments, name-ordered (Instrument.Meta.ordering)."""
    return Instrument.objects.all()


def list_genres() -> QuerySet[Genre]:
    """Return all genres, name-ordered (Genre.Meta.ordering)."""
    return Genre.objects.all()


def create_profile(*, user: User, data: dict[str, Any]) -> MusicianProfile:
    """
    Create a MusicianProfile for the given user.

    `data` is the validated output of MusicianProfileWriteSerializer.
    Raises ProfileAlreadyExistsError if the user already has a profile.
    """
    if MusicianProfile.objects.filter(user=user).exists():
        raise ProfileAlreadyExistsError

    profile = MusicianProfile.objects.create(
        user=user,
        bio=data.get("bio", ""),
        city=data.get("city", ""),
        country=data.get("country", ""),
        is_available=data.get("is_available", True),
        sound_url=data.get("sound_url", ""),
    )

    _set_instruments(profile, data.get("instruments", []))
    _set_genres(profile, data.get("genres", []))

    logger.info("profile_created", extra={"profile_id": str(profile.id), "user_id": str(user.pk)})
    return profile


def update_profile(*, profile: MusicianProfile, data: dict[str, Any]) -> MusicianProfile:
    """
    Partially update a MusicianProfile.

    Only keys present in `data` are updated — absent keys are left untouched.
    `data` is the validated output of MusicianProfileWriteSerializer (partial=True).
    """
    scalar_fields = ("bio", "city", "country", "is_available", "sound_url")
    changed = False
    for field in scalar_fields:
        if field in data:
            setattr(profile, field, data[field])
            changed = True
    if changed:
        profile.save()

    if "instruments" in data:
        _set_instruments(profile, data["instruments"])

    if "genres" in data:
        _set_genres(profile, data["genres"])

    logger.info("profile_updated", extra={"profile_id": str(profile.id)})
    return profile


def list_profiles(*, filters: dict[str, Any]) -> QuerySet[MusicianProfile]:
    """
    Return the public discovery queryset, narrowed by the provided filters.

    All filter keys are optional and combinable. Only keys present in `filters`
    are applied. Recognised keys:
      - city      → case-insensitive exact match
      - country   → case-insensitive exact match
      - instrument→ instrument slug
      - genre     → genre slug
      - available → True restricts to is_available=True; any other value is ignored

    Ordering is left to the caller's paginator (CursorPagination orders by
    -created_at). The queryset prefetches related rows to keep the nested
    serializer free of N+1 queries.
    """
    queryset = MusicianProfile.objects.select_related("user").prefetch_related(
        "musician_instruments__instrument",
        "genres",
    )

    if city := filters.get("city"):
        queryset = queryset.filter(city__iexact=city)
    if country := filters.get("country"):
        queryset = queryset.filter(country__iexact=country)
    if instrument := filters.get("instrument"):
        queryset = queryset.filter(instruments__slug=instrument)
    if genre := filters.get("genre"):
        queryset = queryset.filter(genres__slug=genre)
    if filters.get("available") is True:
        queryset = queryset.filter(is_available=True)

    # M2M filters can duplicate rows across joins.
    queryset = queryset.distinct()

    logger.info("profiles_listed", extra={"filter_keys": sorted(filters.keys())})
    return queryset


def get_public_profile(*, username: str) -> MusicianProfile | None:
    """
    Return a single public profile by its owner's username, or None if absent.

    Username match is case-insensitive. Related rows are prefetched so the
    nested serializer stays free of N+1 queries.
    """
    profile = (
        MusicianProfile.objects.select_related("user")
        .prefetch_related(
            "musician_instruments__instrument",
            "genres",
        )
        .filter(user__username__iexact=username)
        .first()
    )
    logger.info("public_profile_viewed", extra={"username": username, "found": profile is not None})
    return profile


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _set_instruments(
    profile: MusicianProfile,
    instruments: list[dict[str, Any]],
) -> None:
    """Replace all instruments on a profile."""
    profile.musician_instruments.all().delete()
    MusicianInstrument.objects.bulk_create(
        [
            MusicianInstrument(
                profile=profile,
                instrument=item["instrument"],
                proficiency=item.get("proficiency", MusicianInstrument.Proficiency.INTERMEDIATE),
            )
            for item in instruments
        ]
    )


def _set_genres(profile: MusicianProfile, genres: list[Genre]) -> None:
    """Replace all genres on a profile."""
    profile.genres.set(genres)
