"""
Engagement (hire-intent) flow tests — send, list, accept, decline, complete,
retrieve + reveal, and email notifications.

Notifications are Celery tasks emitted via transaction.on_commit, so those tests
wrap the request in django_capture_on_commit_callbacks(execute=True).
"""

from collections.abc import Callable
from typing import Any

import pytest
from django.core.mail import EmailMessage
from rest_framework.test import APIClient

from apps.engagements import services
from apps.engagements.models import EngagementRequest
from apps.engagements.tests.conftest import auth, make_user

ENGAGEMENTS_URL = "/api/engagements/"


def _accept_url(eid: str) -> str:
    return f"{ENGAGEMENTS_URL}{eid}/accept/"


def _decline_url(eid: str) -> str:
    return f"{ENGAGEMENTS_URL}{eid}/decline/"


def _complete_url(eid: str) -> str:
    return f"{ENGAGEMENTS_URL}{eid}/complete/"


# ---------------------------------------------------------------------------
# Send
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSend:
    def test_success_returns_201(self, api_client: APIClient, db: None) -> None:
        requester = make_user("requester")
        make_user("musician")
        auth(api_client, requester)
        response = api_client.post(
            ENGAGEMENTS_URL,
            {
                "musician_username": "user-musician",
                "message": "Need a bassist",
                "proposed_date": "2026-07-01",
                "rate_offer": "₹5000",
            },
            format="json",
        )
        assert response.status_code == 201
        assert response.data["status"] == "pending"
        assert response.data["musician_username"] == "user-musician"
        assert response.data["proposed_date"] == "2026-07-01"

    def test_unknown_musician_returns_404(self, api_client: APIClient, db: None) -> None:
        auth(api_client, make_user("requester"))
        response = api_client.post(ENGAGEMENTS_URL, {"musician_username": "nobody"}, format="json")
        assert response.status_code == 404

    def test_self_hire_rejected(self, api_client: APIClient, db: None) -> None:
        requester = make_user("requester")
        auth(api_client, requester)
        response = api_client.post(
            ENGAGEMENTS_URL, {"musician_username": "user-requester"}, format="json"
        )
        assert response.status_code == 400

    def test_unauthenticated_rejected(self, api_client: APIClient, db: None) -> None:
        response = api_client.post(ENGAGEMENTS_URL, {"musician_username": "x"}, format="json")
        assert response.status_code == 401

    def test_multiple_requests_to_same_musician_allowed(
        self, api_client: APIClient, db: None
    ) -> None:
        requester = make_user("requester")
        make_user("musician")
        auth(api_client, requester)
        first = api_client.post(
            ENGAGEMENTS_URL, {"musician_username": "user-musician"}, format="json"
        )
        second = api_client.post(
            ENGAGEMENTS_URL, {"musician_username": "user-musician"}, format="json"
        )
        assert first.status_code == 201
        assert second.status_code == 201


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestList:
    def test_incoming_shows_requests_to_me(self, api_client: APIClient, db: None) -> None:
        requester = make_user("requester")
        musician = make_user("musician")
        EngagementRequest.objects.create(requester=requester, musician=musician)
        auth(api_client, musician)
        response = api_client.get(ENGAGEMENTS_URL, {"box": "incoming"})
        assert response.status_code == 200
        assert len(response.data["results"]) == 1

    def test_outgoing_shows_my_requests(self, api_client: APIClient, db: None) -> None:
        requester = make_user("requester")
        musician = make_user("musician")
        EngagementRequest.objects.create(requester=requester, musician=musician)
        auth(api_client, requester)
        response = api_client.get(ENGAGEMENTS_URL, {"box": "outgoing"})
        assert response.status_code == 200
        assert len(response.data["results"]) == 1

    def test_detail_hidden_from_non_party(self, api_client: APIClient, db: None) -> None:
        requester = make_user("requester")
        musician = make_user("musician")
        engagement = EngagementRequest.objects.create(requester=requester, musician=musician)
        auth(api_client, make_user("outsider"))
        response = api_client.get(f"{ENGAGEMENTS_URL}{engagement.id}/")
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Accept / decline / complete + reveal
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestResolve:
    def test_musician_accepts_and_reveals_contact(self, api_client: APIClient, db: None) -> None:
        requester = make_user("requester")
        musician = make_user("musician")
        engagement = EngagementRequest.objects.create(requester=requester, musician=musician)
        auth(api_client, musician)
        response = api_client.post(_accept_url(str(engagement.id)))
        assert response.status_code == 200
        assert response.data["status"] == "accepted"
        assert response.data["contact_email"] == requester.email

    def test_requester_cannot_accept(self, api_client: APIClient, db: None) -> None:
        requester = make_user("requester")
        musician = make_user("musician")
        engagement = EngagementRequest.objects.create(requester=requester, musician=musician)
        # Requester is a party (can view) but only the musician may accept.
        auth(api_client, requester)
        response = api_client.post(_accept_url(str(engagement.id)))
        assert response.status_code == 404

    def test_decline(self, api_client: APIClient, db: None) -> None:
        requester = make_user("requester")
        musician = make_user("musician")
        engagement = EngagementRequest.objects.create(requester=requester, musician=musician)
        auth(api_client, musician)
        response = api_client.post(_decline_url(str(engagement.id)))
        assert response.status_code == 200
        assert response.data["status"] == "declined"
        assert response.data["contact_email"] is None

    def test_already_resolved_returns_409(self, api_client: APIClient, db: None) -> None:
        requester = make_user("requester")
        musician = make_user("musician")
        engagement = EngagementRequest.objects.create(
            requester=requester, musician=musician, status=EngagementRequest.Status.DECLINED
        )
        auth(api_client, musician)
        response = api_client.post(_accept_url(str(engagement.id)))
        assert response.status_code == 409

    def test_either_party_can_complete_accepted(self, api_client: APIClient, db: None) -> None:
        requester = make_user("requester")
        musician = make_user("musician")
        engagement = EngagementRequest.objects.create(
            requester=requester, musician=musician, status=EngagementRequest.Status.ACCEPTED
        )
        auth(api_client, requester)
        response = api_client.post(_complete_url(str(engagement.id)))
        assert response.status_code == 200
        assert response.data["status"] == "completed"

    def test_complete_requires_accepted(self, api_client: APIClient, db: None) -> None:
        requester = make_user("requester")
        musician = make_user("musician")
        engagement = EngagementRequest.objects.create(requester=requester, musician=musician)
        auth(api_client, musician)
        response = api_client.post(_complete_url(str(engagement.id)))
        assert response.status_code == 409


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestNotifications:
    def test_sending_emails_the_musician(
        self,
        api_client: APIClient,
        mailoutbox: list[EmailMessage],
        django_capture_on_commit_callbacks: Callable[..., Any],
    ) -> None:
        requester = make_user("requester")
        musician = make_user("musician")
        auth(api_client, requester)
        with django_capture_on_commit_callbacks(execute=True):
            response = api_client.post(
                ENGAGEMENTS_URL,
                {"musician_username": "user-musician", "rate_offer": "₹9000"},
                format="json",
            )
        assert response.status_code == 201
        assert len(mailoutbox) == 1
        assert mailoutbox[0].to == [musician.email]
        assert "₹9000" in mailoutbox[0].body

    def test_accepting_emails_requester_with_reveal(
        self,
        api_client: APIClient,
        mailoutbox: list[EmailMessage],
        django_capture_on_commit_callbacks: Callable[..., Any],
    ) -> None:
        requester = make_user("requester")
        musician = make_user("musician")
        engagement = EngagementRequest.objects.create(requester=requester, musician=musician)
        auth(api_client, musician)
        with django_capture_on_commit_callbacks(execute=True):
            response = api_client.post(_accept_url(str(engagement.id)))
        assert response.status_code == 200
        assert len(mailoutbox) == 1
        assert mailoutbox[0].to == [requester.email]
        assert musician.email in mailoutbox[0].body

    def test_declining_sends_no_email(
        self,
        api_client: APIClient,
        mailoutbox: list[EmailMessage],
        django_capture_on_commit_callbacks: Callable[..., Any],
    ) -> None:
        requester = make_user("requester")
        musician = make_user("musician")
        engagement = EngagementRequest.objects.create(requester=requester, musician=musician)
        auth(api_client, musician)
        with django_capture_on_commit_callbacks(execute=True):
            response = api_client.post(_decline_url(str(engagement.id)))
        assert response.status_code == 200
        assert len(mailoutbox) == 0

    def test_notify_musician_missing_request_is_noop(self, mailoutbox: list[EmailMessage]) -> None:
        services.notify_musician_of_request(engagement_id="00000000-0000-0000-0000-000000000000")
        assert len(mailoutbox) == 0

    def test_notify_requester_missing_request_is_noop(self, mailoutbox: list[EmailMessage]) -> None:
        services.notify_requester_of_acceptance(
            engagement_id="00000000-0000-0000-0000-000000000000"
        )
        assert len(mailoutbox) == 0
