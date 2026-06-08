"""
The musician profile payload embeds the owner's aggregate review rating
(`{average_rating, count}`) on single-profile responses — and deliberately NOT
on the paginated list/search feeds (to avoid a per-row aggregate N+1).
"""

import uuid

import pytest
from rest_framework.test import APIClient

from apps.musicians.models import MusicianProfile
from apps.reviews.models import Review
from apps.users.models import User

PASSWORD = "StrongPass123!"


def _review(author: User, subject: User, rating: int) -> Review:
    return Review.objects.create(
        author=author,
        subject=subject,
        rating=rating,
        context_type=Review.Context.ENGAGEMENT,
        context_id=uuid.uuid4(),
    )


def _make_user(suffix: str) -> User:
    return User.objects.create_user(
        email=f"{suffix}@example.com", username=suffix, password=PASSWORD
    )


@pytest.mark.django_db
class TestProfileRating:
    def test_public_profile_includes_average_and_count(
        self, api_client: APIClient, profile: MusicianProfile, user: User
    ) -> None:
        _review(_make_user("a"), user, 5)
        _review(_make_user("b"), user, 2)
        resp = api_client.get(f"/api/musicians/profiles/{user.username}/")
        assert resp.status_code == 200
        assert resp.data["rating"] == {"average_rating": 3.5, "count": 2}

    def test_public_profile_rating_empty_when_no_reviews(
        self, api_client: APIClient, profile: MusicianProfile, user: User
    ) -> None:
        resp = api_client.get(f"/api/musicians/profiles/{user.username}/")
        assert resp.status_code == 200
        assert resp.data["rating"] == {"average_rating": None, "count": 0}

    def test_me_payload_includes_rating(
        self, api_client: APIClient, profile: MusicianProfile, user: User
    ) -> None:
        _review(_make_user("c"), user, 4)
        token = api_client.post(
            "/api/auth/token/", {"email": user.email, "password": PASSWORD}
        ).data["access"]
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        resp = api_client.get("/api/musicians/profile/me/")
        assert resp.status_code == 200
        assert resp.data["rating"] == {"average_rating": 4.0, "count": 1}

    def test_list_feed_omits_rating(
        self, api_client: APIClient, profile: MusicianProfile, user: User
    ) -> None:
        _review(_make_user("d"), user, 5)
        resp = api_client.get("/api/musicians/profiles/")
        assert resp.status_code == 200
        assert resp.data["results"], "expected at least one profile in the feed"
        assert "rating" not in resp.data["results"][0]
