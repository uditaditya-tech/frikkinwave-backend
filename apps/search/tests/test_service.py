"""
Pure unit tests for the search service.

Deliberately touch no musicians model. The service takes a profile id and a
composed string and returns ids and scores — if these tests ever need a
MusicianProfile, the boundary has been broken.
"""

from __future__ import annotations

import uuid

import pytest
from pytest_django.fixtures import SettingsWrapper

from apps.search import services
from apps.search.models import EMBEDDING_DIMENSIONS, ProfileEmbedding


class FakeOpenAIClient:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def embed(self, text: str) -> list[float]:
        self.calls.append(text)
        return [0.01] * EMBEDDING_DIMENSIONS


@pytest.fixture
def fake_openai(monkeypatch: pytest.MonkeyPatch, settings: SettingsWrapper) -> FakeOpenAIClient:
    settings.OPENAI_API_KEY = "test-key"
    client = FakeOpenAIClient()
    monkeypatch.setattr(services, "get_openai_client", lambda: client)
    return client


@pytest.mark.django_db
class TestRemoveProfile:
    """
    Without a ForeignKey there is no cascade delete, so removal is an explicit
    operation. These exist because the function had no caller when it was
    written — untested *and* unused is how a cleanup path quietly stops working
    before anything ever depends on it.
    """

    def test_removes_the_row(self, fake_openai: FakeOpenAIClient) -> None:
        pid = uuid.uuid4()
        services.index_profile(profile_id=str(pid), embedding_text="text", is_available=True)
        assert ProfileEmbedding.objects.filter(profile_id=pid).exists()

        services.remove_profile(profile_id=str(pid))
        assert not ProfileEmbedding.objects.filter(profile_id=pid).exists()

    def test_is_idempotent(self) -> None:
        """Redelivery and double-deletes must not raise — there is no row to miss."""
        pid = str(uuid.uuid4())
        services.remove_profile(profile_id=pid)
        services.remove_profile(profile_id=pid)

    def test_leaves_other_rows_alone(self, fake_openai: FakeOpenAIClient) -> None:
        keep, drop = uuid.uuid4(), uuid.uuid4()
        services.index_profile(profile_id=str(keep), embedding_text="a", is_available=True)
        services.index_profile(profile_id=str(drop), embedding_text="b", is_available=True)

        services.remove_profile(profile_id=str(drop))

        assert ProfileEmbedding.objects.filter(profile_id=keep).exists()
        assert not ProfileEmbedding.objects.filter(profile_id=drop).exists()


@pytest.mark.django_db
class TestSearchDegradesGracefully:
    def test_no_api_key_returns_empty_not_an_error(self, settings: SettingsWrapper) -> None:
        """An upstream failure must never 500 a user request."""
        settings.OPENAI_API_KEY = ""
        assert services.search(query="anything") == []

    def test_returns_ids_and_scores_never_objects(self, fake_openai: FakeOpenAIClient) -> None:
        """The contract that makes this extractable — ids cross a boundary, ORM objects cannot."""
        pid = uuid.uuid4()
        services.index_profile(profile_id=str(pid), embedding_text="text", is_available=True)

        hits = services.search(query="text", similarity_threshold=0.0)

        assert len(hits) == 1
        found_id, similarity = hits[0]
        assert isinstance(found_id, uuid.UUID)
        assert isinstance(similarity, float)

    def test_available_filter_uses_the_replica(self, fake_openai: FakeOpenAIClient) -> None:
        """
        The filter runs inside the vector query against the replicated flag —
        not against the musicians table, which this service cannot see.
        """
        on, off = uuid.uuid4(), uuid.uuid4()
        services.index_profile(profile_id=str(on), embedding_text="a", is_available=True)
        services.index_profile(profile_id=str(off), embedding_text="b", is_available=False)

        ids = [
            h[0] for h in services.search(query="x", available_only=True, similarity_threshold=0.0)
        ]

        assert on in ids
        assert off not in ids
