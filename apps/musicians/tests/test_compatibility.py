"""
Compatibility blurb tests (Phase 2.6).

The OpenAI client is mocked (counts complete() calls, returns a fixed blurb) so
there's no network or API key. Caching is per canonical unordered pair, so the
blurb is generated once and reused from either direction.
"""

import pytest
from pytest_django.fixtures import SettingsWrapper
from rest_framework.test import APIClient

from apps.musicians import services
from apps.musicians.models import CompatibilityBlurb, MusicianProfile
from apps.users.models import User

PASSWORD = "StrongPass123!"


def _url(username: str) -> str:
    return f"/api/musicians/compatibility/{username}/"


def _make_user_with_profile(suffix: str) -> User:
    user = User.objects.create_user(
        email=f"{suffix}@example.com", username=f"user-{suffix}", password=PASSWORD
    )
    MusicianProfile.objects.create(user=user, bio=f"bio {suffix}", city="Mumbai")
    return user


def _auth(api_client: APIClient, user: User) -> APIClient:
    resp = api_client.post("/api/auth/token/", {"email": user.email, "password": PASSWORD})
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {resp.data['access']}")
    return api_client


class FakeOpenAIClient:
    def __init__(self) -> None:
        self.complete_calls: list[str] = []

    def complete(self, prompt: str) -> str:
        self.complete_calls.append(prompt)
        return "You both love jazz in Mumbai — you'd click instantly."


@pytest.fixture
def fake_openai(monkeypatch: pytest.MonkeyPatch, settings: SettingsWrapper) -> FakeOpenAIClient:
    settings.OPENAI_API_KEY = "test-key"
    client = FakeOpenAIClient()
    monkeypatch.setattr(services, "get_openai_client", lambda: client)
    return client


@pytest.mark.django_db
class TestCompatibility:
    def test_generates_and_returns_blurb(
        self, api_client: APIClient, fake_openai: FakeOpenAIClient
    ) -> None:
        viewer = _make_user_with_profile("viewer")
        _make_user_with_profile("target")
        _auth(api_client, viewer)

        response = api_client.get(_url("user-target"))

        assert response.status_code == 200
        assert response.data["with"] == "user-target"
        assert "jazz" in response.data["blurb"]
        assert len(fake_openai.complete_calls) == 1
        assert CompatibilityBlurb.objects.count() == 1

    def test_second_request_is_cached(
        self, api_client: APIClient, fake_openai: FakeOpenAIClient
    ) -> None:
        viewer = _make_user_with_profile("viewer")
        _make_user_with_profile("target")
        _auth(api_client, viewer)

        api_client.get(_url("user-target"))
        api_client.get(_url("user-target"))

        assert len(fake_openai.complete_calls) == 1  # generated once, then cached
        assert CompatibilityBlurb.objects.count() == 1

    def test_reverse_direction_hits_same_cache(
        self, api_client: APIClient, fake_openai: FakeOpenAIClient
    ) -> None:
        viewer = _make_user_with_profile("viewer")
        target = _make_user_with_profile("target")

        _auth(api_client, viewer)
        api_client.get(_url("user-target"))

        # Target now views viewer — same canonical pair, no new generation.
        api_client.credentials()
        _auth(api_client, target)
        response = api_client.get(_url("user-viewer"))

        assert response.status_code == 200
        assert len(fake_openai.complete_calls) == 1
        assert CompatibilityBlurb.objects.count() == 1

    def test_self_returns_400(self, api_client: APIClient, fake_openai: FakeOpenAIClient) -> None:
        viewer = _make_user_with_profile("viewer")
        _auth(api_client, viewer)
        assert api_client.get(_url("user-viewer")).status_code == 400

    def test_unknown_target_returns_404(
        self, api_client: APIClient, fake_openai: FakeOpenAIClient
    ) -> None:
        viewer = _make_user_with_profile("viewer")
        _auth(api_client, viewer)
        assert api_client.get(_url("ghost")).status_code == 404

    def test_viewer_without_profile_returns_400(
        self, api_client: APIClient, fake_openai: FakeOpenAIClient
    ) -> None:
        viewer = User.objects.create_user(
            email="noprofile@example.com", username="user-noprofile", password=PASSWORD
        )
        _make_user_with_profile("target")
        _auth(api_client, viewer)
        assert api_client.get(_url("user-target")).status_code == 400

    def test_unauthenticated_returns_401(self, api_client: APIClient) -> None:
        _make_user_with_profile("target")
        assert api_client.get(_url("user-target")).status_code == 401

    def test_openai_error_returns_503(
        self, api_client: APIClient, monkeypatch: pytest.MonkeyPatch, settings: SettingsWrapper
    ) -> None:
        from apps.musicians.openai_client import OpenAIUnavailableError

        settings.OPENAI_API_KEY = "test-key"

        class FailingClient:
            def complete(self, prompt: str) -> str:
                raise OpenAIUnavailableError("quota exhausted")

        monkeypatch.setattr(services, "get_openai_client", lambda: FailingClient())
        viewer = _make_user_with_profile("viewer")
        _make_user_with_profile("target")
        _auth(api_client, viewer)

        response = api_client.get(_url("user-target"))
        # Degrades to 503, not a 500, and caches nothing.
        assert response.status_code == 503
        assert CompatibilityBlurb.objects.count() == 0

    def test_no_api_key_returns_503(
        self, api_client: APIClient, monkeypatch: pytest.MonkeyPatch, settings: SettingsWrapper
    ) -> None:
        settings.OPENAI_API_KEY = ""
        client = FakeOpenAIClient()
        monkeypatch.setattr(services, "get_openai_client", lambda: client)
        viewer = _make_user_with_profile("viewer")
        _make_user_with_profile("target")
        _auth(api_client, viewer)

        response = api_client.get(_url("user-target"))
        assert response.status_code == 503
        assert client.complete_calls == []
