"""
The profile index: what a profile looks like once it is searchable.

This app owns the index definition because it owns the index. The producer
decides which profile *facts* to publish; this module decides how they are
analyzed, weighted and filtered. That split is what lets relevance be retuned
without the musicians app knowing it happened.

The fields are structured rather than one blob, and that is the substantive
change from the vector era. Embeddings needed a single string, so
`build_embedding_text` blended bio + instruments + genres + city together — and
that blending is precisely what diluted short queries, which is why a
near-verbatim bio query used to top out around 0.55 similarity. BM25 has the
opposite need: keep the fields apart so a query naming an instrument can be
scored against the instrument field instead of drowning in prose.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

#: Fields the free-text query is matched against, with their boosts.
#:
#: The weighting encodes an editorial judgement: someone typing "jazz double
#: bass" is naming what they want to find, so an exact instrument or genre
#: should outrank a bio that mentions the same word in passing.
#:
#: These numbers are reasoned, NOT measured — there is no query log to tune them
#: against yet. Revisit with real queries before treating them as settled.
SEARCH_FIELDS = [
    "instruments^3",
    "genres^2",
    "city^2",
    "country",
    "bio",
]

#: Index settings + mappings, passed verbatim to `indices.create`.
INDEX_BODY: dict[str, Any] = {
    "settings": {
        # One shard, deliberately. BM25 computes term frequencies per shard, so
        # the same document can score differently depending on which shard it
        # landed on — an inconsistency that is invisible on a huge corpus and
        # obvious on one this size. One shard also makes scoring exact rather
        # than approximate.
        "number_of_shards": 1,
        # No replica. This index is DERIVED: every document in it can be rebuilt
        # from Postgres by `reindex_profiles`, so a replica buys availability
        # during a node failure, not durability. Raise it when the domain runs
        # more than one node and search being briefly down actually costs
        # something.
        "number_of_replicas": 0,
    },
    "mappings": {
        # Reject unknown fields rather than silently inferring a type for them.
        # The default ("true") would let a typo'd payload key create a new field
        # with a guessed mapping, which then cannot be changed without a
        # reindex — a schema mistake that reports success at write time and
        # surfaces much later.
        "dynamic": "strict",
        "properties": {
            # English analyzer: bio is prose, so stemming ("recording" ->
            # "record") is worth having. The other fields are names, where
            # stemming buys little and can surprise.
            "bio": {"type": "text", "analyzer": "english"},
            "instruments": {"type": "text"},
            "genres": {"type": "text"},
            # Text for matching a typed city name, with a keyword sub-field so
            # an exact filter or an aggregation is available without a reindex.
            "city": {"type": "text", "fields": {"keyword": {"type": "keyword"}}},
            "country": {"type": "text", "fields": {"keyword": {"type": "keyword"}}},
            # Replicated from MusicianProfile. It lives here because the
            # availability filter and the scoring pass have to run in the same
            # query: filtering afterwards would silently shrink a caller's
            # requested limit (ask for 20, drop half, return 9).
            "is_available": {"type": "boolean"},
            # When this document was last written. Not searchable content — it
            # is the watermark a full rebuild prunes against: reindex everything
            # with a fresh timestamp, then delete whatever still carries an old
            # one. That is how a profile deleted straight from the database
            # (admin, a cascade, the demo seeder's --reset) eventually leaves the
            # index, in a system that has no delete endpoint to fire an event.
            "indexed_at": {"type": "date"},
        },
    },
}


def build_document(
    *,
    bio: str,
    instruments: list[str],
    genres: list[str],
    city: str,
    country: str,
    is_available: bool,
    indexed_at: datetime,
) -> dict[str, Any]:
    """
    Assemble the indexed document from the facts the producer published.

    Deliberately total: every mapped field is written on every index call, so a
    field cleared on the profile is cleared in the index too. Indexing is a full
    document replacement, not a merge — omitting a key here would leave the
    previous value searchable forever.
    """
    return {
        "bio": bio,
        "instruments": instruments,
        "genres": genres,
        "city": city,
        "country": country,
        "is_available": is_available,
        "indexed_at": indexed_at.isoformat(),
    }


def build_query(*, query: str, limit: int, available_only: bool) -> dict[str, Any]:
    """
    Build the search request body.

    `_source: False` because the caller wants ids and scores, nothing else — it
    hydrates the full profiles from Postgres, which it owns. Asking OpenSearch
    to ship documents back that are then thrown away is pure transfer cost.
    """
    body: dict[str, Any] = {
        "size": limit,
        "_source": False,
        "query": {
            "bool": {
                "must": [
                    {
                        "multi_match": {
                            "query": query,
                            "fields": SEARCH_FIELDS,
                            # best_fields: score by the single field that
                            # matches best, rather than summing across fields.
                            # "guitar" appearing in both bio and instruments
                            # should not beat a stronger instrument-only match.
                            "type": "best_fields",
                        }
                    }
                ],
            }
        },
    }

    if available_only:
        # A filter clause, not a must: it is a yes/no gate that should not
        # contribute to the relevance score, and filter clauses are cacheable.
        body["query"]["bool"]["filter"] = [{"term": {"is_available": True}}]

    return body


def build_stale_query(*, older_than: datetime) -> dict[str, Any]:
    """
    Match documents last written before `older_than`.

    Used by a full rebuild to remove what it did not touch. Expressed as a range
    on the watermark rather than as a list of ids to keep: shipping every known
    profile id to the cluster to diff against would be the one part of a rebuild
    that got worse as the site grew.
    """
    return {"query": {"range": {"indexed_at": {"lt": older_than.isoformat()}}}}
