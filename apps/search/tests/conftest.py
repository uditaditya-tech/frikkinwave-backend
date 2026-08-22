"""
Fixtures for search tests.

Two tiers, deliberately:

`fake_search` injects a spy in place of the client. It proves the *wiring* — that
a filter is asked for, that a limit is passed through, that an unreachable
cluster degrades instead of raising. It cannot prove anything about relevance,
because a fake that answers queries would only be testing the fake.

`opensearch` points the service at a real cluster and gives it an index of its
own. That is the only way to assert that the mapping analyzes text the way it is
supposed to and that ranking comes out in the right order. It SKIPS when
OPENSEARCH_TEST_URL is unset, so `pytest` works on a laptop with no container —
and tests/test_infrastructure.py asserts CI sets it, so skipping is a local
convenience rather than a way for these to quietly stop running.

The MusicianProfile fixtures below look like the coupling this extraction
removed. They are not: the *service* needs only ids and plain fields. Profiles
appear because the pipeline tests exercise the seam end to end — profile saved →
event published → relayed → indexed — and testing a seam requires both sides.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from pytest_django.fixtures import SettingsWrapper

from apps.musicians.models import Genre, Instrument, MusicianProfile
from apps.users.models import User


class FakeSearchClient:
    """
    Records what it was asked, answers with whatever it was told to answer.

    Mirrors the SearchClient surface rather than the SDK's, so these tests break
    if the seam's shape changes — which is the point of having a seam.
    """

    def __init__(self) -> None:
        self.indexed: dict[str, dict[str, Any]] = {}
        self.deleted: list[str] = []
        self.queries: list[dict[str, Any]] = []
        self.ensured: int = 0
        self.hits: list[dict[str, Any]] = []
        self.index = "fake-index"

    def ensure_index(self, *, body: dict[str, Any]) -> bool:
        self.ensured += 1
        return True

    def index_document(self, *, doc_id: str, document: dict[str, Any]) -> None:
        self.indexed[doc_id] = document

    def delete_document(self, *, doc_id: str) -> bool:
        self.deleted.append(doc_id)
        return self.indexed.pop(doc_id, None) is not None

    def search(self, *, body: dict[str, Any]) -> list[dict[str, Any]]:
        self.queries.append(body)
        return self.hits

    def refresh(self) -> None:  # pragma: no cover - nothing to flush
        pass


@pytest.fixture
def fake_search(monkeypatch: pytest.MonkeyPatch, settings: SettingsWrapper) -> FakeSearchClient:
    """Swap the client for a spy, and configure a URL so the guards let calls through."""
    from apps.search import services

    settings.OPENSEARCH_URL = "http://fake:9200"
    client = FakeSearchClient()
    monkeypatch.setattr(services, "get_search_client", lambda: client)
    return client


@pytest.fixture
def opensearch(settings: SettingsWrapper) -> Iterator[Any]:
    """
    Point the service at a real cluster, in an index unique to this test.

    A unique index per test rather than a shared one that is emptied between
    tests: emptying relies on a refresh landing before the next write, which is
    the kind of ordering that fails once in fifty runs and looks like a flake in
    whatever test happens to run next.
    """
    url = os.environ.get("OPENSEARCH_TEST_URL")
    if not url:
        pytest.skip("OPENSEARCH_TEST_URL is not set — no cluster to test against")

    from apps.search.client import get_search_client

    settings.OPENSEARCH_URL = url
    settings.OPENSEARCH_INDEX = f"test-profiles-{uuid.uuid4().hex}"
    get_search_client.cache_clear()

    client = get_search_client()
    try:
        yield client
    finally:
        client.delete_index()
        get_search_client.cache_clear()


@pytest.fixture
def instrument(db: None) -> Instrument:
    return Instrument.objects.create(name="Electric Guitar", slug="electric-guitar")


@pytest.fixture
def genre(db: None) -> Genre:
    return Genre.objects.create(name="Jazz", slug="jazz")


@pytest.fixture
def profile(user: User) -> MusicianProfile:
    return MusicianProfile.objects.create(
        user=user,
        bio="I play lead guitar.",
        city="Mumbai",
        country="India",
        is_available=True,
    )
