"""
Listing application flow tests — apply, list, accept, decline, retrieve + reveal,
and email notifications.

Notifications are Celery tasks emitted via transaction.on_commit, so those tests
wrap the request in django_capture_on_commit_callbacks(execute=True) to fire the
callbacks (the django fixture's outer transaction never commits on its own).
"""

from collections.abc import Callable
from typing import Any

import pytest
from django.core.mail import EmailMessage
from rest_framework.test import APIClient

from apps.listings import services
from apps.listings.models import Listing, ListingApplication
from apps.listings.tests.conftest import auth, make_user

LISTINGS_URL = "/api/listings/"
APPLICATIONS_URL = "/api/listings/applications/"


def _apply_url(listing_id: str) -> str:
    return f"{LISTINGS_URL}{listing_id}/apply/"


def _accept_url(application_id: str) -> str:
    return f"{APPLICATIONS_URL}{application_id}/accept/"


def _decline_url(application_id: str) -> str:
    return f"{APPLICATIONS_URL}{application_id}/decline/"


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestApply:
    def test_success_returns_201(self, api_client: APIClient, listing: Listing) -> None:
        applicant = make_user("applicant")
        auth(api_client, applicant)
        response = api_client.post(
            _apply_url(str(listing.id)), {"message": "I play funk bass"}, format="json"
        )
        assert response.status_code == 201
        assert response.data["status"] == "pending"
        assert response.data["applicant_username"] == applicant.username
        assert response.data["listing_title"] == listing.title

    def test_unauthenticated_rejected(self, api_client: APIClient, listing: Listing) -> None:
        response = api_client.post(_apply_url(str(listing.id)), {}, format="json")
        assert response.status_code == 401

    def test_self_application_rejected(self, api_client: APIClient, listing: Listing) -> None:
        auth(api_client, listing.author)
        response = api_client.post(_apply_url(str(listing.id)), {}, format="json")
        assert response.status_code == 400

    def test_duplicate_rejected(self, api_client: APIClient, listing: Listing) -> None:
        applicant = make_user("applicant")
        auth(api_client, applicant)
        first = api_client.post(_apply_url(str(listing.id)), {}, format="json")
        assert first.status_code == 201
        second = api_client.post(_apply_url(str(listing.id)), {}, format="json")
        assert second.status_code == 409

    def test_missing_listing_returns_404(self, api_client: APIClient, db: None) -> None:
        applicant = make_user("applicant")
        auth(api_client, applicant)
        response = api_client.post(
            _apply_url("01890000-0000-7000-8000-000000000000"), {}, format="json"
        )
        assert response.status_code == 404

    def test_inactive_listing_returns_404(self, api_client: APIClient, listing: Listing) -> None:
        listing.is_active = False
        listing.save(update_fields=["is_active"])
        applicant = make_user("applicant")
        auth(api_client, applicant)
        response = api_client.post(_apply_url(str(listing.id)), {}, format="json")
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestListApplications:
    def test_outgoing_shows_own_applications(self, api_client: APIClient, listing: Listing) -> None:
        applicant = make_user("applicant")
        ListingApplication.objects.create(listing=listing, applicant=applicant)
        auth(api_client, applicant)
        response = api_client.get(APPLICATIONS_URL, {"box": "outgoing"})
        assert response.status_code == 200
        assert len(response.data["results"]) == 1

    def test_incoming_shows_applications_to_my_listings(
        self, api_client: APIClient, listing: Listing
    ) -> None:
        applicant = make_user("applicant")
        ListingApplication.objects.create(listing=listing, applicant=applicant)
        auth(api_client, listing.author)
        response = api_client.get(APPLICATIONS_URL, {"box": "incoming"})
        assert response.status_code == 200
        assert len(response.data["results"]) == 1

    def test_incoming_empty_for_applicant(self, api_client: APIClient, listing: Listing) -> None:
        applicant = make_user("applicant")
        ListingApplication.objects.create(listing=listing, applicant=applicant)
        auth(api_client, applicant)
        response = api_client.get(APPLICATIONS_URL, {"box": "incoming"})
        assert response.status_code == 200
        assert len(response.data["results"]) == 0


