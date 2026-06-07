"""Bands-app test fixtures + auth helpers."""

import pytest
from rest_framework.test import APIClient

from apps.bands.models import Band
from apps.users.models import User

PASSWORD = "StrongPass123!"


def make_user(suffix: str) -> User:
    return User.objects.create_user(
        email=f"{suffix}@example.com",
        username=f"user-{suffix}",
        password=PASSWORD,
    )


def auth(api_client: APIClient, user: User) -> APIClient:
    resp = api_client.post("/api/auth/token/", {"email": user.email, "password": PASSWORD})
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {resp.data['access']}")
    return api_client


@pytest.fixture
def owner(db: None) -> User:
    return make_user("owner")


@pytest.fixture
def band(owner: User) -> Band:
    return Band.objects.create(
        owner=owner,
        name="The Midnight Riff",
        slug="the-midnight-riff",
        bio="A funk-soul outfit.",
        city="Mumbai",
        country="India",
    )
