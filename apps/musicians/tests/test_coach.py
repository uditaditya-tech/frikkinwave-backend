"""
Profile coach tests.

The coach is now purely rule-based: a weighted completeness score and structured
per-field suggestions. There used to be an LLM `tip` alongside them; it is gone,
and so is the mocking that surrounded it — these tests need no fake client, no
key and no network, because there is no longer anything to fake.

`tip` is asserted *absent* rather than simply not mentioned. Dropping a response
key is a breaking change for a client, so it is worth a test that fails if the
key quietly comes back.
"""

from __future__ import annotations

import pytest
from rest_framework.test import APIClient

from apps.musicians.models import Genre, Instrument, MusicianProfile
from apps.users.models import User

COACH_URL = "/api/musicians/profile/coach/"
PASSWORD = "StrongPass123!"


def _auth(api_client: APIClient, user: User) -> APIClient:
    resp = api_client.post("/api/auth/token/", {"email": user.email, "password": PASSWORD})
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {resp.data['access']}")
    return api_client


def _make_user(suffix: str) -> User:
    return User.objects.create_user(
        email=f"{suffix}@example.com", username=f"user-{suffix}", password=PASSWORD
    )


@pytest.mark.django_db
class TestCoach:
    def test_incomplete_profile_lists_missing_fields(self, api_client: APIClient) -> None:
        user = _make_user("sparse")
        # Only a (short) bio + city → instruments/genres/sound_url/country missing.
        MusicianProfile.objects.create(user=user, bio="short", city="Pune")
        _auth(api_client, user)

        response = api_client.get(COACH_URL)

        assert response.status_code == 200
        fields = {s["field"] for s in response.data["suggestions"]}
        # bio is under the 30-char floor, so it is still flagged.
        assert {"bio", "instruments", "genres", "sound_url", "country"} <= fields
        assert response.data["completeness"] == 10  # only city (10) earned

    def test_complete_profile_scores_100(self, api_client: APIClient) -> None:
        user = _make_user("full")
        profile = MusicianProfile.objects.create(
            user=user,
            bio="I am a seasoned jazz guitarist with ten years on stage.",
            city="Mumbai",
            country="India",
            sound_url="https://example.com/track",
        )
        profile.musician_instruments.create(
            instrument=Instrument.objects.create(name="Guitar", slug="guitar"),
            proficiency="advanced",
        )
        profile.genres.add(Genre.objects.create(name="Jazz", slug="jazz"))
        _auth(api_client, user)

        response = api_client.get(COACH_URL)

        assert response.data["completeness"] == 100
        assert response.data["suggestions"] == []

    def test_the_response_no_longer_carries_a_tip(self, api_client: APIClient) -> None:
        """Dropping a key is a breaking change; this fails if it silently returns."""
        user = _make_user("notip")
        MusicianProfile.objects.create(user=user, bio="short")
        _auth(api_client, user)

        response = api_client.get(COACH_URL)

        assert set(response.data) == {"completeness", "suggestions"}

    def test_is_deterministic(self, api_client: APIClient) -> None:
        """
        The property the generated tip could never offer: same profile in, same
        coaching out. Worth asserting now that nothing non-deterministic remains.
        """
        user = _make_user("stable")
        MusicianProfile.objects.create(user=user, bio="short", city="Goa")
        _auth(api_client, user)

        first = api_client.get(COACH_URL).data
        second = api_client.get(COACH_URL).data

        assert first == second

    def test_viewer_without_profile_returns_400(self, api_client: APIClient) -> None:
        _auth(api_client, _make_user("noprofile"))
        assert api_client.get(COACH_URL).status_code == 400

    def test_unauthenticated_returns_401(self, api_client: APIClient) -> None:
        assert api_client.get(COACH_URL).status_code == 401
