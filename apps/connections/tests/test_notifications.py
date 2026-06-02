"""
Contact-request email notification tests (Phase 2.2).

Notifications are Celery tasks emitted via transaction.on_commit, so the tests
wrap the request in django_capture_on_commit_callbacks(execute=True) to fire
those callbacks (the django fixture's outer transaction never commits on its
own). Celery runs eager in tests, and pytest-django swaps in the locmem email
backend, so sends land in the `mailoutbox` fixture.
"""

from collections.abc import Callable
from typing import Any

import pytest
from django.core.mail import EmailMessage
from rest_framework.test import APIClient

from apps.connections import services
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
    resp = api_client.post("/api/auth/token/", {"email": user.email, "password": PASSWORD})
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {resp.data['access']}")
    return api_client


@pytest.mark.django_db
class TestSendNotification:
    def test_sending_request_emails_the_recipient(
        self,
        api_client: APIClient,
        mailoutbox: list[EmailMessage],
        django_capture_on_commit_callbacks: Callable[..., Any],
    ) -> None:
        sender = _make_user("sender")
        _make_user("recipient")
        _auth(api_client, sender)

        with django_capture_on_commit_callbacks(execute=True):
            response = api_client.post(
                REQUESTS_URL,
                {"recipient_username": "user-recipient", "message": "Let's jam"},
            )

        assert response.status_code == 201
        assert len(mailoutbox) == 1
        email = mailoutbox[0]
        assert email.to == ["recipient@example.com"]
        assert "user-sender" in email.subject
        assert "Let's jam" in email.body


@pytest.mark.django_db
class TestAcceptNotification:
    def test_accepting_emails_sender_with_revealed_contact(
        self,
        api_client: APIClient,
        mailoutbox: list[EmailMessage],
        django_capture_on_commit_callbacks: Callable[..., Any],
    ) -> None:
        sender = _make_user("sender")
        recipient = _make_user("recipient")
        request = ContactRequest.objects.create(sender=sender, recipient=recipient)

        _auth(api_client, recipient)
        with django_capture_on_commit_callbacks(execute=True):
            response = api_client.post(f"{REQUESTS_URL}{request.id}/accept/")

        assert response.status_code == 200
        assert len(mailoutbox) == 1
        email = mailoutbox[0]
        assert email.to == ["sender@example.com"]
        # The recipient's contact email is revealed to the sender on accept.
        assert "recipient@example.com" in email.body

    def test_declining_sends_no_email(
        self,
        api_client: APIClient,
        mailoutbox: list[EmailMessage],
        django_capture_on_commit_callbacks: Callable[..., Any],
    ) -> None:
        sender = _make_user("sender")
        recipient = _make_user("recipient")
        request = ContactRequest.objects.create(sender=sender, recipient=recipient)

        _auth(api_client, recipient)
        with django_capture_on_commit_callbacks(execute=True):
            response = api_client.post(f"{REQUESTS_URL}{request.id}/decline/")

        assert response.status_code == 200
        assert len(mailoutbox) == 0


@pytest.mark.django_db
class TestNotificationQueueHygiene:
    """A task firing for a request that no longer exists must not blow up."""

    def test_notify_recipient_missing_request_is_noop(self, mailoutbox: list[EmailMessage]) -> None:
        services.notify_recipient_of_request(request_id="00000000-0000-0000-0000-000000000000")
        assert len(mailoutbox) == 0

    def test_notify_sender_missing_request_is_noop(self, mailoutbox: list[EmailMessage]) -> None:
        services.notify_sender_of_acceptance(request_id="00000000-0000-0000-0000-000000000000")
        assert len(mailoutbox) == 0
