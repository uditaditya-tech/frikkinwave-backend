"""
Semantic search endpoint tests (Phase 2.5).

The OpenAI client is mocked: the fake returns a fixed query vector (closest to
profile A), so ranking is deterministic without any network call or API key.
"""

import pytest
from pytest_django.fixtures import SettingsWrapper
from rest_framework.test import APIClient

from apps.musicians import services
from apps.musicians.models import (
    EMBEDDING_DIMENSIONS,
    MusicianProfile,
    ProfileEmbedding,
)
from apps.users.models import User

SEARCH_URL = "/api/musicians/search/"


def _unit_vector(hot_index: int, second: int | None = None) -> list[float]:
    vec = [0.0] * EMBEDDING_DIMENSIONS
    vec[hot_index] = 1.0
    if second is not None:
        vec[second] = 0.1
    return vec


def _profile_with_embedding(
    suffix: str, hot_index: int, *, available: bool = True
) -> MusicianProfile:
    user = User.objects.create_user(
        email=f"{suffix}@example.com", username=f"user-{suffix}", password="StrongPass123!"
    )
    profile = MusicianProfile.objects.create(user=user, bio=f"bio {suffix}", is_available=available)
    ProfileEmbedding.objects.create(
        profile=profile, embedding=_unit_vector(hot_index), embedding_text=f"text {suffix}"
    )
    return profile


class FakeOpenAIClient:
    def __init__(self, vector: list[float]) -> None:
        self.vector = vector
        self.calls: list[str] = []

    def embed(self, text: str) -> list[float]:
        self.calls.append(text)
        return self.vector


@pytest.fixture
def query_closest_to_a(
    monkeypatch: pytest.MonkeyPatch, settings: SettingsWrapper
) -> FakeOpenAIClient:
    settings.OPENAI_API_KEY = "test-key"
    # Disable the similarity floor so these tests assert on raw ranking/counts;
    # the floor itself is covered by test_drops_below_similarity_threshold.
    settings.SEARCH_SIMILARITY_THRESHOLD = 0.0
    # Mostly index 0 (profile A), a touch of index 1 (profile B).
    client = FakeOpenAIClient(_unit_vector(0, second=1))
    monkeypatch.setattr(services, "get_openai_client", lambda: client)
    return client


@pytest.mark.django_db
class TestSearch:
    def test_ranks_by_similarity(
        self, api_client: APIClient, query_closest_to_a: FakeOpenAIClient
    ) -> None:
        a = _profile_with_embedding("a", 0)
        b = _profile_with_embedding("b", 1)
        c = _profile_with_embedding("c", 2)

        response = api_client.get(SEARCH_URL, {"q": "jazz drummer"})

        assert response.status_code == 200
        assert response.data["query"] == "jazz drummer"
        results = response.data["results"]
        ids = [r["id"] for r in results]
        assert ids == [str(a.id), str(b.id), str(c.id)]  # A nearest, C farthest
        # Each result carries a similarity score, descending.
        sims = [r["similarity"] for r in results]
        assert sims[0] > sims[1] > sims[2]
        assert query_closest_to_a.calls == ["jazz drummer"]

    def test_drops_below_similarity_threshold(
        self, api_client: APIClient, monkeypatch: pytest.MonkeyPatch, settings: SettingsWrapper
    ) -> None:
        # Query ~= profile A (similarity ~0.995); B (~0.10) and C (0.0) are weak.
        settings.OPENAI_API_KEY = "test-key"
        settings.SEARCH_SIMILARITY_THRESHOLD = 0.8
        client = FakeOpenAIClient(_unit_vector(0, second=1))
        monkeypatch.setattr(services, "get_openai_client", lambda: client)

        a = _profile_with_embedding("a", 0)
        _profile_with_embedding("b", 1)  # below floor → dropped
        _profile_with_embedding("c", 2)  # below floor → dropped

        response = api_client.get(SEARCH_URL, {"q": "jazz drummer"})

        assert response.status_code == 200
        results = response.data["results"]
        assert [r["id"] for r in results] == [str(a.id)]
        assert results[0]["similarity"] >= 0.8

    def test_limit_caps_results(
        self, api_client: APIClient, query_closest_to_a: FakeOpenAIClient
    ) -> None:
        for i in range(5):
            _profile_with_embedding(f"p{i}", i)
        response = api_client.get(SEARCH_URL, {"q": "anything", "limit": "2"})
        assert response.status_code == 200
        assert len(response.data["results"]) == 2

    def test_available_filter(
        self, api_client: APIClient, query_closest_to_a: FakeOpenAIClient
    ) -> None:
        _profile_with_embedding("avail", 0, available=True)
        _profile_with_embedding("busy", 1, available=False)

        response = api_client.get(SEARCH_URL, {"q": "x", "available": "true"})
        usernames = [r["username"] for r in response.data["results"]]
        assert usernames == ["user-avail"]

    def test_excludes_profiles_without_embedding(
        self, api_client: APIClient, query_closest_to_a: FakeOpenAIClient
    ) -> None:
        _profile_with_embedding("has", 0)
        # A profile with no embedding row.
        bare_user = User.objects.create_user(
            email="bare@example.com", username="user-bare", password="StrongPass123!"
        )
        MusicianProfile.objects.create(user=bare_user, bio="no embedding")

        response = api_client.get(SEARCH_URL, {"q": "x"})
        usernames = [r["username"] for r in response.data["results"]]
        assert usernames == ["user-has"]

    def test_blank_query_returns_400(
        self, api_client: APIClient, query_closest_to_a: FakeOpenAIClient
    ) -> None:
        assert api_client.get(SEARCH_URL, {"q": "   "}).status_code == 400
        assert api_client.get(SEARCH_URL).status_code == 400

    def test_unauthenticated_allowed(
        self, api_client: APIClient, query_closest_to_a: FakeOpenAIClient
    ) -> None:
        _profile_with_embedding("a", 0)
        assert api_client.get(SEARCH_URL, {"q": "x"}).status_code == 200

    def test_openai_error_returns_empty(
        self, api_client: APIClient, monkeypatch: pytest.MonkeyPatch, settings: SettingsWrapper
    ) -> None:
        from apps.musicians.openai_client import OpenAIUnavailableError

        settings.OPENAI_API_KEY = "test-key"

        class FailingClient:
            def embed(self, text: str) -> list[float]:
                raise OpenAIUnavailableError("quota exhausted")

        monkeypatch.setattr(services, "get_openai_client", lambda: FailingClient())
        _profile_with_embedding("a", 0)

        response = api_client.get(SEARCH_URL, {"q": "x"})
        # Degrades to empty results, not a 500.
        assert response.status_code == 200
        assert response.data["results"] == []

    def test_no_api_key_returns_empty(
        self, api_client: APIClient, monkeypatch: pytest.MonkeyPatch, settings: SettingsWrapper
    ) -> None:
        settings.OPENAI_API_KEY = ""
        client = FakeOpenAIClient(_unit_vector(0))
        monkeypatch.setattr(services, "get_openai_client", lambda: client)
        _profile_with_embedding("a", 0)

        response = api_client.get(SEARCH_URL, {"q": "x"})
        assert response.status_code == 200
        assert response.data["results"] == []
        assert client.calls == []  # never embedded — short-circuited on no key
