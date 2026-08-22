"""
Unit tests for the search service.

Deliberately touch no musicians model. The service takes a profile id and plain
fields and returns ids and scores — if these tests ever need a MusicianProfile,
the boundary has been broken.

These use the spy client, so they assert on what the service *asks for*, never
on what comes back. Whether the query actually finds the right profiles is
test_relevance.py's job, against a real cluster.
"""

from __future__ import annotations

import uuid

import pytest
from pytest_django.fixtures import SettingsWrapper

from apps.search import services
from apps.search.client import SearchUnavailableError
from apps.search.testing import FakeSearchClient


def _doc(profile_id: str, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "profile_id": profile_id,
        "bio": "I play lead guitar.",
        "instruments": ["Electric Guitar"],
        "genres": ["Jazz"],
        "city": "Mumbai",
        "country": "India",
        "is_available": True,
    }
    payload.update(overrides)
    return payload


class TestIndexProfile:
    def test_indexes_under_the_profile_id(self, fake_search: FakeSearchClient) -> None:
        """
        The document id IS the profile id, which is what makes a redelivery
        harmless. Kafka is at-least-once, so the same event arrives twice; a
        generated id would turn the second delivery into a duplicate result.
        """
        pid = str(uuid.uuid4())
        services.index_profile(**_doc(pid))  # type: ignore[arg-type]
        services.index_profile(**_doc(pid))  # type: ignore[arg-type]

        assert list(fake_search.indexed) == [pid]

    def test_writes_every_mapped_field(self, fake_search: FakeSearchClient) -> None:
        """
        Indexing replaces the document rather than merging into it, so a field
        left out here would keep its previous value searchable forever.
        """
        pid = str(uuid.uuid4())
        services.index_profile(**_doc(pid))  # type: ignore[arg-type]

        document = fake_search.indexed[pid]
        # indexed_at is the writer's clock, not payload content — asserted
        # present rather than equal, because a rebuild prunes against it and a
        # document written without it would be deleted by the next sweep.
        assert document.pop("indexed_at")
        assert document == {
            "bio": "I play lead guitar.",
            "instruments": ["Electric Guitar"],
            "genres": ["Jazz"],
            "city": "Mumbai",
            "country": "India",
            "is_available": True,
        }

    def test_ensures_the_index_exists(self, fake_search: FakeSearchClient) -> None:
        """
        Checked per event, not once per process: if the index disappears,
        OpenSearch would otherwise auto-create it on write with *inferred*
        mappings, silently replacing our analyzers and boosts with guesses.
        """
        services.index_profile(**_doc(str(uuid.uuid4())))  # type: ignore[arg-type]
        assert fake_search.ensured == 1

    def test_no_cluster_configured_is_a_no_op(self, settings: SettingsWrapper) -> None:
        settings.OPENSEARCH_URL = ""
        services.index_profile(**_doc(str(uuid.uuid4())))  # type: ignore[arg-type]

    def test_failure_propagates_so_the_consumer_can_retry(
        self, monkeypatch: pytest.MonkeyPatch, settings: SettingsWrapper
    ) -> None:
        """
        The asymmetry that matters: a read degrades, a write retries.

        Swallowing this would commit the Kafka offset and lose the update
        silently, leaving the index permanently stale. Raising sends it to a
        bounded retry and then the dead-letter topic, where it is visible.
        """
        settings.OPENSEARCH_URL = "http://fake:9200"

        class Failing(FakeSearchClient):
            def index_document(self, *, doc_id: str, document: dict[str, object]) -> None:
                raise SearchUnavailableError("cluster down")

        monkeypatch.setattr(services, "get_search_client", lambda: Failing())

        with pytest.raises(SearchUnavailableError):
            services.index_profile(**_doc(str(uuid.uuid4())))  # type: ignore[arg-type]


class TestRemoveProfile:
    """
    Nothing cascades into a separate store, so removal is an explicit event.
    These exist because the function had no caller when it was written —
    untested *and* unused is how a cleanup path quietly stops working before
    anything depends on it.
    """

    def test_removes_the_document(self, fake_search: FakeSearchClient) -> None:
        pid = str(uuid.uuid4())
        services.index_profile(**_doc(pid))  # type: ignore[arg-type]

        services.remove_profile(profile_id=pid)

        assert fake_search.indexed == {}

    def test_is_idempotent(self, fake_search: FakeSearchClient) -> None:
        """Redelivery and double-deletes must not raise — there is no document to miss."""
        pid = str(uuid.uuid4())
        services.remove_profile(profile_id=pid)
        services.remove_profile(profile_id=pid)

    def test_leaves_other_documents_alone(self, fake_search: FakeSearchClient) -> None:
        keep, drop = str(uuid.uuid4()), str(uuid.uuid4())
        services.index_profile(**_doc(keep))  # type: ignore[arg-type]
        services.index_profile(**_doc(drop))  # type: ignore[arg-type]

        services.remove_profile(profile_id=drop)

        assert list(fake_search.indexed) == [keep]


