"""
Band membership / invite flow tests — invite, list, accept, decline, retrieve +
reveal, and email notifications.

Notifications are Celery tasks emitted via transaction.on_commit, so those tests
wrap the request in django_capture_on_commit_callbacks(execute=True).
"""

from collections.abc import Callable
from typing import Any

import pytest
from django.core.mail import EmailMessage
from rest_framework.test import APIClient

from apps.bands import services
from apps.bands.models import Band, BandMembership
from apps.bands.tests.conftest import auth, make_user

BANDS_URL = "/api/bands/"
MEMBERSHIPS_URL = "/api/bands/memberships/"


def _invite_url(slug: str) -> str:
    return f"{BANDS_URL}{slug}/invite/"


def _accept_url(mid: str) -> str:
    return f"{MEMBERSHIPS_URL}{mid}/accept/"


def _decline_url(mid: str) -> str:
    return f"{MEMBERSHIPS_URL}{mid}/decline/"


# ---------------------------------------------------------------------------
# Invite
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestInvite:
    def test_owner_invites_returns_201(self, api_client: APIClient, band: Band) -> None:
        make_user("member")
        auth(api_client, band.owner)
        response = api_client.post(
            _invite_url(band.slug),
            {"member_username": "user-member", "role": "Keys"},
            format="json",
        )
        assert response.status_code == 201
        assert response.data["status"] == "pending"
        assert response.data["member_username"] == "user-member"
        assert response.data["role"] == "Keys"

    def test_non_owner_cannot_invite(self, api_client: APIClient, band: Band) -> None:
        make_user("member")
        auth(api_client, make_user("intruder"))
        response = api_client.post(
            _invite_url(band.slug), {"member_username": "user-member"}, format="json"
        )
        assert response.status_code == 403

    def test_unknown_user_returns_404(self, api_client: APIClient, band: Band) -> None:
        auth(api_client, band.owner)
        response = api_client.post(
            _invite_url(band.slug), {"member_username": "nobody"}, format="json"
        )
        assert response.status_code == 404

    def test_owner_cannot_invite_self(self, api_client: APIClient, band: Band) -> None:
        auth(api_client, band.owner)
        response = api_client.post(
            _invite_url(band.slug), {"member_username": band.owner.username}, format="json"
        )
        assert response.status_code == 400

    def test_duplicate_invite_returns_409(self, api_client: APIClient, band: Band) -> None:
        member = make_user("member")
        BandMembership.objects.create(band=band, member=member)
        auth(api_client, band.owner)
        response = api_client.post(
            _invite_url(band.slug), {"member_username": "user-member"}, format="json"
        )
        assert response.status_code == 409

    def test_unauthenticated_rejected(self, api_client: APIClient, band: Band) -> None:
        response = api_client.post(_invite_url(band.slug), {"member_username": "x"}, format="json")
        assert response.status_code == 401


# ---------------------------------------------------------------------------
# List / retrieve
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestListMemberships:
    def test_lists_own_invites(self, api_client: APIClient, band: Band) -> None:
        member = make_user("member")
        BandMembership.objects.create(band=band, member=member)
        auth(api_client, member)
        response = api_client.get(MEMBERSHIPS_URL)
        assert response.status_code == 200
        assert len(response.data["results"]) == 1
        assert response.data["results"][0]["band_slug"] == band.slug

    def test_detail_hidden_from_non_party(self, api_client: APIClient, band: Band) -> None:
        member = make_user("member")
        membership = BandMembership.objects.create(band=band, member=member)
        auth(api_client, make_user("outsider"))
        response = api_client.get(f"{MEMBERSHIPS_URL}{membership.id}/")
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Accept / decline + reveal
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestResolveMembership:
    def test_member_accepts_and_sees_owner_email(self, api_client: APIClient, band: Band) -> None:
        member = make_user("member")
        membership = BandMembership.objects.create(band=band, member=member)
        auth(api_client, member)
        response = api_client.post(_accept_url(str(membership.id)))
        assert response.status_code == 200
        assert response.data["status"] == "accepted"
        assert response.data["contact_email"] == band.owner.email

    def test_member_declines(self, api_client: APIClient, band: Band) -> None:
        member = make_user("member")
        membership = BandMembership.objects.create(band=band, member=member)
        auth(api_client, member)
        response = api_client.post(_decline_url(str(membership.id)))
        assert response.status_code == 200
        assert response.data["status"] == "declined"
        assert response.data["contact_email"] is None

    def test_owner_cannot_accept_on_behalf(self, api_client: APIClient, band: Band) -> None:
        member = make_user("member")
        membership = BandMembership.objects.create(band=band, member=member)
        # Owner is a party (can view) but only the member may accept.
        auth(api_client, band.owner)
        response = api_client.post(_accept_url(str(membership.id)))
        assert response.status_code == 404

    def test_already_resolved_returns_409(self, api_client: APIClient, band: Band) -> None:
        member = make_user("member")
        membership = BandMembership.objects.create(
            band=band, member=member, status=BandMembership.Status.ACCEPTED
        )
        auth(api_client, member)
        response = api_client.post(_decline_url(str(membership.id)))
        assert response.status_code == 409


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestMembershipNotifications:
    def test_inviting_emails_the_member(
        self,
        api_client: APIClient,
        band: Band,
        mailoutbox: list[EmailMessage],
        django_capture_on_commit_callbacks: Callable[..., Any],
    ) -> None:
        member = make_user("member")
        auth(api_client, band.owner)
        with django_capture_on_commit_callbacks(execute=True):
            response = api_client.post(
                _invite_url(band.slug),
                {"member_username": "user-member", "role": "Guitar"},
                format="json",
            )
        assert response.status_code == 201
        assert len(mailoutbox) == 1
        assert mailoutbox[0].to == [member.email]
        assert "Guitar" in mailoutbox[0].body

    def test_accepting_emails_the_owner(
        self,
        api_client: APIClient,
        band: Band,
        mailoutbox: list[EmailMessage],
        django_capture_on_commit_callbacks: Callable[..., Any],
    ) -> None:
        member = make_user("member")
        membership = BandMembership.objects.create(band=band, member=member)
        auth(api_client, member)
        with django_capture_on_commit_callbacks(execute=True):
            response = api_client.post(_accept_url(str(membership.id)))
        assert response.status_code == 200
        assert len(mailoutbox) == 1
        assert mailoutbox[0].to == [band.owner.email]

    def test_declining_sends_no_email(
        self,
        api_client: APIClient,
        band: Band,
        mailoutbox: list[EmailMessage],
        django_capture_on_commit_callbacks: Callable[..., Any],
    ) -> None:
        member = make_user("member")
        membership = BandMembership.objects.create(band=band, member=member)
        auth(api_client, member)
        with django_capture_on_commit_callbacks(execute=True):
            response = api_client.post(_decline_url(str(membership.id)))
        assert response.status_code == 200
        assert len(mailoutbox) == 0

    def test_notify_invite_missing_membership_is_noop(self, mailoutbox: list[EmailMessage]) -> None:
        services.notify_member_of_invite(membership_id="00000000-0000-0000-0000-000000000000")
        assert len(mailoutbox) == 0

    def test_notify_accept_missing_membership_is_noop(self, mailoutbox: list[EmailMessage]) -> None:
        services.notify_owner_of_acceptance(membership_id="00000000-0000-0000-0000-000000000000")
        assert len(mailoutbox) == 0
