"""Venues-app test fixtures + auth helpers."""

import pytest
from rest_framework.test import APIClient

from apps.users.models import User
from apps.venues.models import Venue

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
def venue(owner: User) -> Venue:
    return Venue.objects.create(
        owner=owner,
        name="The Blue Room",
        slug="the-blue-room",
        description="An intimate jazz club.",
        address="12 MG Road",
        city="Mumbai",
        country="India",
        capacity=120,
    )
