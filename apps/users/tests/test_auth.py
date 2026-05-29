"""
Auth endpoint tests — register, login, refresh, logout.

Coverage:
  - Happy path for each endpoint
  - At least one negative path per endpoint
"""

import pytest
from rest_framework.test import APIClient

from apps.users.models import User

REGISTER_URL = "/api/auth/register/"
LOGIN_URL = "/api/auth/token/"
REFRESH_URL = "/api/auth/token/refresh/"
LOGOUT_URL = "/api/auth/logout/"


# ---------------------------------------------------------------------------
# Register
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestRegister:
    def test_success_returns_token_pair(self, api_client: APIClient) -> None:
        payload = {
            "email": "new@example.com",
            "username": "newuser",
            "password": "StrongPass123!",
            "password_confirm": "StrongPass123!",
        }
        response = api_client.post(REGISTER_URL, payload)
        assert response.status_code == 201
        assert "access" in response.data
        assert "refresh" in response.data
        assert User.objects.filter(email="new@example.com").exists()

    def test_duplicate_email_returns_400(self, api_client: APIClient, user: User) -> None:
        payload = {
            "email": "user@example.com",  # same as fixture
            "username": "anotheruser",
            "password": "StrongPass123!",
            "password_confirm": "StrongPass123!",
        }
        response = api_client.post(REGISTER_URL, payload)
        assert response.status_code == 400

    def test_duplicate_username_returns_400(self, api_client: APIClient, user: User) -> None:
        payload = {
            "email": "different@example.com",
            "username": "testuser",  # same as fixture
            "password": "StrongPass123!",
            "password_confirm": "StrongPass123!",
        }
        response = api_client.post(REGISTER_URL, payload)
        assert response.status_code == 400

    def test_password_mismatch_returns_400(self, api_client: APIClient) -> None:
        payload = {
            "email": "new@example.com",
            "username": "newuser",
            "password": "StrongPass123!",
            "password_confirm": "DifferentPass456!",
        }
        response = api_client.post(REGISTER_URL, payload)
        assert response.status_code == 400

    def test_missing_fields_returns_400(self, api_client: APIClient) -> None:
        response = api_client.post(REGISTER_URL, {"email": "new@example.com"})
        assert response.status_code == 400


# ---------------------------------------------------------------------------
# Login (token obtain)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestLogin:
    def test_success_returns_token_pair(self, api_client: APIClient, user: User) -> None:
        response = api_client.post(
            LOGIN_URL, {"email": "user@example.com", "password": "StrongPass123!"}
        )
        assert response.status_code == 200
        assert "access" in response.data
        assert "refresh" in response.data

    def test_wrong_password_returns_401(self, api_client: APIClient, user: User) -> None:
        response = api_client.post(
            LOGIN_URL, {"email": "user@example.com", "password": "WrongPass999!"}
        )
        assert response.status_code == 401

    def test_nonexistent_email_returns_401(self, api_client: APIClient, db: None) -> None:
        response = api_client.post(
            LOGIN_URL, {"email": "nobody@example.com", "password": "StrongPass123!"}
        )
        assert response.status_code == 401


# ---------------------------------------------------------------------------
# Refresh
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestRefresh:
    def test_success_returns_new_access_token(self, api_client: APIClient, user: User) -> None:
        login = api_client.post(
            LOGIN_URL, {"email": "user@example.com", "password": "StrongPass123!"}
        )
        response = api_client.post(REFRESH_URL, {"refresh": login.data["refresh"]})
        assert response.status_code == 200
        assert "access" in response.data

    def test_invalid_token_returns_401(self, api_client: APIClient, db: None) -> None:
        response = api_client.post(REFRESH_URL, {"refresh": "thisisnotavalidtoken"})
        assert response.status_code == 401


# ---------------------------------------------------------------------------
# Logout
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestLogout:
    def test_success_blacklists_token(self, api_client: APIClient, user: User) -> None:
        login = api_client.post(
            LOGIN_URL, {"email": "user@example.com", "password": "StrongPass123!"}
        )
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['access']}")
        response = api_client.post(LOGOUT_URL, {"refresh": login.data["refresh"]})
        assert response.status_code == 204

    def test_blacklisted_token_cannot_refresh(self, api_client: APIClient, user: User) -> None:
        """After logout, the refresh token must be dead."""
        login = api_client.post(
            LOGIN_URL, {"email": "user@example.com", "password": "StrongPass123!"}
        )
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['access']}")
        api_client.post(LOGOUT_URL, {"refresh": login.data["refresh"]})
        # Attempting to refresh with the blacklisted token must fail
        response = api_client.post(REFRESH_URL, {"refresh": login.data["refresh"]})
        assert response.status_code == 401

    def test_missing_refresh_returns_400(self, api_client: APIClient, user: User) -> None:
        login = api_client.post(
            LOGIN_URL, {"email": "user@example.com", "password": "StrongPass123!"}
        )
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['access']}")
        response = api_client.post(LOGOUT_URL, {})
        assert response.status_code == 400

    def test_invalid_token_returns_400(self, api_client: APIClient, user: User) -> None:
        login = api_client.post(
            LOGIN_URL, {"email": "user@example.com", "password": "StrongPass123!"}
        )
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['access']}")
        response = api_client.post(LOGOUT_URL, {"refresh": "notavalidtoken"})
        assert response.status_code == 400

    def test_unauthenticated_returns_401(self, api_client: APIClient, db: None) -> None:
        response = api_client.post(LOGOUT_URL, {"refresh": "sometoken"})
        assert response.status_code == 401
