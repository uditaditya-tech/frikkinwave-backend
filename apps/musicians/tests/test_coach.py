"""
Profile coach tests (Phase 2.7).

Hybrid coach: deterministic completeness score + structured suggestions (always
present), plus an LLM `tip` (mocked here; null without a key). No network.
"""

import pytest
from pytest_django.fixtures import SettingsWrapper
from rest_framework.test import APIClient

from apps.musicians import services
from apps.musicians.models import Genre, Instrument, MusicianProfile
from apps.users.models import User

COACH_URL = "/api/musicians/profile/coach/"
PASSWORD = "StrongPass123!"


def _auth(api_client: APIClient, user: User) -> APIClient:
    resp = api_client.post("/api/auth/token/", {"email": user.email, "password": PASSWORD})
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {resp.data['access']}")
    return api_client


class FakeOpenAIClient:
    def __init__(self) -> None:
        self.complete_calls: list[str] = []

    def complete(self, prompt: str) -> str:
        self.complete_calls.append(prompt)
        return "Add a line about the projects you want to join."


@pytest.fixture
def fake_openai(monkeypatch: pytest.MonkeyPatch, settings: SettingsWrapper) -> FakeOpenAIClient:
    settings.OPENAI_API_KEY = "test-key"
    client = FakeOpenAIClient()
    monkeypatch.setattr(services, "get_openai_client", lambda: client)
    return client


def _make_user(suffix: str) -> User:
    return User.objects.create_user(
        email=f"{suffix}@example.com", username=f"user-{suffix}", password=PASSWORD
    )


@pytest.mark.django_db
class TestCoach:
    def test_incomplete_profile_lists_missing_fields(
        self, api_client: APIClient, fake_openai: FakeOpenAIClient
    ) -> None:
        user = _make_user("sparse")
        # Only a (short) bio + city set → instruments/genres/sound_url/country missing.
        MusicianProfile.objects.create(user=user, bio="short", city="Pune")
        _auth(api_client, user)

        response = api_client.get(COACH_URL)

        assert response.status_code == 200
        fields = {s["field"] for s in response.data["suggestions"]}
        # bio too short (<30) so it's still flagged; instruments/genres/sound_url/country missing.
        assert {"bio", "instruments", "genres", "sound_url", "country"} <= fields
        assert response.data["completeness"] == 10  # only city (10) earned
        assert response.data["tip"] == "Add a line about the projects you want to join."
        assert len(fake_openai.complete_calls) == 1

    def test_complete_profile_scores_100(
        self, api_client: APIClient, fake_openai: FakeOpenAIClient
    ) -> None:
        user = _make_user("full")
        profile = MusicianProfile.objects.create(
            user=user,
            bio="I am a seasoned jazz guitarist with ten years on stage.",
            city="Mumbai",
            country="India",
            sound_url="https://example.com/track",
        )
        profile.musician_instruments.create(
            instrument=Instrument.objects.create(name="Guitar", slug="guitar"),
            proficiency="advanced",
        )
        profile.genres.add(Genre.objects.create(name="Jazz", slug="jazz"))
        _auth(api_client, user)

        response = api_client.get(COACH_URL)

        assert response.data["completeness"] == 100
        assert response.data["suggestions"] == []
        assert response.data["tip"] is not None

    def test_no_api_key_returns_null_tip_with_suggestions(
        self, api_client: APIClient, monkeypatch: pytest.MonkeyPatch, settings: SettingsWrapper
    ) -> None:
        settings.OPENAI_API_KEY = ""
        client = FakeOpenAIClient()
        monkeypatch.setattr(services, "get_openai_client", lambda: client)

        user = _make_user("nokey")
        MusicianProfile.objects.create(user=user, bio="short")
        _auth(api_client, user)

        response = api_client.get(COACH_URL)

        assert response.status_code == 200
        assert response.data["tip"] is None
        assert response.data["suggestions"]  # still computed
        assert client.complete_calls == []  # LLM never called

    def test_openai_error_returns_null_tip_with_suggestions(
        self, api_client: APIClient, monkeypatch: pytest.MonkeyPatch, settings: SettingsWrapper
    ) -> None:
        from apps.musicians.openai_client import OpenAIUnavailableError

        settings.OPENAI_API_KEY = "test-key"

        class FailingClient:
            def complete(self, prompt: str) -> str:
                raise OpenAIUnavailableError("quota exhausted")

        monkeypatch.setattr(services, "get_openai_client", lambda: FailingClient())
        user = _make_user("err")
        MusicianProfile.objects.create(user=user, bio="short")
        _auth(api_client, user)

        response = api_client.get(COACH_URL)
        # Rule-based coaching survives; only the LLM tip is dropped — no 500.
        assert response.status_code == 200
        assert response.data["tip"] is None
        assert response.data["suggestions"]

    def test_viewer_without_profile_returns_400(
        self, api_client: APIClient, fake_openai: FakeOpenAIClient
    ) -> None:
        _auth(api_client, _make_user("noprofile"))
        assert api_client.get(COACH_URL).status_code == 400

    def test_unauthenticated_returns_401(self, api_client: APIClient) -> None:
        assert api_client.get(COACH_URL).status_code == 401
