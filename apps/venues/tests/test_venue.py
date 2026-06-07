"""
Venue CRUD + browse/filter tests.

Coverage: create (slug derivation + collision), retrieve, update, soft-delete
(happy + negatives: unauth, non-owner, missing, inactive), browse with
city/country filters.
"""

import pytest
from rest_framework.test import APIClient

from apps.users.models import User
from apps.venues.models import Venue
from apps.venues.tests.conftest import auth, make_user

VENUES_URL = "/api/venues/"


def _detail_url(slug: str) -> str:
    return f"{VENUES_URL}{slug}/"


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCreateVenue:
    def test_success_returns_201_and_derives_slug(self, api_client: APIClient, owner: User) -> None:
        auth(api_client, owner)
        response = api_client.post(
            VENUES_URL,
            {"name": "The Jazz Loft", "city": "Pune", "capacity": 80},
            format="json",
        )
        assert response.status_code == 201
        assert response.data["slug"] == "the-jazz-loft"
        assert response.data["owner_username"] == owner.username
        assert response.data["capacity"] == 80

    def test_slug_collision_gets_suffix(self, api_client: APIClient, owner: User) -> None:
        Venue.objects.create(owner=owner, name="Echo", slug="echo")
        auth(api_client, owner)
        response = api_client.post(VENUES_URL, {"name": "Echo"}, format="json")
        assert response.status_code == 201
        assert response.data["slug"] == "echo-2"

    def test_unauthenticated_rejected(self, api_client: APIClient, db: None) -> None:
        response = api_client.post(VENUES_URL, {"name": "Nope"}, format="json")
        assert response.status_code == 401

    def test_missing_name_rejected(self, api_client: APIClient, owner: User) -> None:
        auth(api_client, owner)
        response = api_client.post(VENUES_URL, {"city": "Pune"}, format="json")
        assert response.status_code == 400

    def test_negative_capacity_rejected(self, api_client: APIClient, owner: User) -> None:
        auth(api_client, owner)
        response = api_client.post(VENUES_URL, {"name": "Bad Cap", "capacity": -5}, format="json")
        assert response.status_code == 400


# ---------------------------------------------------------------------------
# Retrieve
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestRetrieveVenue:
    def test_public_retrieve(self, api_client: APIClient, venue: Venue) -> None:
        response = api_client.get(_detail_url(venue.slug))
        assert response.status_code == 200
        assert response.data["name"] == venue.name
        assert response.data["capacity"] == 120

    def test_missing_returns_404(self, api_client: APIClient, db: None) -> None:
        response = api_client.get(_detail_url("ghost-venue"))
        assert response.status_code == 404

    def test_inactive_returns_404(self, api_client: APIClient, venue: Venue) -> None:
        venue.is_active = False
        venue.save(update_fields=["is_active"])
        response = api_client.get(_detail_url(venue.slug))
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Update / delete
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestMutateVenue:
    def test_owner_can_patch(self, api_client: APIClient, venue: Venue) -> None:
        auth(api_client, venue.owner)
        response = api_client.patch(_detail_url(venue.slug), {"capacity": 200}, format="json")
        assert response.status_code == 200
        assert response.data["capacity"] == 200

    def test_non_owner_patch_forbidden(self, api_client: APIClient, venue: Venue) -> None:
        auth(api_client, make_user("intruder"))
        response = api_client.patch(_detail_url(venue.slug), {"capacity": 1}, format="json")
        assert response.status_code == 403

    def test_owner_soft_deletes(self, api_client: APIClient, venue: Venue) -> None:
        auth(api_client, venue.owner)
        response = api_client.delete(_detail_url(venue.slug))
        assert response.status_code == 204
        venue.refresh_from_db()
        assert venue.is_active is False

    def test_non_owner_delete_forbidden(self, api_client: APIClient, venue: Venue) -> None:
        auth(api_client, make_user("intruder"))
        response = api_client.delete(_detail_url(venue.slug))
        assert response.status_code == 403


# ---------------------------------------------------------------------------
# Browse + filter
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestBrowseVenues:
    def _seed(self, owner: User) -> None:
        Venue.objects.create(
            owner=owner, name="Mumbai Hall", slug="mumbai-hall", city="Mumbai", country="India"
        )
        Venue.objects.create(
            owner=owner, name="Berlin Club", slug="berlin-club", city="Berlin", country="Germany"
        )
        inactive = Venue.objects.create(
            owner=owner, name="Closed", slug="closed", city="Mumbai", country="India"
        )
        inactive.is_active = False
        inactive.save(update_fields=["is_active"])

    def test_lists_active_only(self, api_client: APIClient, owner: User) -> None:
        self._seed(owner)
        response = api_client.get(VENUES_URL)
        names = {row["name"] for row in response.data["results"]}
        assert names == {"Mumbai Hall", "Berlin Club"}

    def test_filter_by_city(self, api_client: APIClient, owner: User) -> None:
        self._seed(owner)
        response = api_client.get(VENUES_URL, {"city": "mumbai"})
        names = {row["name"] for row in response.data["results"]}
        assert names == {"Mumbai Hall"}

    def test_filter_by_country(self, api_client: APIClient, owner: User) -> None:
        self._seed(owner)
        response = api_client.get(VENUES_URL, {"country": "Germany"})
        names = {row["name"] for row in response.data["results"]}
        assert names == {"Berlin Club"}
