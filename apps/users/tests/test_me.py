"""
Tests for GET /api/auth/me/ — current-user identity endpoint.
"""

import pytest
from rest_framework.test import APIClient

from apps.users.models import User


@pytest.mark.django_db
class TestMe:
    def test_returns_identity_when_authenticated(self, api_client: APIClient, user: User) -> None:
        api_client.force_authenticate(user=user)

        response = api_client.get("/api/auth/me/")

        assert response.status_code == 200
        assert response.data["email"] == user.email
        assert response.data["username"] == user.username
        assert str(response.data["id"]) == str(user.id)

    def test_requires_authentication(self, api_client: APIClient) -> None:
        response = api_client.get("/api/auth/me/")
        assert response.status_code == 401
