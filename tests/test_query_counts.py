"""
N+1 guardrails for the list endpoints (TESTING.md, Gap A).

Every list service already uses `select_related`/`prefetch_related` — 29 calls
across eight apps. **One** assertion in the whole suite covered any of it, so
deleting a `select_related` broke nothing: the endpoint simply got slower, and
only in production, and only under enough rows to notice.

These assert the property that actually matters — **the query count does not grow
with the number of rows returned** — rather than pinning an exact number. An exact
count is brittle (any extra `SELECT` anywhere edits every test) and, worse, it
fails for the wrong reason: an N+1 and a harmless new query look identical.

Both row counts stay under the paginators' `page_size = 20`, so the comparison is
between two single pages and never accidentally measures pagination itself.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from apps.bands.models import Band
from apps.listings.models import Listing
from apps.reviews.models import Review
from apps.social.models import Follow
from apps.users.models import User
from apps.venues.models import Venue

FEW = 2
MANY = 12


def _users(prefix: str, n: int) -> list[User]:
    """n distinct users, so an unfetched FK shows up as n extra queries."""
    return [
        User.objects.create_user(
            email=f"{prefix}{i}@example.com",
            username=f"{prefix}{i}",
            password="StrongPass123!",
        )
        for i in range(n)
    ]


def _seed_listings(n: int) -> str:
    for i, author in enumerate(_users("listing-author", n)):
        Listing.objects.create(
            author=author,
            listing_type=Listing.ListingType.GIG,
            title=f"Listing {i}",
            description="Seeded for the N+1 guard.",
            city="Mumbai",
            country="India",
            is_paid=False,
        )
    return "/api/listings/"


def _seed_venues(n: int) -> str:
    for i, owner in enumerate(_users("venue-owner", n)):
        Venue.objects.create(
            owner=owner,
            name=f"Venue {i}",
            slug=f"venue-{i}",
            description="Seeded for the N+1 guard.",
            city="Mumbai",
            country="India",
        )
    return "/api/venues/"


def _seed_bands(n: int) -> str:
    for i, owner in enumerate(_users("band-owner", n)):
        Band.objects.create(
            owner=owner,
            name=f"Band {i}",
            slug=f"band-{i}",
            bio="Seeded for the N+1 guard.",
            city="Mumbai",
            country="India",
        )
    return "/api/bands/"


def _seed_reviews(n: int) -> str:
    """n reviews of ONE subject by n distinct authors — the FK that must be joined."""
    subject = User.objects.create_user(
        email="review-subject@example.com",
        username="review-subject",
        password="StrongPass123!",
    )
    for author in _users("review-author", n):
        Review.objects.create(
            author=author,
            subject=subject,
            rating=4,
            comment="Seeded for the N+1 guard.",
            context_type=Review.Context.ENGAGEMENT,
            context_id=uuid.uuid4(),
        )
    return f"/api/reviews/{subject.username}/"


def _seed_following(n: int) -> str:
    follower = User.objects.create_user(
        email="follower@example.com",
        username="follower",
        password="StrongPass123!",
    )
    for followed in _users("followed", n):
        Follow.objects.create(follower=follower, followed=followed)
    return f"/api/social/{follower.username}/following/"


SEEDERS: dict[str, Callable[[int], str]] = {
    "listings": _seed_listings,
    "venues": _seed_venues,
    "bands": _seed_bands,
    "reviews": _seed_reviews,
    "following": _seed_following,
}


def _count_queries(client: APIClient, url: str) -> int:
    with CaptureQueriesContext(connection) as captured:
        response = client.get(url)
        assert response.status_code == 200, f"{url} -> {response.status_code}"
    return len(captured)


@pytest.mark.django_db
@pytest.mark.parametrize("name", sorted(SEEDERS))
def test_list_endpoint_query_count_does_not_grow_with_rows(name: str) -> None:
    """
    Serving 12 rows must cost the same number of queries as serving 2.

    If this fails with a difference close to MANY - FEW, a `select_related` or
    `prefetch_related` has been dropped from that endpoint's service function.
    """
    seed = SEEDERS[name]

    few_url = seed(FEW)
    few = _count_queries(APIClient(), few_url)

    # Reset so the second measurement starts from a comparable state, then seed
    # the larger set. Fresh client: DRF caches nothing across instances, but a
    # reused one would carry a session/auth header into the count.
    _reset_all()
    many_url = seed(MANY)
    many = _count_queries(APIClient(), many_url)

    assert few == many, (
        f"/{name}/ issued {few} queries for {FEW} rows but {many} for {MANY} — "
        f"the count grows with rows, which is an N+1. "
        f"Check select_related/prefetch_related in the list service."
    )


def _reset_all() -> None:
    """Drop every seeded row so the second pass measures only its own data."""
    Follow.objects.all().delete()
    Review.objects.all().delete()
    Listing.objects.all().delete()
    Venue.objects.all().delete()
    Band.objects.all().delete()
    User.objects.all().delete()
