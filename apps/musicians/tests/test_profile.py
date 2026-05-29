"""
Profile endpoint tests — create, retrieve, update.

Coverage:
  - Happy path for each endpoint
  - At least one negative path per endpoint
"""

import pytest
from rest_framework.test import APIClient

from apps.musicians.models import Genre, Instrument, MusicianProfile
from apps.users.models import User

PROFILE_URL = "/api/musicians/profile/"
PROFILE_ME_URL = "/api/musicians/profile/me/"


def _auth(api_client: APIClient, user: User) -> APIClient:
    """Log in and attach Bearer token to the client."""
    resp = api_client.post(
        "/api/auth/token/",
        {"email": user.email, "password": "StrongPass123!"},
    )
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {resp.data['access']}")
    return api_client


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCreateProfile:
    def test_success_returns_201(self, api_client: APIClient, user: User) -> None:
        _auth(api_client, user)
        response = api_client.post(PROFILE_URL, {"bio": "Guitarist", "city": "Delhi"})
        assert response.status_code == 201
        assert response.data["bio"] == "Guitarist"
        assert response.data["city"] == "Delhi"
        assert MusicianProfile.objects.filter(user=user).exists()

    def test_with_instruments_and_genres(
        self,
        api_client: APIClient,
        user: User,
        instrument: Instrument,
        genre: Genre,
    ) -> None:
        _auth(api_client, user)
        payload = {
            "instruments": [{"instrument": str(instrument.id), "proficiency": "advanced"}],
            "genres": [str(genre.id)],
        }
        response = api_client.post(PROFILE_URL, payload, format="json")
        assert response.status_code == 201
        assert len(response.data["instruments"]) == 1
        assert response.data["instruments"][0]["proficiency"] == "advanced"
        assert len(response.data["genres"]) == 1

    def test_duplicate_returns_409(
        self, api_client: APIClient, user: User, profile: MusicianProfile
    ) -> None:
        _auth(api_client, user)
        response = api_client.post(PROFILE_URL, {"bio": "Second profile"})
        assert response.status_code == 409

    def test_unauthenticated_returns_401(self, api_client: APIClient) -> None:
        response = api_client.post(PROFILE_URL, {"bio": "No token"})
        assert response.status_code == 401


# ---------------------------------------------------------------------------
# Retrieve (me)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestRetrieveProfile:
    def test_success_returns_own_profile(
        self, api_client: APIClient, user: User, profile: MusicianProfile
    ) -> None:
        _auth(api_client, user)
        response = api_client.get(PROFILE_ME_URL)
        assert response.status_code == 200
        assert str(response.data["user_id"]) == str(user.id)
        assert response.data["bio"] == profile.bio

    def test_no_profile_returns_404(self, api_client: APIClient, user: User) -> None:
        _auth(api_client, user)
        response = api_client.get(PROFILE_ME_URL)
        assert response.status_code == 404

    def test_unauthenticated_returns_401(self, api_client: APIClient) -> None:
        response = api_client.get(PROFILE_ME_URL)
        assert response.status_code == 401


# ---------------------------------------------------------------------------
# Update (me)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestUpdateProfile:
    def test_scalar_fields_updated(
        self, api_client: APIClient, user: User, profile: MusicianProfile
    ) -> None:
        _auth(api_client, user)
        response = api_client.patch(PROFILE_ME_URL, {"city": "Bangalore", "is_available": False})
        assert response.status_code == 200
        assert response.data["city"] == "Bangalore"
        assert response.data["is_available"] is False
        # Fields not in payload are unchanged
        assert response.data["bio"] == profile.bio

    def test_instruments_replaced(
        self,
        api_client: APIClient,
        user: User,
        profile: MusicianProfile,
        instrument: Instrument,
    ) -> None:
        _auth(api_client, user)
        payload = {"instruments": [{"instrument": str(instrument.id), "proficiency": "beginner"}]}
        response = api_client.patch(PROFILE_ME_URL, payload, format="json")
        assert response.status_code == 200
        assert len(response.data["instruments"]) == 1
        assert response.data["instruments"][0]["proficiency"] == "beginner"

    def test_genres_replaced(
        self,
        api_client: APIClient,
        user: User,
        profile: MusicianProfile,
        genre: Genre,
    ) -> None:
        _auth(api_client, user)
        payload = {"genres": [str(genre.id)]}
        response = api_client.patch(PROFILE_ME_URL, payload, format="json")
        assert response.status_code == 200
        assert len(response.data["genres"]) == 1
        assert response.data["genres"][0]["name"] == genre.name

    def test_omitted_instruments_unchanged(
        self,
        api_client: APIClient,
        user: User,
        profile: MusicianProfile,
        instrument: Instrument,
    ) -> None:
        """PATCH without 'instruments' key must not clear existing instruments."""
        from apps.musicians.models import MusicianInstrument

        MusicianInstrument.objects.create(
            profile=profile,
            instrument=instrument,
            proficiency=MusicianInstrument.Proficiency.ADVANCED,
        )
        _auth(api_client, user)
        # PATCH only bio — instruments must be untouched
        response = api_client.patch(PROFILE_ME_URL, {"bio": "Updated bio"})
        assert response.status_code == 200
        assert len(response.data["instruments"]) == 1

    def test_no_profile_returns_404(self, api_client: APIClient, user: User) -> None:
        _auth(api_client, user)
        response = api_client.patch(PROFILE_ME_URL, {"bio": "Updated"})
        assert response.status_code == 404

    def test_unauthenticated_returns_401(self, api_client: APIClient) -> None:
        response = api_client.patch(PROFILE_ME_URL, {"bio": "Updated"})
        assert response.status_code == 401
