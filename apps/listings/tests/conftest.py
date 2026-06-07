"""Listings-app test fixtures + auth helpers."""

import pytest
from rest_framework.test import APIClient

from apps.listings.models import Listing
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
def author(db: None) -> User:
    return make_user("author")


@pytest.fixture
def listing(author: User) -> Listing:
    return Listing.objects.create(
        author=author,
        listing_type=Listing.ListingType.GIG,
        title="Bassist wanted for wedding gig",
        description="Funk/soul wedding band needs a bassist for a one-off show.",
        city="Mumbai",
        country="India",
        is_paid=True,
        pay_description="₹5000 for the night",
    )
