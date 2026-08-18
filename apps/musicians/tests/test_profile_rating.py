"""
The profile payload exposes a *denormalized* review rollup.

Architecture under test (see MICROSERVICES.md §6): the musicians app never
queries the reviews tables. Reviews pushes the aggregate onto the profile via a
post-commit Celery task, and the serializer performs a pure local read. These
tests cover both halves plus the reconciliation command.
"""

from collections.abc import Callable
from typing import Any

import pytest
from django.core.management import call_command
from rest_framework.test import APIClient

from apps.engagements.models import EngagementRequest
from apps.musicians.models import MusicianProfile
from apps.reviews.models import Review
from apps.reviews.services import create_review, propagate_rating_to_profile
from apps.users.models import User

PASSWORD = "StrongPass123!"


def _make_user(suffix: str) -> User:
    return User.objects.create_user(
        email=f"{suffix}@example.com", username=suffix, password=PASSWORD
    )


def _completed_engagement(requester: User, musician: User) -> EngagementRequest:
    return EngagementRequest.objects.create(
        requester=requester,
        musician=musician,
        status=EngagementRequest.Status.COMPLETED,
    )


@pytest.mark.django_db
class TestSerializerReadsDenormalizedColumns:
    def test_public_profile_exposes_stored_rollup(
        self, api_client: APIClient, profile: MusicianProfile, user: User
    ) -> None:
        MusicianProfile.objects.filter(pk=profile.pk).update(rating_avg=4.25, rating_count=8)
        resp = api_client.get(f"/api/musicians/profiles/{user.username}/")
        assert resp.status_code == 200
        assert resp.data["rating"] == {"average_rating": 4.25, "count": 8}

    def test_defaults_to_empty_rollup(
        self, api_client: APIClient, profile: MusicianProfile, user: User
    ) -> None:
        resp = api_client.get(f"/api/musicians/profiles/{user.username}/")
        assert resp.data["rating"] == {"average_rating": None, "count": 0}

    def test_reading_a_profile_never_queries_reviews(
        self,
        api_client: APIClient,
        profile: MusicianProfile,
        user: User,
        django_assert_num_queries: Any,
    ) -> None:
        # A review exists but the rollup was never pushed — the payload must still
        # report 0, proving the serializer reads columns rather than aggregating.
        other = _make_user("reviewer")
        Review.objects.create(
            author=other,
            subject=user,
            rating=5,
            context_type=Review.Context.ENGAGEMENT,
            context_id=_completed_engagement(other, user).id,
        )
        resp = api_client.get(f"/api/musicians/profiles/{user.username}/")
        assert resp.data["rating"] == {"average_rating": None, "count": 0}

    def test_list_feed_omits_rating(
        self, api_client: APIClient, profile: MusicianProfile, user: User
    ) -> None:
        resp = api_client.get("/api/musicians/profiles/")
        assert resp.status_code == 200
        assert resp.data["results"]
        assert "rating" not in resp.data["results"][0]


@pytest.mark.django_db
class TestPropagation:
    def test_creating_a_review_updates_the_profile(
        self,
        profile: MusicianProfile,
        user: User,
        django_capture_on_commit_callbacks: Callable[..., Any],
    ) -> None:
        hirer = _make_user("hirer")
        engagement = _completed_engagement(hirer, user)
        with django_capture_on_commit_callbacks(execute=True):
            create_review(
                author=hirer,
                subject_username=user.username,
                engagement_id=str(engagement.id),
                rating=4,
            )
        profile.refresh_from_db()
        assert profile.rating_avg == 4.0
        assert profile.rating_count == 1

    def test_second_review_recomputes_the_average(
        self,
        profile: MusicianProfile,
        user: User,
        django_capture_on_commit_callbacks: Callable[..., Any],
    ) -> None:
        for i, rating in enumerate([5, 2]):
            hirer = _make_user(f"hirer{i}")
            engagement = _completed_engagement(hirer, user)
            with django_capture_on_commit_callbacks(execute=True):
                create_review(
                    author=hirer,
                    subject_username=user.username,
                    engagement_id=str(engagement.id),
                    rating=rating,
                )
        profile.refresh_from_db()
        assert profile.rating_avg == 3.5
        assert profile.rating_count == 2

    def test_propagation_is_idempotent(self, profile: MusicianProfile, user: User) -> None:
        other = _make_user("rev")
        Review.objects.create(
            author=other,
            subject=user,
            rating=3,
            context_type=Review.Context.ENGAGEMENT,
            context_id=_completed_engagement(other, user).id,
        )
        for _ in range(3):  # a retried task must converge, not accumulate
            propagate_rating_to_profile(subject_user_id=str(user.pk))
        profile.refresh_from_db()
        assert profile.rating_avg == 3.0
        assert profile.rating_count == 1

    def test_user_without_a_profile_is_a_noop(self) -> None:
        loner = _make_user("loner")
        propagate_rating_to_profile(subject_user_id=str(loner.pk))  # must not raise


@pytest.mark.django_db
class TestBackfillCommand:
    def test_rebuilds_drifted_rollups(self, profile: MusicianProfile, user: User) -> None:
        other = _make_user("rev2")
        Review.objects.create(
            author=other,
            subject=user,
            rating=5,
            context_type=Review.Context.ENGAGEMENT,
            context_id=_completed_engagement(other, user).id,
        )
        # Simulate drift (lost task / restored snapshot).
        MusicianProfile.objects.filter(pk=profile.pk).update(rating_avg=1.0, rating_count=99)
        call_command("backfill_profile_ratings")
        profile.refresh_from_db()
        assert profile.rating_avg == 5.0
        assert profile.rating_count == 1
