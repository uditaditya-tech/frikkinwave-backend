"""Tests for the follow graph — follow/unfollow + follower/following lists."""

import pytest
from rest_framework.test import APIClient

from apps.social.models import Follow
from apps.social.tests.conftest import auth, make_user
from apps.users.models import User


@pytest.mark.django_db
class TestFollow:
    def test_follow_creates_edge(self, api_client: APIClient, alice: User, bob: User) -> None:
        client = auth(api_client, alice)
        resp = client.post(f"/api/social/follow/{bob.username}/")
        assert resp.status_code == 201
        assert resp.data["username"] == bob.username
        assert Follow.objects.filter(follower=alice, followed=bob).exists()

    def test_refollow_is_idempotent(self, api_client: APIClient, alice: User, bob: User) -> None:
        client = auth(api_client, alice)
        first = client.post(f"/api/social/follow/{bob.username}/")
        second = client.post(f"/api/social/follow/{bob.username}/")
        assert first.status_code == 201
        assert second.status_code == 200  # repeat follow is a no-op success
        assert Follow.objects.filter(follower=alice, followed=bob).count() == 1

    def test_cannot_follow_self(self, api_client: APIClient, alice: User) -> None:
        client = auth(api_client, alice)
        resp = client.post(f"/api/social/follow/{alice.username}/")
        assert resp.status_code == 400
        assert not Follow.objects.filter(follower=alice).exists()

    def test_follow_unknown_user_404(self, api_client: APIClient, alice: User) -> None:
        client = auth(api_client, alice)
        resp = client.post("/api/social/follow/nobody/")
        assert resp.status_code == 404

    def test_follow_requires_auth(self, api_client: APIClient, bob: User) -> None:
        resp = api_client.post(f"/api/social/follow/{bob.username}/")
        assert resp.status_code == 401


@pytest.mark.django_db
class TestUnfollow:
    def test_unfollow_removes_edge(self, api_client: APIClient, alice: User, bob: User) -> None:
        Follow.objects.create(follower=alice, followed=bob)
        client = auth(api_client, alice)
        resp = client.delete(f"/api/social/follow/{bob.username}/")
        assert resp.status_code == 204
        assert not Follow.objects.filter(follower=alice, followed=bob).exists()

    def test_unfollow_when_not_following_is_204(
        self, api_client: APIClient, alice: User, bob: User
    ) -> None:
        client = auth(api_client, alice)
        resp = client.delete(f"/api/social/follow/{bob.username}/")
        assert resp.status_code == 204  # idempotent

    def test_unfollow_unknown_user_404(self, api_client: APIClient, alice: User) -> None:
        client = auth(api_client, alice)
        resp = client.delete("/api/social/follow/nobody/")
        assert resp.status_code == 404


@pytest.mark.django_db
class TestLists:
    def test_following_list(self, api_client: APIClient, alice: User, bob: User) -> None:
        carol = make_user("carol")
        Follow.objects.create(follower=alice, followed=bob)
        Follow.objects.create(follower=alice, followed=carol)
        client = auth(api_client, alice)
        resp = client.get("/api/social/following/")
        assert resp.status_code == 200
        usernames = {row["username"] for row in resp.data["results"]}
        assert usernames == {bob.username, carol.username}

    def test_followers_list(self, api_client: APIClient, alice: User, bob: User) -> None:
        carol = make_user("carol")
        Follow.objects.create(follower=bob, followed=alice)
        Follow.objects.create(follower=carol, followed=alice)
        client = auth(api_client, alice)
        resp = client.get("/api/social/followers/")
        assert resp.status_code == 200
        usernames = {row["username"] for row in resp.data["results"]}
        assert usernames == {bob.username, carol.username}

    def test_lists_require_auth(self, api_client: APIClient) -> None:
        assert api_client.get("/api/social/following/").status_code == 401
        assert api_client.get("/api/social/followers/").status_code == 401


@pytest.mark.django_db
class TestPublicLists:
    def test_public_followers_no_auth(self, api_client: APIClient, alice: User, bob: User) -> None:
        Follow.objects.create(follower=bob, followed=alice)
        resp = api_client.get(f"/api/social/{alice.username}/followers/")
        assert resp.status_code == 200
        usernames = {row["username"] for row in resp.data["results"]}
        assert usernames == {bob.username}

    def test_public_following_no_auth(self, api_client: APIClient, alice: User, bob: User) -> None:
        Follow.objects.create(follower=alice, followed=bob)
        resp = api_client.get(f"/api/social/{alice.username}/following/")
        assert resp.status_code == 200
        usernames = {row["username"] for row in resp.data["results"]}
        assert usernames == {bob.username}

    def test_public_list_unknown_user_404(self, api_client: APIClient) -> None:
        assert api_client.get("/api/social/nobody/followers/").status_code == 404
        assert api_client.get("/api/social/nobody/following/").status_code == 404
