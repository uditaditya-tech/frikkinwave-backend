import pytest
from rest_framework.test import APIClient

from apps.users.models import User


@pytest.fixture
def api_client() -> APIClient:
    return APIClient()


@pytest.fixture
def user(db: None) -> User:
    return User.objects.create_user(
        email="user@example.com",
        username="testuser",
        password="StrongPass123!",
    )
