"""
Tests for the activity feed (fan-out-on-write).

Activities are recorded by producing apps' services and fanned out post-commit
via Celery (eager under pytest). The db fixture's outer transaction never
commits, so each producing action runs inside
`django_capture_on_commit_callbacks(execute=True)` to fire the on_commit
fan-out — mirroring the CLAUDE.md Celery test pattern.
"""

from collections.abc import Callable
from typing import Any

import pytest
from rest_framework.test import APIClient

from apps.bands.services import create_band
from apps.listings.services import create_listing
from apps.social.models import Activity, FeedEntry, Follow
from apps.social.tests.conftest import auth
from apps.users.models import User


def _post_listing(capture: Callable[..., Any], author: User, title: str = "Drummer wanted") -> Any:
    with capture(execute=True):
        return create_listing(
            author=author,
            listing_type="gig",
            title=title,
            description="A gig.",
            city="Mumbai",
            country="India",
        )


@pytest.mark.django_db
class TestFanOut:
    def test_followed_users_activity_lands_in_feed(
        self,
        api_client: APIClient,
        alice: User,
        bob: User,
        django_capture_on_commit_callbacks: Callable[..., Any],
    ) -> None:
        # alice follows bob, then bob posts a listing.
        Follow.objects.create(follower=alice, followed=bob)
        _post_listing(django_capture_on_commit_callbacks, bob, title="Bassist wanted")

        client = auth(api_client, alice)
        resp = client.get("/api/social/feed/")
        assert resp.status_code == 200
        summaries = [row["summary"] for row in resp.data["results"]]
        assert "Bassist wanted" in summaries
        row = next(r for r in resp.data["results"] if r["summary"] == "Bassist wanted")
        assert row["verb"] == "posted_listing"
        assert row["actor_username"] == bob.username
        assert row["target_type"] == "listing"

    def test_unfollowed_users_activity_absent(
        self,
        api_client: APIClient,
        alice: User,
        bob: User,
        django_capture_on_commit_callbacks: Callable[..., Any],
    ) -> None:
        # alice does NOT follow bob.
        _post_listing(django_capture_on_commit_callbacks, bob)
        client = auth(api_client, alice)
        resp = client.get("/api/social/feed/")
        assert resp.data["results"] == []

    def test_actor_sees_own_activity(
        self,
        api_client: APIClient,
        alice: User,
        django_capture_on_commit_callbacks: Callable[..., Any],
    ) -> None:
        _post_listing(django_capture_on_commit_callbacks, alice)
        client = auth(api_client, alice)
        resp = client.get("/api/social/feed/")
        assert len(resp.data["results"]) == 1
        assert resp.data["results"][0]["actor_username"] == alice.username

    def test_band_creation_appears(
        self,
        api_client: APIClient,
        alice: User,
        bob: User,
        django_capture_on_commit_callbacks: Callable[..., Any],
    ) -> None:
        Follow.objects.create(follower=alice, followed=bob)
        with django_capture_on_commit_callbacks(execute=True):
            create_band(owner=bob, name="The Riffs", city="Pune", country="India")

        client = auth(api_client, alice)
        resp = client.get("/api/social/feed/")
        row = resp.data["results"][0]
        assert row["verb"] == "created_band"
        assert row["summary"] == "The Riffs"
        assert row["target_type"] == "band"
        assert row["target_slug"] == "the-riffs"

    def test_feed_newest_first(
        self,
        api_client: APIClient,
        alice: User,
        bob: User,
        django_capture_on_commit_callbacks: Callable[..., Any],
    ) -> None:
        Follow.objects.create(follower=alice, followed=bob)
        _post_listing(django_capture_on_commit_callbacks, bob, title="First")
        _post_listing(django_capture_on_commit_callbacks, bob, title="Second")
        client = auth(api_client, alice)
        resp = client.get("/api/social/feed/")
        summaries = [row["summary"] for row in resp.data["results"]]
        assert summaries == ["Second", "First"]

    def test_feed_requires_auth(self, api_client: APIClient) -> None:
        assert api_client.get("/api/social/feed/").status_code == 401


@pytest.mark.django_db
class TestBackfillAndPrune:
    def test_following_backfills_past_activity(
        self,
        api_client: APIClient,
        alice: User,
        bob: User,
        django_capture_on_commit_callbacks: Callable[..., Any],
    ) -> None:
        # bob posts BEFORE alice follows — backfill must surface it.
        _post_listing(django_capture_on_commit_callbacks, bob, title="Earlier gig")
        with django_capture_on_commit_callbacks(execute=True):
            api_client_authed = auth(api_client, alice)
            api_client_authed.post(f"/api/social/follow/{bob.username}/")

        resp = api_client_authed.get("/api/social/feed/")
        summaries = [row["summary"] for row in resp.data["results"]]
        assert "Earlier gig" in summaries

    def test_unfollow_prunes_feed(
        self,
        api_client: APIClient,
        alice: User,
        bob: User,
        django_capture_on_commit_callbacks: Callable[..., Any],
    ) -> None:
        Follow.objects.create(follower=alice, followed=bob)
        _post_listing(django_capture_on_commit_callbacks, bob, title="Soon gone")
        client = auth(api_client, alice)
        assert client.get("/api/social/feed/").data["results"]  # populated

        with django_capture_on_commit_callbacks(execute=True):
            client.delete(f"/api/social/follow/{bob.username}/")

        resp = client.get("/api/social/feed/")
        assert resp.data["results"] == []
        # bob's own inbox copy survives the prune (only alice's was removed).
        assert FeedEntry.objects.filter(owner=bob).exists()


@pytest.mark.django_db
class TestRecording:
    def test_activity_row_created_once(
        self,
        alice: User,
        bob: User,
        django_capture_on_commit_callbacks: Callable[..., Any],
    ) -> None:
        Follow.objects.create(follower=bob, followed=alice)
        _post_listing(django_capture_on_commit_callbacks, alice)
        # One canonical Activity, fanned to follower (bob) + actor (alice).
        assert Activity.objects.count() == 1
        assert FeedEntry.objects.count() == 2

    def test_rolled_back_action_records_nothing(
        self,
        alice: User,
    ) -> None:
        # No capture context → the on_commit fan-out never fires (the action's
        # transaction never commits), so no phantom activity is recorded.
        create_listing(
            author=alice,
            listing_type="gig",
            title="Phantom",
            description="x",
            city="Mumbai",
            country="India",
        )
        assert Activity.objects.count() == 0
        assert FeedEntry.objects.count() == 0
