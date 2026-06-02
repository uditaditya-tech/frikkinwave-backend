"""
ProfileEmbedding storage tests (Phase 2.3).

These exercise the pgvector layer directly — round-trip, the one-embedding-per-
profile constraint, cosine nearest-neighbour ordering (which also proves the
`vector` extension is enabled), and dimension enforcement. No OpenAI here; the
embedding pipeline that populates these vectors lands in 2.4.
"""

import pytest
from django.db import DataError, IntegrityError
from pgvector.django import CosineDistance

from apps.musicians.models import EMBEDDING_DIMENSIONS, MusicianProfile, ProfileEmbedding
from apps.users.models import User


def _unit_vector(hot_index: int) -> list[float]:
    """A 1536-dim vector that is 1.0 at hot_index, 0.0 elsewhere."""
    vec = [0.0] * EMBEDDING_DIMENSIONS
    vec[hot_index] = 1.0
    return vec


def _make_profile(suffix: str) -> MusicianProfile:
    user = User.objects.create_user(
        email=f"{suffix}@example.com",
        username=f"user-{suffix}",
        password="StrongPass123!",
    )
    return MusicianProfile.objects.create(user=user, bio=f"bio {suffix}")


@pytest.mark.django_db
class TestProfileEmbeddingStorage:
    def test_vector_round_trips(self, profile: MusicianProfile) -> None:
        vec = _unit_vector(0)
        vec[5] = 0.25
        ProfileEmbedding.objects.create(profile=profile, embedding=vec, embedding_text="hi")

        stored = ProfileEmbedding.objects.get(profile=profile)
        assert stored.embedding.tolist() == pytest.approx(vec)
        assert stored.embedding_text == "hi"
        assert stored.generated_at is not None

    def test_one_embedding_per_profile(self, profile: MusicianProfile) -> None:
        ProfileEmbedding.objects.create(
            profile=profile, embedding=_unit_vector(0), embedding_text="first"
        )
        with pytest.raises(IntegrityError):
            ProfileEmbedding.objects.create(
                profile=profile, embedding=_unit_vector(1), embedding_text="second"
            )

    def test_wrong_dimension_is_rejected(self, profile: MusicianProfile) -> None:
        with pytest.raises(DataError):
            ProfileEmbedding.objects.create(
                profile=profile, embedding=[1.0, 2.0, 3.0], embedding_text="too short"
            )


@pytest.mark.django_db
class TestCosineNearestNeighbour:
    def test_orders_by_cosine_distance(self) -> None:
        # Three orthogonal unit vectors, one per profile.
        a = _make_profile("a")
        b = _make_profile("b")
        c = _make_profile("c")
        ProfileEmbedding.objects.create(profile=a, embedding=_unit_vector(0), embedding_text="a")
        ProfileEmbedding.objects.create(profile=b, embedding=_unit_vector(1), embedding_text="b")
        ProfileEmbedding.objects.create(profile=c, embedding=_unit_vector(2), embedding_text="c")

        # Query closest to A's direction (mostly index 0, a touch of index 1).
        query = _unit_vector(0)
        query[1] = 0.1

        nearest = list(
            ProfileEmbedding.objects.order_by(CosineDistance("embedding", query)).values_list(
                "profile_id", flat=True
            )
        )
        assert nearest[0] == a.pk  # A is closest
        assert nearest[1] == b.pk  # then B (shares the small index-1 component)
        assert nearest[2] == c.pk  # C (orthogonal) is last
