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
from pgvector.django import HnswIndex, VectorField


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
    # External audio link (SoundCloud / Spotify / YouTube) shown on the profile.
    sound_url = models.URLField(max_length=500, blank=True)

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


# ---------------------------------------------------------------------------
# AI embeddings (Phase 2)
# ---------------------------------------------------------------------------

# OpenAI text-embedding-3-small output dimensionality.
EMBEDDING_DIMENSIONS = 1536


class ProfileEmbedding(models.Model):
    """
    Vector embedding of a MusicianProfile, used for semantic jam-partner search.

    Populated asynchronously by a Celery task on profile save (Phase 2.4) from
    text-embedding-3-small. The HNSW index with cosine distance backs the
    nearest-neighbour query in Phase 2.5 — text-embedding-3-small vectors are
    normalised, so cosine is the right metric.
    """

    id = models.UUIDField(primary_key=True, default=_new_uuid, editable=False)
    profile = models.OneToOneField(
        MusicianProfile,
        on_delete=models.CASCADE,
        related_name="embedding",
    )
    embedding = VectorField(dimensions=EMBEDDING_DIMENSIONS)
    # The raw text that was embedded — kept for debugging / re-embedding.
    embedding_text = models.TextField()
    # When the embedding was last computed (auto-updates on each recompute).
    generated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-generated_at"]
        indexes = [
            HnswIndex(
                name="profile_embedding_hnsw",
                fields=["embedding"],
                m=16,
                ef_construction=64,
                opclasses=["vector_cosine_ops"],
            ),
        ]

    def __str__(self) -> str:
        return f"ProfileEmbedding({self.profile_id})"


class CompatibilityBlurb(models.Model):
    """
    Cached "why you might click" text for an unordered pair of profiles.

    The pair is canonical: callers order the two profiles by id before lookup
    (profile_a.id < profile_b.id), so (A,B) and (B,A) map to one row. The blurb
    is phrased neutrally ("you both…"), generated once by gpt-4o-mini and cached.
    """

    id = models.UUIDField(primary_key=True, default=_new_uuid, editable=False)
    profile_a = models.ForeignKey(
        MusicianProfile,
        on_delete=models.CASCADE,
        related_name="compat_blurbs_as_a",
    )
    profile_b = models.ForeignKey(
        MusicianProfile,
        on_delete=models.CASCADE,
        related_name="compat_blurbs_as_b",
    )
    blurb = models.TextField()
    generated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-generated_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["profile_a", "profile_b"],
                name="unique_compatibility_pair",
            ),
        ]

    def __str__(self) -> str:
        return f"CompatibilityBlurb({self.profile_a_id}, {self.profile_b_id})"
