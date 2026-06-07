"""
Band CRUD + browse/filter tests.

Coverage: create (slug derivation + collision), retrieve (with roster), update,
soft-delete (happy + negatives: unauth, non-owner, missing, inactive), and browse
with city/country filters.
"""

import pytest
from rest_framework.test import APIClient

from apps.bands.models import Band, BandMembership
from apps.bands.tests.conftest import auth, make_user
from apps.users.models import User

BANDS_URL = "/api/bands/"


def _detail_url(slug: str) -> str:
    return f"{BANDS_URL}{slug}/"


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCreateBand:
    def test_success_returns_201_and_derives_slug(self, api_client: APIClient, owner: User) -> None:
        auth(api_client, owner)
        response = api_client.post(
            BANDS_URL, {"name": "The Night Owls", "city": "Pune"}, format="json"
        )
        assert response.status_code == 201
        assert response.data["slug"] == "the-night-owls"
        assert response.data["owner_username"] == owner.username

    def test_slug_collision_gets_suffix(self, api_client: APIClient, owner: User) -> None:
        Band.objects.create(owner=owner, name="Echo", slug="echo")
        auth(api_client, owner)
        response = api_client.post(BANDS_URL, {"name": "Echo"}, format="json")
        assert response.status_code == 201
        assert response.data["slug"] == "echo-2"

    def test_unauthenticated_rejected(self, api_client: APIClient, db: None) -> None:
        response = api_client.post(BANDS_URL, {"name": "Nope"}, format="json")
        assert response.status_code == 401

    def test_missing_name_rejected(self, api_client: APIClient, owner: User) -> None:
        auth(api_client, owner)
        response = api_client.post(BANDS_URL, {"city": "Pune"}, format="json")
        assert response.status_code == 400


# ---------------------------------------------------------------------------
# Retrieve
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestRetrieveBand:
    def test_public_retrieve_with_roster(self, api_client: APIClient, band: Band) -> None:
        member = make_user("member")
        BandMembership.objects.create(
            band=band, member=member, role="Drums", status=BandMembership.Status.ACCEPTED
        )
        # A pending invite should NOT appear in the public roster.
        pending = make_user("pending")
        BandMembership.objects.create(band=band, member=pending, role="Bass")

        response = api_client.get(_detail_url(band.slug))
        assert response.status_code == 200
        rosters = response.data["members"]
        assert len(rosters) == 1
        assert rosters[0]["member_username"] == member.username
        assert rosters[0]["role"] == "Drums"

    def test_missing_returns_404(self, api_client: APIClient, db: None) -> None:
        response = api_client.get(_detail_url("ghost-band"))
        assert response.status_code == 404

    def test_inactive_returns_404(self, api_client: APIClient, band: Band) -> None:
        band.is_active = False
        band.save(update_fields=["is_active"])
        response = api_client.get(_detail_url(band.slug))
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Update / delete
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestMutateBand:
    def test_owner_can_patch(self, api_client: APIClient, band: Band) -> None:
        auth(api_client, band.owner)
        response = api_client.patch(_detail_url(band.slug), {"bio": "Updated"}, format="json")
        assert response.status_code == 200
        assert response.data["bio"] == "Updated"

    def test_non_owner_patch_forbidden(self, api_client: APIClient, band: Band) -> None:
        auth(api_client, make_user("intruder"))
        response = api_client.patch(_detail_url(band.slug), {"bio": "Hijacked"}, format="json")
        assert response.status_code == 403

    def test_owner_soft_deletes(self, api_client: APIClient, band: Band) -> None:
        auth(api_client, band.owner)
        response = api_client.delete(_detail_url(band.slug))
        assert response.status_code == 204
        band.refresh_from_db()
        assert band.is_active is False

    def test_non_owner_delete_forbidden(self, api_client: APIClient, band: Band) -> None:
        auth(api_client, make_user("intruder"))
        response = api_client.delete(_detail_url(band.slug))
        assert response.status_code == 403


# ---------------------------------------------------------------------------
# Browse + filter
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestBrowseBands:
    def _seed(self, owner: User) -> None:
        Band.objects.create(
            owner=owner, name="Mumbai Funk", slug="mumbai-funk", city="Mumbai", country="India"
        )
        Band.objects.create(
            owner=owner, name="Berlin Beat", slug="berlin-beat", city="Berlin", country="Germany"
        )
        inactive = Band.objects.create(
            owner=owner, name="Defunct", slug="defunct", city="Mumbai", country="India"
        )
        inactive.is_active = False
        inactive.save(update_fields=["is_active"])

    def test_lists_active_only(self, api_client: APIClient, owner: User) -> None:
        self._seed(owner)
        response = api_client.get(BANDS_URL)
        names = {row["name"] for row in response.data["results"]}
        assert names == {"Mumbai Funk", "Berlin Beat"}

    def test_filter_by_city(self, api_client: APIClient, owner: User) -> None:
        self._seed(owner)
        response = api_client.get(BANDS_URL, {"city": "mumbai"})
        names = {row["name"] for row in response.data["results"]}
        assert names == {"Mumbai Funk"}

    def test_filter_by_country(self, api_client: APIClient, owner: User) -> None:
        self._seed(owner)
        response = api_client.get(BANDS_URL, {"country": "Germany"})
        names = {row["name"] for row in response.data["results"]}
        assert names == {"Berlin Beat"}