class TestSearch:
    def test_returns_ids_and_scores_never_objects(self, fake_search: FakeSearchClient) -> None:
        """The contract that makes this extractable — ids cross a boundary, ORM objects cannot."""
        pid = uuid.uuid4()
        fake_search.hits = [{"_id": str(pid), "_score": 1.75}]

        hits = services.search(query="guitar")

        assert hits == [(pid, 1.75)]
        assert isinstance(hits[0][0], uuid.UUID)
        assert isinstance(hits[0][1], float)

    def test_passes_the_limit_through(self, fake_search: FakeSearchClient) -> None:
        services.search(query="guitar", limit=7)
        assert fake_search.queries[0]["size"] == 7

    def test_available_filter_is_a_filter_not_a_scoring_clause(
        self, fake_search: FakeSearchClient
    ) -> None:
        """
        Availability is a yes/no gate, so it belongs in `filter` — it must not
        nudge relevance, and filter clauses are cacheable.
        """
        services.search(query="guitar", available_only=True)

        clause = fake_search.queries[0]["query"]["bool"]["filter"]
        assert clause == [{"term": {"is_available": True}}]

    def test_no_filter_clause_when_not_restricting(self, fake_search: FakeSearchClient) -> None:
        services.search(query="guitar")
        assert "filter" not in fake_search.queries[0]["query"]["bool"]

    def test_does_not_fetch_source_documents(self, fake_search: FakeSearchClient) -> None:
        """The caller hydrates from Postgres, so shipping documents back is wasted transfer."""
        services.search(query="guitar")
        assert fake_search.queries[0]["_source"] is False


class TestSearchDegradesGracefully:
    def test_no_cluster_configured_returns_empty_not_an_error(
        self, settings: SettingsWrapper
    ) -> None:
        """An upstream failure must never 500 a user request."""
        settings.OPENSEARCH_URL = ""
        assert services.search(query="anything") == []

    def test_unreachable_cluster_returns_empty_not_an_error(
        self, monkeypatch: pytest.MonkeyPatch, settings: SettingsWrapper
    ) -> None:
        settings.OPENSEARCH_URL = "http://fake:9200"

        class Failing(FakeSearchClient):
            def search(self, *, body: dict[str, object]) -> list[dict[str, object]]:
                raise SearchUnavailableError("cluster down")

        monkeypatch.setattr(services, "get_search_client", lambda: Failing())

        assert services.search(query="anything") == []


class TestTimeoutsAreSplitByPath:
    """
    Reads and writes have opposite constraints, so they must not share a timeout.

    A read is in the request path: a generous timeout there turns a slow cluster
    into worker exhaustion, so it degrades fast instead. A write is in a Kafka
    consumer with three attempts total: a timeout there burns one of them, and
    running out dead-letters the event and leaves a profile stale in search.

    Measured in production 2026-08-23 — a cold TLS connection exceeded the 3s
    read timeout twice in a row and indexed on attempt 3 of 3.
    """

    def test_the_write_timeout_is_longer_than_the_read_timeout(
        self, settings: SettingsWrapper
    ) -> None:
        assert settings.OPENSEARCH_WRITE_TIMEOUT > settings.OPENSEARCH_TIMEOUT

    def test_the_write_budget_survives_a_full_round_of_consumer_retries(
        self, settings: SettingsWrapper
    ) -> None:
        """
        The real question is not "is it bigger" but "is one attempt enough".
        A cold connection has to fit inside a SINGLE attempt, because burning
        two of three on a routine cold start leaves nothing for a real blip.
        """
        assert settings.OPENSEARCH_WRITE_TIMEOUT >= 15.0, (
            "A cold DNS+TCP+TLS connection to a VPC OpenSearch endpoint was "
            "measured taking over 3 seconds. Leave real headroom in one attempt."
        )

    def test_writes_use_the_write_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The seam must actually pass it, not merely define it."""
        from apps.search.client import SearchClient

        calls: list[float | None] = []

        class RecordingIndices:
            def exists(self, **kw: object) -> bool:
                calls.append(kw.get("request_timeout"))  # type: ignore[arg-type]
                return True

        class RecordingRaw:
            indices = RecordingIndices()

            def index(self, **kw: object) -> None:
                calls.append(kw.get("request_timeout"))  # type: ignore[arg-type]

        client = SearchClient(RecordingRaw(), "idx", write_timeout=20.0)  # type: ignore[arg-type]
        client.ensure_index(body={})
        client.index_document(doc_id="x", document={})

        assert calls == [20.0, 20.0]
