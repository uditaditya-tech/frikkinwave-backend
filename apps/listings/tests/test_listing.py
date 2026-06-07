"""
Listing CRUD + browse/filter tests.

Coverage: post / retrieve / update / soft-delete (happy + negatives:
unauth, non-author, missing, inactive) and browse with type/city/country filters.
"""

import pytest
from rest_framework.test import APIClient

from apps.listings.models import Listing
from apps.listings.tests.conftest import PASSWORD, auth, make_user  # noqa: F401
from apps.users.models import User

LISTINGS_URL = "/api/listings/"


def _detail_url(listing_id: str) -> str:
    return f"{LISTINGS_URL}{listing_id}/"


def _valid_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "listing_type": "gig",
        "title": "Drummer for indie band",
        "description": "Looking for a drummer for an indie rock project.",
        "city": "Bengaluru",
        "country": "India",
        "is_paid": False,
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCreateListing:
    def test_success_returns_201(self, api_client: APIClient, author: User) -> None:
        auth(api_client, author)
        response = api_client.post(LISTINGS_URL, _valid_payload(), format="json")
        assert response.status_code == 201
        assert response.data["title"] == "Drummer for indie band"
        assert response.data["author_username"] == author.username
        assert response.data["is_active"] is True

    def test_unauthenticated_rejected(self, api_client: APIClient, db: None) -> None:
        response = api_client.post(LISTINGS_URL, _valid_payload(), format="json")
        assert response.status_code == 401

    def test_invalid_type_rejected(self, api_client: APIClient, author: User) -> None:
        auth(api_client, author)
        response = api_client.post(
            LISTINGS_URL, _valid_payload(listing_type="festival"), format="json"
        )
        assert response.status_code == 400

    def test_missing_title_rejected(self, api_client: APIClient, author: User) -> None:
        auth(api_client, author)
        payload = _valid_payload()
        del payload["title"]
        response = api_client.post(LISTINGS_URL, payload, format="json")
        assert response.status_code == 400


# ---------------------------------------------------------------------------
# Retrieve
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestRetrieveListing:
    def test_public_retrieve(self, api_client: APIClient, listing: Listing) -> None:
        response = api_client.get(_detail_url(str(listing.id)))
        assert response.status_code == 200
        assert response.data["title"] == listing.title

    def test_missing_returns_404(self, api_client: APIClient, db: None) -> None:
        response = api_client.get(_detail_url("01890000-0000-7000-8000-000000000000"))
        assert response.status_code == 404

    def test_inactive_returns_404(self, api_client: APIClient, listing: Listing) -> None:
        listing.is_active = False
        listing.save(update_fields=["is_active"])
        response = api_client.get(_detail_url(str(listing.id)))
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestUpdateListing:
    def test_author_can_patch(self, api_client: APIClient, listing: Listing) -> None:
        auth(api_client, listing.author)
        response = api_client.patch(
            _detail_url(str(listing.id)), {"title": "Updated title"}, format="json"
        )
        assert response.status_code == 200
        assert response.data["title"] == "Updated title"
        listing.refresh_from_db()
        assert listing.title == "Updated title"

    def test_non_author_forbidden(self, api_client: APIClient, listing: Listing) -> None:
        other = make_user("intruder")
        auth(api_client, other)
        response = api_client.patch(
            _detail_url(str(listing.id)), {"title": "Hijacked"}, format="json"
        )
        assert response.status_code == 403

    def test_unauthenticated_rejected(self, api_client: APIClient, listing: Listing) -> None:
        response = api_client.patch(_detail_url(str(listing.id)), {"title": "Nope"}, format="json")
        assert response.status_code == 401


# ---------------------------------------------------------------------------
# Delete (soft)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestDeleteListing:
    def test_author_soft_deletes(self, api_client: APIClient, listing: Listing) -> None:
        auth(api_client, listing.author)
        response = api_client.delete(_detail_url(str(listing.id)))
        assert response.status_code == 204
        listing.refresh_from_db()
        assert listing.is_active is False

    def test_non_author_forbidden(self, api_client: APIClient, listing: Listing) -> None:
        other = make_user("intruder")
        auth(api_client, other)
        response = api_client.delete(_detail_url(str(listing.id)))
        assert response.status_code == 403
        listing.refresh_from_db()
        assert listing.is_active is True


# ---------------------------------------------------------------------------
# Browse + filter
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestBrowseListings:
    def _seed(self, author: User) -> None:
        Listing.objects.create(
            author=author,
            listing_type=Listing.ListingType.GIG,
            title="Gig in Mumbai",
            description="x",
            city="Mumbai",
            country="India",
        )
        Listing.objects.create(
            author=author,
            listing_type=Listing.ListingType.AUDITION,
            title="Audition in Berlin",
            description="x",
            city="Berlin",
            country="Germany",
        )
        inactive = Listing.objects.create(
            author=author,
            listing_type=Listing.ListingType.VENUE,
            title="Closed venue",
            description="x",
            city="Mumbai",
            country="India",
        )
        inactive.is_active = False
        inactive.save(update_fields=["is_active"])

    def test_lists_active_only(self, api_client: APIClient, author: User) -> None:
        self._seed(author)
        response = api_client.get(LISTINGS_URL)
        assert response.status_code == 200
        titles = {row["title"] for row in response.data["results"]}
        assert titles == {"Gig in Mumbai", "Audition in Berlin"}

    def test_filter_by_type(self, api_client: APIClient, author: User) -> None:
        self._seed(author)
        response = api_client.get(LISTINGS_URL, {"type": "audition"})
        titles = {row["title"] for row in response.data["results"]}
        assert titles == {"Audition in Berlin"}

    def test_filter_by_city_case_insensitive(self, api_client: APIClient, author: User) -> None:
        self._seed(author)
        response = api_client.get(LISTINGS_URL, {"city": "mumbai"})
        titles = {row["title"] for row in response.data["results"]}
        assert titles == {"Gig in Mumbai"}

    def test_filter_by_country(self, api_client: APIClient, author: User) -> None:
        self._seed(author)
        response = api_client.get(LISTINGS_URL, {"country": "Germany"})
        titles = {row["title"] for row in response.data["results"]}
        assert titles == {"Audition in Berlin"}
