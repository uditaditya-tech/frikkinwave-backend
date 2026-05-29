"""
Service layer for the musicians app.

All business logic lives here. Views call services; services call models.
apps.users.models.User is imported only under TYPE_CHECKING — no runtime coupling.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from apps.users.models import User

from apps.musicians.models import Genre, MusicianInstrument, MusicianProfile

logger = logging.getLogger(__name__)


class ProfileAlreadyExistsError(Exception):
    """Raised when a user tries to create a second MusicianProfile."""


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
    scalar_fields = ("bio", "city", "country", "is_available")
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
