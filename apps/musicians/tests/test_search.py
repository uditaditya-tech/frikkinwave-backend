"""
Search endpoint tests.

The search service is stubbed at the seam: these assert on what the *endpoint*
does with ids and scores — hydration, ordering, the response shape, the
degradation contract. Whether OpenSearch returns good ids for a given query is
apps/search/tests/test_relevance.py's job, against a real cluster.

Stubbing the service rather than `search_profiles` itself is deliberate: it
leaves the hydration path — the id → profile lookup and the missing-profile
branch — genuinely under test.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from rest_framework.test import APIClient

from apps.musicians.models import MusicianProfile
from apps.search import services as search_services
from apps.users.models import User

SEARCH_URL = "/api/musicians/search/"


def _profile(suffix: str, *, available: bool = True) -> MusicianProfile:
    user = User.objects.create_user(
        email=f"{suffix}@example.com", username=f"user-{suffix}", password="StrongPass123!"
    )
    return MusicianProfile.objects.create(user=user, bio=f"bio {suffix}", is_available=available)


class StubSearch:
    """Stands in for apps.search.services.search: returns canned hits, records calls."""

    def __init__(self) -> None:
        self.hits: list[tuple[uuid.UUID, float]] = []
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self, *, query: str, limit: int = 20, available_only: bool = False
    ) -> list[tuple[uuid.UUID, float]]:
        self.calls.append({"query": query, "limit": limit, "available_only": available_only})
        return self.hits[:limit]


@pytest.fixture
def stub_search(monkeypatch: pytest.MonkeyPatch) -> StubSearch:
    """
    Patch the attribute on the search services module.

    search_profiles imports that module inside the function body and looks the
    name up at call time, so replacing the attribute here is what the caller
    actually resolves.
    """
    stub = StubSearch()
    monkeypatch.setattr(search_services, "search", stub)
    return stub


@pytest.mark.django_db
class TestSearch:
    def test_returns_profiles_in_the_order_search_ranked_them(
        self, api_client: APIClient, stub_search: StubSearch
    ) -> None:
        a, b, c = _profile("a"), _profile("b"), _profile("c")
        stub_search.hits = [(b.id, 4.2), (a.id, 2.0), (c.id, 0.5)]

        response = api_client.get(SEARCH_URL, {"q": "jazz drummer"})

        assert response.status_code == 200
        assert response.data["query"] == "jazz drummer"
        ids = [r["id"] for r in response.data["results"]]
        assert ids == [str(b.id), str(a.id), str(c.id)]

    def test_each_result_carries_its_score(
        self, api_client: APIClient, stub_search: StubSearch
    ) -> None:
        a = _profile("a")
        stub_search.hits = [(a.id, 4.25)]

        response = api_client.get(SEARCH_URL, {"q": "x"})

        assert response.data["results"][0]["score"] == 4.25

    def test_a_hit_whose_profile_is_gone_is_skipped(
        self, api_client: APIClient, stub_search: StubSearch
    ) -> None:
        """
        A real state, not an impossible one: the index is a separate store with
        nothing cascading into it, so it can outlive a profile.
        """
        alive = _profile("alive")
        stub_search.hits = [(uuid.uuid4(), 9.0), (alive.id, 1.0)]

        response = api_client.get(SEARCH_URL, {"q": "x"})

        assert [r["id"] for r in response.data["results"]] == [str(alive.id)]

    def test_limit_reaches_the_service_and_is_capped(
        self, api_client: APIClient, stub_search: StubSearch
    ) -> None:
        api_client.get(SEARCH_URL, {"q": "x", "limit": "2"})
        assert stub_search.calls[-1]["limit"] == 2

        api_client.get(SEARCH_URL, {"q": "x", "limit": "999"})
        assert stub_search.calls[-1]["limit"] == 50  # clamped, not rejected

        api_client.get(SEARCH_URL, {"q": "x", "limit": "abc"})
        assert stub_search.calls[-1]["limit"] == 20  # garbage falls back to the default

    def test_available_flag_reaches_the_service(
        self, api_client: APIClient, stub_search: StubSearch
    ) -> None:
        api_client.get(SEARCH_URL, {"q": "x", "available": "true"})
        assert stub_search.calls[-1]["available_only"] is True

        api_client.get(SEARCH_URL, {"q": "x"})
        assert stub_search.calls[-1]["available_only"] is False

    def test_blank_query_returns_400(self, api_client: APIClient) -> None:
        assert api_client.get(SEARCH_URL, {"q": "   "}).status_code == 400
        assert api_client.get(SEARCH_URL).status_code == 400

    def test_unauthenticated_allowed(self, api_client: APIClient, stub_search: StubSearch) -> None:
        stub_search.hits = [(_profile("a").id, 1.0)]
        assert api_client.get(SEARCH_URL, {"q": "x"}).status_code == 200

    def test_no_cluster_returns_empty_not_an_error(self, api_client: APIClient) -> None:
        """
        Nothing is stubbed here: settings blank OPENSEARCH_URL under pytest, so
        this runs the real degradation path end to end. An upstream failure must
        never 500 a user request.
        """
        _profile("a")

        response = api_client.get(SEARCH_URL, {"q": "x"})

        assert response.status_code == 200
        assert response.data["results"] == []
