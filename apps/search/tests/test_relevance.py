"""
Search behaviour against a real cluster.

Everything here needs OpenSearch itself, because everything here is a claim
about OpenSearch: that the analyzer stems prose, that the boosts order results
the way the mapping says they do, that a strict mapping actually rejects an
unknown field. A fake could be made to "pass" all of it and would prove nothing.

Skipped when OPENSEARCH_TEST_URL is unset — see conftest.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from apps.search import services
from apps.search.client import SearchUnavailableError
from apps.search.mapping import INDEX_BODY

pytestmark = pytest.mark.usefixtures("opensearch")


def _index(
    client: Any,
    *,
    bio: str = "",
    instruments: list[str] | None = None,
    genres: list[str] | None = None,
    city: str = "",
    country: str = "",
    is_available: bool = True,
) -> uuid.UUID:
    profile_id = uuid.uuid4()
    services.index_profile(
        profile_id=str(profile_id),
        bio=bio,
        instruments=instruments or [],
        genres=genres or [],
        city=city,
        country=country,
        is_available=is_available,
    )
    client.refresh()
    return profile_id


class TestFindsWhatItShould:
    def test_indexed_profile_is_searchable(self, opensearch: Any) -> None:
        pid = _index(opensearch, instruments=["Double Bass"], genres=["Jazz"])

        hits = services.search(query="double bass")

        assert [h[0] for h in hits] == [pid]

    def test_matches_a_genre(self, opensearch: Any) -> None:
        pid = _index(opensearch, genres=["Hindustani Classical"])
        assert [h[0] for h in services.search(query="hindustani")] == [pid]

    def test_matches_a_city(self, opensearch: Any) -> None:
        pid = _index(opensearch, city="Bengaluru", country="India")
        assert [h[0] for h in services.search(query="Bengaluru")] == [pid]

    def test_bio_is_stemmed(self, opensearch: Any) -> None:
        """
        The english analyzer is why "record" finds "recording". Without it this
        query returns nothing, which is the single clearest reason bio is not
        mapped as a plain keyword.
        """
        pid = _index(opensearch, bio="I spend most weekends recording at home.")

        assert [h[0] for h in services.search(query="record")] == [pid]

    def test_no_match_returns_empty(self, opensearch: Any) -> None:
        _index(opensearch, instruments=["Tabla"], genres=["Folk"])
        assert services.search(query="bassoon") == []


class TestRanking:
    def test_an_instrument_match_outranks_a_passing_mention_in_a_bio(self, opensearch: Any) -> None:
        """
        The boost, doing the job it exists for. Someone typing "drums" is naming
        what they want to find, so an actual drummer must beat a violinist who
        mentioned drums in passing — which is exactly what a single blended
        embedding text could not express.
        """
        drummer = _index(opensearch, instruments=["Drums"], bio="Available most weekends.")
        violinist = _index(
            opensearch,
            instruments=["Violin"],
            bio="I tried drums once at a party and it went badly.",
        )

        ranked = [h[0] for h in services.search(query="drums")]

        assert ranked.index(drummer) < ranked.index(violinist)

    def test_scores_descend(self, opensearch: Any) -> None:
        _index(opensearch, instruments=["Sitar"], genres=["Hindustani Classical"])
        _index(opensearch, bio="I have heard a sitar before.")

        scores = [score for _, score in services.search(query="sitar")]

        assert len(scores) == 2
        assert scores == sorted(scores, reverse=True)

    def test_limit_caps_results(self, opensearch: Any) -> None:
        for _ in range(5):
            _index(opensearch, instruments=["Guitar"])

        assert len(services.search(query="guitar", limit=2)) == 2


class TestFiltering:
    def test_available_only_excludes_the_unavailable(self, opensearch: Any) -> None:
        """
        The filter runs inside the same query as the scoring pass, against the
        replicated flag. That is the whole reason is_available is duplicated
        into this index: filtering afterwards would shrink the caller's limit.
        """
        free = _index(opensearch, instruments=["Guitar"], is_available=True)
        busy = _index(opensearch, instruments=["Guitar"], is_available=False)

        ids = [h[0] for h in services.search(query="guitar", available_only=True)]

        assert free in ids
        assert busy not in ids

    def test_unfiltered_search_returns_both(self, opensearch: Any) -> None:
        free = _index(opensearch, instruments=["Guitar"], is_available=True)
        busy = _index(opensearch, instruments=["Guitar"], is_available=False)

        ids = [h[0] for h in services.search(query="guitar")]

        assert {free, busy} <= set(ids)


class TestIndexIntegrity:
    def test_reindexing_replaces_rather_than_duplicating(self, opensearch: Any) -> None:
        """At-least-once delivery means this happens in production, not just here."""
        profile_id = uuid.uuid4()
        for _ in range(3):
            services.index_profile(
                profile_id=str(profile_id),
                bio="",
                instruments=["Trumpet"],
                genres=[],
                city="",
                country="",
                is_available=True,
            )
        opensearch.refresh()

        assert len(services.search(query="trumpet")) == 1

    def test_a_cleared_field_stops_matching(self, opensearch: Any) -> None:
        """
        Indexing is a full replacement, so dropping an instrument must actually
        remove it from the index — a merge would leave it searchable forever.
        """
        profile_id = uuid.uuid4()
        services.index_profile(
            profile_id=str(profile_id),
            bio="",
            instruments=["Harmonium"],
            genres=[],
            city="",
            country="",
            is_available=True,
        )
        services.index_profile(
            profile_id=str(profile_id),
            bio="",
            instruments=["Piano"],
            genres=[],
            city="",
            country="",
            is_available=True,
        )
        opensearch.refresh()

        assert services.search(query="harmonium") == []
        assert len(services.search(query="piano")) == 1

    def test_removed_profile_stops_appearing(self, opensearch: Any) -> None:
        pid = _index(opensearch, instruments=["Flute"])

        services.remove_profile(profile_id=str(pid))
        opensearch.refresh()

        assert services.search(query="flute") == []

    def test_an_unknown_field_is_rejected(self, opensearch: Any) -> None:
        """
        `dynamic: strict`. The default would accept a typo'd key and invent a
        mapping for it, which cannot then be changed without a full reindex — a
        schema mistake that reports success on write and surfaces much later.
        """
        opensearch.ensure_index(body=INDEX_BODY)

        with pytest.raises(SearchUnavailableError):
            opensearch.index_document(
                doc_id=str(uuid.uuid4()),
                document={"bio": "fine", "instrment": ["typo"]},
            )
