"""
ContactRequest flow tests — send, list, accept, decline, retrieve + reveal.

Coverage: happy path for each endpoint plus negative paths
(unknown recipient, self-request, duplicate, non-party access, already-resolved).
"""

import pytest
from rest_framework.test import APIClient

from apps.connections.models import ContactRequest
from apps.users.models import User

REQUESTS_URL = "/api/connections/requests/"

PASSWORD = "StrongPass123!"


def _make_user(suffix: str) -> User:
    return User.objects.create_user(
        email=f"{suffix}@example.com",
        username=f"user-{suffix}",
        password=PASSWORD,
    )


def _auth(api_client: APIClient, user: User) -> APIClient:
    resp = api_client.post(
        "/api/auth/token/",
        {"email": user.email, "password": PASSWORD},
    )
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {resp.data['access']}")
    return api_client


def _detail_url(request_id: str) -> str:
    return f"{REQUESTS_URL}{request_id}/"


def _accept_url(request_id: str) -> str:
    return f"{REQUESTS_URL}{request_id}/accept/"


def _decline_url(request_id: str) -> str:
    return f"{REQUESTS_URL}{request_id}/decline/"


# ---------------------------------------------------------------------------
# Send
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSendContactRequest:
    def test_success_returns_201(self, api_client: APIClient) -> None:
        sender = _make_user("sender")
        _make_user("recipient")
        _auth(api_client, sender)
        response = api_client.post(
            REQUESTS_URL,
            {"recipient_username": "user-recipient", "message": "Let's jam"},
        )
        assert response.status_code == 201
        assert response.data["status"] == "pending"
        assert response.data["recipient_username"] == "user-recipient"
        assert response.data["contact_email"] is None  # not revealed while pending

    def test_unknown_recipient_returns_404(self, api_client: APIClient) -> None:
        _auth(api_client, _make_user("sender"))
        response = api_client.post(REQUESTS_URL, {"recipient_username": "ghost"})
        assert response.status_code == 404

    def test_self_request_returns_400(self, api_client: APIClient) -> None:
        sender = _make_user("sender")
        _auth(api_client, sender)
        response = api_client.post(REQUESTS_URL, {"recipient_username": "user-sender"})
        assert response.status_code == 400

    def test_duplicate_returns_409(self, api_client: APIClient) -> None:
        sender = _make_user("sender")
        _make_user("recipient")
        _auth(api_client, sender)
        body = {"recipient_username": "user-recipient"}
        assert api_client.post(REQUESTS_URL, body).status_code == 201
        assert api_client.post(REQUESTS_URL, body).status_code == 409

    def test_unauthenticated_returns_401(self, api_client: APIClient) -> None:
        response = api_client.post(REQUESTS_URL, {"recipient_username": "user-recipient"})
        assert response.status_code == 401


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestListContactRequests:
    def test_incoming_box_shows_received(self, api_client: APIClient) -> None:
        sender = _make_user("sender")
        recipient = _make_user("recipient")
        ContactRequest.objects.create(sender=sender, recipient=recipient)
        _auth(api_client, recipient)
        response = api_client.get(REQUESTS_URL)  # default box=incoming
        assert response.status_code == 200
        assert len(response.data["results"]) == 1

    def test_outgoing_box_shows_sent(self, api_client: APIClient) -> None:
        sender = _make_user("sender")
        recipient = _make_user("recipient")
        ContactRequest.objects.create(sender=sender, recipient=recipient)
        _auth(api_client, sender)
        incoming = api_client.get(REQUESTS_URL)
        outgoing = api_client.get(REQUESTS_URL, {"box": "outgoing"})
        assert len(incoming.data["results"]) == 0
        assert len(outgoing.data["results"]) == 1


# ---------------------------------------------------------------------------
# Accept / decline
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestResolveContactRequest:
    def test_recipient_accepts_and_reveals_contact(self, api_client: APIClient) -> None:
        sender = _make_user("sender")
        recipient = _make_user("recipient")
        cr = ContactRequest.objects.create(sender=sender, recipient=recipient)
        _auth(api_client, recipient)
        response = api_client.post(_accept_url(str(cr.id)))
        assert response.status_code == 200
        assert response.data["status"] == "accepted"
        # recipient sees the sender's email once accepted
        assert response.data["contact_email"] == sender.email

    def test_non_recipient_cannot_accept(self, api_client: APIClient) -> None:
        sender = _make_user("sender")
        recipient = _make_user("recipient")
        cr = ContactRequest.objects.create(sender=sender, recipient=recipient)
        _auth(api_client, sender)  # sender is not the recipient
        response = api_client.post(_accept_url(str(cr.id)))
        assert response.status_code == 404

    def test_accept_already_resolved_returns_409(self, api_client: APIClient) -> None:
        sender = _make_user("sender")
        recipient = _make_user("recipient")
        cr = ContactRequest.objects.create(
            sender=sender, recipient=recipient, status=ContactRequest.Status.DECLINED
        )
        _auth(api_client, recipient)
        response = api_client.post(_accept_url(str(cr.id)))
        assert response.status_code == 409

    def test_recipient_declines(self, api_client: APIClient) -> None:
        sender = _make_user("sender")
        recipient = _make_user("recipient")
        cr = ContactRequest.objects.create(sender=sender, recipient=recipient)
        _auth(api_client, recipient)
        response = api_client.post(_decline_url(str(cr.id)))
        assert response.status_code == 200
        assert response.data["status"] == "declined"
        assert response.data["contact_email"] is None  # no reveal on decline


# ---------------------------------------------------------------------------
# Retrieve
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestRetrieveContactRequest:
    def test_party_sees_request_email_hidden_until_accepted(self, api_client: APIClient) -> None:
        sender = _make_user("sender")
        recipient = _make_user("recipient")
        cr = ContactRequest.objects.create(sender=sender, recipient=recipient)
        _auth(api_client, sender)
        response = api_client.get(_detail_url(str(cr.id)))
        assert response.status_code == 200
        assert response.data["contact_email"] is None

    def test_sender_sees_recipient_email_after_accept(self, api_client: APIClient) -> None:
        sender = _make_user("sender")
        recipient = _make_user("recipient")
        cr = ContactRequest.objects.create(
            sender=sender, recipient=recipient, status=ContactRequest.Status.ACCEPTED
        )
        _auth(api_client, sender)
        response = api_client.get(_detail_url(str(cr.id)))
        assert response.data["contact_email"] == recipient.email

    def test_non_party_returns_404(self, api_client: APIClient) -> None:
        sender = _make_user("sender")
        recipient = _make_user("recipient")
        outsider = _make_user("outsider")
        cr = ContactRequest.objects.create(sender=sender, recipient=recipient)
        _auth(api_client, outsider)
        response = api_client.get(_detail_url(str(cr.id)))
        assert response.status_code == 404