# ---------------------------------------------------------------------------
# Accept / decline + reveal
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestResolveApplication:
    def test_author_accepts_and_reveals_contact(
        self, api_client: APIClient, listing: Listing
    ) -> None:
        applicant = make_user("applicant")
        application = ListingApplication.objects.create(listing=listing, applicant=applicant)
        auth(api_client, listing.author)
        response = api_client.post(_accept_url(str(application.id)))
        assert response.status_code == 200
        assert response.data["status"] == "accepted"
        # Author sees the applicant's email once accepted.
        assert response.data["contact_email"] == applicant.email

    def test_author_declines(self, api_client: APIClient, listing: Listing) -> None:
        applicant = make_user("applicant")
        application = ListingApplication.objects.create(listing=listing, applicant=applicant)
        auth(api_client, listing.author)
        response = api_client.post(_decline_url(str(application.id)))
        assert response.status_code == 200
        assert response.data["status"] == "declined"
        assert response.data["contact_email"] is None

    def test_non_author_cannot_resolve(self, api_client: APIClient, listing: Listing) -> None:
        applicant = make_user("applicant")
        application = ListingApplication.objects.create(listing=listing, applicant=applicant)
        # The applicant is a party but not the author — cannot accept.
        auth(api_client, applicant)
        response = api_client.post(_accept_url(str(application.id)))
        assert response.status_code == 404

    def test_already_resolved_returns_409(self, api_client: APIClient, listing: Listing) -> None:
        applicant = make_user("applicant")
        application = ListingApplication.objects.create(
            listing=listing, applicant=applicant, status=ListingApplication.Status.ACCEPTED
        )
        auth(api_client, listing.author)
        response = api_client.post(_decline_url(str(application.id)))
        assert response.status_code == 409

    def test_applicant_sees_reveal_after_accept(
        self, api_client: APIClient, listing: Listing
    ) -> None:
        applicant = make_user("applicant")
        application = ListingApplication.objects.create(
            listing=listing, applicant=applicant, status=ListingApplication.Status.ACCEPTED
        )
        auth(api_client, applicant)
        response = api_client.get(f"{APPLICATIONS_URL}{application.id}/")
        assert response.status_code == 200
        # Applicant sees the author's email once accepted.
        assert response.data["contact_email"] == listing.author.email

    def test_detail_hidden_from_non_party(self, api_client: APIClient, listing: Listing) -> None:
        applicant = make_user("applicant")
        application = ListingApplication.objects.create(listing=listing, applicant=applicant)
        outsider = make_user("outsider")
        auth(api_client, outsider)
        response = api_client.get(f"{APPLICATIONS_URL}{application.id}/")
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestApplicationNotifications:
    def test_applying_emails_the_author(
        self,
        api_client: APIClient,
        listing: Listing,
        mailoutbox: list[EmailMessage],
        django_capture_on_commit_callbacks: Callable[..., Any],
    ) -> None:
        applicant = make_user("applicant")
        auth(api_client, applicant)
        with django_capture_on_commit_callbacks(execute=True):
            response = api_client.post(
                _apply_url(str(listing.id)), {"message": "Pick me"}, format="json"
            )
        assert response.status_code == 201
        assert len(mailoutbox) == 1
        email = mailoutbox[0]
        assert email.to == [listing.author.email]
        assert "Pick me" in email.body

    def test_accepting_emails_applicant_with_reveal(
        self,
        api_client: APIClient,
        listing: Listing,
        mailoutbox: list[EmailMessage],
        django_capture_on_commit_callbacks: Callable[..., Any],
    ) -> None:
        applicant = make_user("applicant")
        application = ListingApplication.objects.create(listing=listing, applicant=applicant)
        auth(api_client, listing.author)
        with django_capture_on_commit_callbacks(execute=True):
            response = api_client.post(_accept_url(str(application.id)))
        assert response.status_code == 200
        assert len(mailoutbox) == 1
        email = mailoutbox[0]
        assert email.to == [applicant.email]
        assert listing.author.email in email.body

    def test_declining_sends_no_email(
        self,
        api_client: APIClient,
        listing: Listing,
        mailoutbox: list[EmailMessage],
        django_capture_on_commit_callbacks: Callable[..., Any],
    ) -> None:
        applicant = make_user("applicant")
        application = ListingApplication.objects.create(listing=listing, applicant=applicant)
        auth(api_client, listing.author)
        with django_capture_on_commit_callbacks(execute=True):
            response = api_client.post(_decline_url(str(application.id)))
        assert response.status_code == 200
        assert len(mailoutbox) == 0

    def test_notify_author_missing_application_is_noop(
        self, mailoutbox: list[EmailMessage]
    ) -> None:
        services.notify_author_of_application(application_id="00000000-0000-0000-0000-000000000000")
        assert len(mailoutbox) == 0

    def test_notify_applicant_missing_application_is_noop(
        self, mailoutbox: list[EmailMessage]
    ) -> None:
        services.notify_applicant_of_acceptance(
            application_id="00000000-0000-0000-0000-000000000000"
        )
        assert len(mailoutbox) == 0
