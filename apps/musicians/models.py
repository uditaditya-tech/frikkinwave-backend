"""
Models for the musicians app.

MusicianProfile extends the User with music-specific data.
User → MusicianProfile is a one-to-one relationship created at profile setup time.

Design decisions:
- city/country as free-text for Phase 1 simplicity; can normalise to lookup table
  later via a migration if geo-search demands it.
- is_available signals jam-partner availability — the Phase 1 anchor feature.
- Reference AUTH_USER_MODEL via settings string to avoid cross-app model imports.
- MusicianInstrument is an explicit through model so we can store proficiency level.
- Genre is a plain M2M — no extra attributes needed on the join.
"""

import uuid
from typing import Any, ClassVar

import uuid6
from django.conf import settings
from django.db import models
from django.utils.text import slugify


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
        ordering: ClassVar[list[str]] = ["name"]

    def __str__(self) -> str:
        return self.name

    def save(self, *args: object, **kwargs: object) -> None:
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)  # type: ignore[arg-type]


class Genre(models.Model):
    id = models.UUIDField(primary_key=True, default=_new_uuid, editable=False)
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=100, unique=True)

    class Meta:
        ordering: ClassVar[list[str]] = ["name"]

    def __str__(self) -> str:
        return self.name

    def save(self, *args: object, **kwargs: object) -> None:
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)  # type: ignore[arg-type]


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

    # Any annotation required: MusicianInstrument is defined after this class, so mypy
    # cannot resolve the forward-referenced through model — known django-stubs limitation.
    instruments: Any = models.ManyToManyField(
        Instrument,
        through="MusicianInstrument",
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
        ordering: ClassVar[list[str]] = ["-created_at"]

    def __str__(self) -> str:
        return f"MusicianProfile({self.user_id})"


# ---------------------------------------------------------------------------
# Through model
# ---------------------------------------------------------------------------


class MusicianInstrument(models.Model):
    """Explicit through model for MusicianProfile ↔ Instrument."""

    class Proficiency(models.TextChoices):
        BEGINNER = "beginner", "Beginner"
        INTERMEDIATE = "intermediate", "Intermediate"
        ADVANCED = "advanced", "Advanced"

    id = models.UUIDField(primary_key=True, default=_new_uuid, editable=False)
    profile = models.ForeignKey(
        MusicianProfile,
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
        ordering: ClassVar[list[str]] = ["instrument__name"]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["profile", "instrument"],
                name="unique_musician_instrument",
            )
        ]

    def __str__(self) -> str:
        return f"{self.profile} — {self.instrument} ({self.proficiency})"
