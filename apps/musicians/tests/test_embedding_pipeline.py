"""
Embedding pipeline tests (Phase 2.4).

A fake OpenAI client is injected via monkeypatch — no network, no API key. The
pipeline runs profile save → on_commit → eager Celery task → service → store, so
API-level tests wrap the request in django_capture_on_commit_callbacks.
"""

from collections.abc import Callable
from typing import Any

import pytest
from pytest_django.fixtures import SettingsWrapper
from rest_framework.test import APIClient

from apps.musicians import services
from apps.musicians.models import EMBEDDING_DIMENSIONS, Genre, Instrument, MusicianProfile
from apps.users.models import User

PASSWORD = "StrongPass123!"
PROFILE_URL = "/api/musicians/profile/"
PROFILE_ME_URL = "/api/musicians/profile/me/"


class FakeOpenAIClient:
    """Records embed() calls and returns a fixed-size vector."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def embed(self, text: str) -> list[float]:
        self.calls.append(text)
        return [0.01] * EMBEDDING_DIMENSIONS


@pytest.fixture
def fake_openai(monkeypatch: pytest.MonkeyPatch, settings: SettingsWrapper) -> FakeOpenAIClient:
    settings.OPENAI_API_KEY = "test-key"  # non-empty so the no-key guard passes
    client = FakeOpenAIClient()
    monkeypatch.setattr(services, "get_openai_client", lambda: client)
    return client


def _auth(api_client: APIClient, user: User) -> APIClient:
    resp = api_client.post("/api/auth/token/", {"email": user.email, "password": PASSWORD})
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {resp.data['access']}")
    return api_client


# ---------------------------------------------------------------------------
# build_embedding_text (pure-ish, reads relations)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestBuildEmbeddingText:
    def test_includes_bio_location_instruments_genres(
        self, profile: MusicianProfile, instrument: Instrument, genre: Genre
    ) -> None:
        profile.musician_instruments.create(instrument=instrument, proficiency="advanced")
        profile.genres.add(genre)

        text = services.build_embedding_text(profile)

        assert "I play lead guitar." in text
        assert "Mumbai" in text
        assert "Electric Guitar (advanced)" in text
        assert "Jazz" in text


# ---------------------------------------------------------------------------
# Pipeline via the API (on_commit → task → service)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestEmbeddingPipeline:
    def test_create_profile_generates_embedding(
        self,
        api_client: APIClient,
        user: User,
        fake_openai: FakeOpenAIClient,
        django_capture_on_commit_callbacks: Callable[..., Any],
    ) -> None:
        _auth(api_client, user)
        with django_capture_on_commit_callbacks(execute=True):
            response = api_client.post(PROFILE_URL, {"bio": "Drummer for hire", "city": "Pune"})

        assert response.status_code == 201
        assert len(fake_openai.calls) == 1
        profile = MusicianProfile.objects.get(user=user)
        assert hasattr(profile, "embedding")
        assert profile.embedding.embedding_text == fake_openai.calls[0]
        assert len(profile.embedding.embedding.tolist()) == EMBEDDING_DIMENSIONS

    def test_updating_bio_reembeds(
        self,
        api_client: APIClient,
        user: User,
        fake_openai: FakeOpenAIClient,
        django_capture_on_commit_callbacks: Callable[..., Any],
    ) -> None:
        _auth(api_client, user)
        with django_capture_on_commit_callbacks(execute=True):
            api_client.post(PROFILE_URL, {"bio": "original", "city": "Pune"})
        with django_capture_on_commit_callbacks(execute=True):
            api_client.patch(PROFILE_ME_URL, {"bio": "totally new bio"})

        assert len(fake_openai.calls) == 2  # re-embedded on bio change
        assert "totally new bio" in fake_openai.calls[1]

    def test_updating_only_availability_does_not_reembed(
        self,
        api_client: APIClient,
        user: User,
        fake_openai: FakeOpenAIClient,
        django_capture_on_commit_callbacks: Callable[..., Any],
    ) -> None:
        _auth(api_client, user)
        with django_capture_on_commit_callbacks(execute=True):
            api_client.post(PROFILE_URL, {"bio": "steady", "city": "Pune"})
        with django_capture_on_commit_callbacks(execute=True):
            api_client.patch(PROFILE_ME_URL, {"is_available": False})

        # The task ran again, but embedding text is unchanged → no OpenAI call.
        assert len(fake_openai.calls) == 1


# ---------------------------------------------------------------------------
# Service-level guards
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestEmbeddingGuards:
    def test_missing_profile_is_noop(self, fake_openai: FakeOpenAIClient) -> None:
        services.generate_profile_embedding(profile_id="00000000-0000-0000-0000-000000000000")
        assert fake_openai.calls == []

    def test_no_api_key_skips(
        self, profile: MusicianProfile, monkeypatch: pytest.MonkeyPatch, settings: SettingsWrapper
    ) -> None:
        from apps.musicians.models import ProfileEmbedding

        settings.OPENAI_API_KEY = ""
        client = FakeOpenAIClient()
        monkeypatch.setattr(services, "get_openai_client", lambda: client)

        services.generate_profile_embedding(profile_id=str(profile.id))
        assert client.calls == []
        assert not ProfileEmbedding.objects.filter(profile=profile).exists()

    def test_reembed_keeps_single_row(
        self, profile: MusicianProfile, fake_openai: FakeOpenAIClient
    ) -> None:
        from apps.musicians.models import ProfileEmbedding

        services.generate_profile_embedding(profile_id=str(profile.id))
        profile.bio = "changed bio"
        profile.save()
        services.generate_profile_embedding(profile_id=str(profile.id))

        assert ProfileEmbedding.objects.filter(profile=profile).count() == 1
        assert len(fake_openai.calls) == 2
