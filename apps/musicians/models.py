"""
Models for the musicians app.

MusicianProfile extends the User with music-specific data.
User → MusicianProfile is a one-to-one relationship created at profile setup time.

Model order matters for forward references:
  Instrument → Genre → MusicianInstrument → MusicianProfile
MusicianInstrument references MusicianProfile as a string FK so it can be
defined before MusicianProfile, allowing MusicianProfile.instruments to use
a direct (non-string) through= reference.

Design decisions:
- city/country as free-text for Phase 1; normalise later if geo-search demands it.
- is_available signals jam-partner availability — the Phase 1 anchor feature.
- Reference AUTH_USER_MODEL via settings string to avoid cross-app model imports.
- MusicianInstrument is an explicit through model to store proficiency level.
- Slug on Instrument/Genre is set explicitly by the caller (serializer / management
  command) — no save() override needed, which avoids fighting django-stubs' typed
  Model.save() signature.
"""

import uuid

import uuid6
from django.conf import settings
from django.db import models


def _new_uuid() -> uuid.UUID:
    """
    Generate a UUIDv7 (time-ordered, index-friendly).
    Uses the uuid6 backport — upgrade to stdlib uuid.uuid7() when on Python 3.14+.
    """
    return uuid6.uuid7()


# ---------------------------------------------------------------------------
# Lookup tables
# ---------------------------------------------------------------------------


class Instrument(models.Model):
    id = models.UUIDField(primary_key=True, default=_new_uuid, editable=False)
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=100, unique=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class Genre(models.Model):
    id = models.UUIDField(primary_key=True, default=_new_uuid, editable=False)
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=100, unique=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


# ---------------------------------------------------------------------------
# Through model (defined before MusicianProfile so the M2M field can
# reference it directly instead of via a forward-reference string)
# ---------------------------------------------------------------------------


class MusicianInstrument(models.Model):
    """Explicit through model for MusicianProfile ↔ Instrument."""

    class Proficiency(models.TextChoices):
        BEGINNER = "beginner", "Beginner"
        INTERMEDIATE = "intermediate", "Intermediate"
        ADVANCED = "advanced", "Advanced"

    id = models.UUIDField(primary_key=True, default=_new_uuid, editable=False)
    # String FK — MusicianProfile is defined after this class.
    profile = models.ForeignKey(
        "MusicianProfile",
        on_delete=models.CASCADE,
        related_name="musician_instruments",
    )
    instrument = models.ForeignKey(
        Instrument,
        on_delete=models.CASCADE,
        related_name="musician_instruments",
    )
    proficiency = models.CharField(
        max_length=20,
        choices=Proficiency.choices,
        default=Proficiency.INTERMEDIATE,
    )

    class Meta:
        ordering = ["instrument__name"]
        constraints = [
            models.UniqueConstraint(
                fields=["profile", "instrument"],
                name="unique_musician_instrument",
            )
        ]

    def __str__(self) -> str:
        return f"{self.profile} — {self.instrument} ({self.proficiency})"


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------


class MusicianProfile(models.Model):
    id = models.UUIDField(primary_key=True, default=_new_uuid, editable=False)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="musician_profile",
    )

    bio = models.TextField(blank=True)
    city = models.CharField(max_length=100, blank=True)
    country = models.CharField(max_length=100, blank=True)
    is_available = models.BooleanField(default=True)

    instruments = models.ManyToManyField(
        Instrument,
        through=MusicianInstrument,
        blank=True,
        related_name="profiles",
    )
    genres = models.ManyToManyField(
        Genre,
        blank=True,
        related_name="profiles",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"MusicianProfile({self.user_id})"
