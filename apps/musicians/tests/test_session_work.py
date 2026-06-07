"""
Session-musician intent on the profile (Phase 4 Block B).

Coverage: create + update set the fields, the read serializer exposes them,
and the discovery feed filters by ?open_to_session=true.
"""

import pytest
from rest_framework.test import APIClient

from apps.musicians.models import MusicianProfile
from apps.users.models import User

PROFILE_URL = "/api/musicians/profile/"
PROFILE_ME_URL = "/api/musicians/profile/me/"
PROFILES_URL = "/api/musicians/profiles/"
PASSWORD = "StrongPass123!"


def _make_user(suffix: str) -> User:
    return User.objects.create_user(
        email=f"{suffix}@example.com", username=f"user-{suffix}", password=PASSWORD
    )


def _auth(api_client: APIClient, user: User) -> APIClient:
    resp = api_client.post("/api/auth/token/", {"email": user.email, "password": PASSWORD})
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {resp.data['access']}")
    return api_client


@pytest.mark.django_db
class TestSessionWorkFields:
    def test_defaults_off(self, api_client: APIClient, user: User) -> None:
        _auth(api_client, user)
        response = api_client.post(PROFILE_URL, {"bio": "Drummer"}, format="json")
        assert response.status_code == 201
        assert response.data["is_open_to_session_work"] is False
        assert response.data["session_rate"] == ""

    def test_create_with_session_intent(self, api_client: APIClient, user: User) -> None:
        _auth(api_client, user)
        response = api_client.post(
            PROFILE_URL,
            {"is_open_to_session_work": True, "session_rate": "₹5000/session"},
            format="json",
        )
        assert response.status_code == 201
        assert response.data["is_open_to_session_work"] is True
        assert response.data["session_rate"] == "₹5000/session"

    def test_patch_updates_intent(self, api_client: APIClient, user: User) -> None:
        MusicianProfile.objects.create(user=user)
        _auth(api_client, user)
        response = api_client.patch(
            PROFILE_ME_URL, {"is_open_to_session_work": True}, format="json"
        )
        assert response.status_code == 200
        assert response.data["is_open_to_session_work"] is True


@pytest.mark.django_db
class TestSessionWorkFilter:
    def test_filter_open_to_session(self, api_client: APIClient, db: None) -> None:
        open_user = _make_user("open")
        closed_user = _make_user("closed")
        MusicianProfile.objects.create(user=open_user, city="Mumbai", is_open_to_session_work=True)
        MusicianProfile.objects.create(
            user=closed_user, city="Mumbai", is_open_to_session_work=False
        )

        response = api_client.get(PROFILES_URL, {"open_to_session": "true"})
        assert response.status_code == 200
        usernames = {row["username"] for row in response.data["results"]}
        assert usernames == {"user-open"}
