"""Reviews-app test fixtures + helpers."""

import pytest
from rest_framework.test import APIClient

from apps.engagements.models import EngagementRequest
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


def make_engagement(
    requester: User,
    musician: User,
    status: str = EngagementRequest.Status.COMPLETED,
) -> EngagementRequest:
    return EngagementRequest.objects.create(requester=requester, musician=musician, status=status)


@pytest.fixture
def requester(db: None) -> User:
    return make_user("requester")


@pytest.fixture
def musician(db: None) -> User:
    return make_user("musician")
