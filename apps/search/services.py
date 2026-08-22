"""
Service layer for the search app.

The contract with the rest of the system is unchanged by the move off pgvector:

    search(query=...) -> [(profile_id, score), ...]

**IDs and scores, never objects.** An ORM instance is not serializable across a
network, and producing one would require search to own the profile tables. The
caller hydrates from its own store, in the order given here. That this survived
a complete backend replacement untouched is the whole argument for having drawn
the boundary where it is.

What *did* change is the meaning of the second element. It used to be cosine
similarity, bounded 0..1, comparable between queries. It is now a BM25 relevance
score: unbounded, and meaningful only for ranking within one result set. Nothing
should compare it against a constant — which is exactly why the old
`SEARCH_SIMILARITY_THRESHOLD` floor is gone rather than retuned. A measured
strong match scores around 0.4 here, sitting right on top of the old default, so
a floor that survived the swap would have looked plausible while quietly cutting
good results.

`is_available` is still replicated into this app for the reason it always was:
the availability filter has to run inside the same query as the scoring pass.
Filter afterwards and a caller asking for 20 results silently gets 9.
"""

from __future__ import annotations

import logging
import uuid

from django.conf import settings

from apps.search.client import SearchUnavailableError, get_search_client
from apps.search.mapping import INDEX_BODY, build_document, build_query

logger = logging.getLogger(__name__)

# (profile_id, score) — BM25 relevance, higher is better. Rank-only: the
# absolute value carries no meaning across queries.
SearchHit = tuple[uuid.UUID, float]


def search(*, query: str, limit: int = 20, available_only: bool = False) -> list[SearchHit]:
    """
    Run `query` against the profile index and return the best ids, best first.

    Degrades to `[]` rather than raising when the cluster is unreachable or not
    configured — both are the same case on purpose. An upstream failure must
    never 500 a user request; an empty result set is a survivable answer, and a
    discovery feed that returns nothing for a minute is better than one that
    returns 500s.
    """
    if not settings.OPENSEARCH_URL:
        logger.warning("search_skipped_no_cluster")
        return []

    try:
        hits = get_search_client().search(
            body=build_query(query=query, limit=limit, available_only=available_only)
        )
    except SearchUnavailableError:
        logger.warning("search_skipped_cluster_unavailable", exc_info=True)
        return []

    results: list[SearchHit] = [(uuid.UUID(hit["_id"]), float(hit["_score"])) for hit in hits]
    logger.info(
        "profiles_searched",
        extra={"result_count": len(results), "limit": limit, "available_only": available_only},
    )
    return results


def index_profile(
    *,
    profile_id: str,
    bio: str,
    instruments: list[str],
    genres: list[str],
    city: str,
    country: str,
    is_available: bool,
) -> None:
    """
    Create or replace a profile's document from a self-contained payload.

    Takes the facts rather than a profile id to read: this app must not touch
    the musicians tables to find out what a profile contains. Composing the
    document from those facts is `mapping.build_document`'s job, so the index's
    shape and the index's mapping stay adjacent.

    **Errors propagate here, unlike in `search`.** This runs in a Kafka consumer,
    where a raised handler means a bounded retry and then the dead-letter topic.
    Swallowing a failure would commit the offset and lose the update silently,
    leaving the index permanently stale with nothing to show for it. The
    asymmetry is deliberate: a read degrades, a write retries.

    There is no longer any "skip if unchanged" check. That existed to avoid
    paying OpenAI for an embedding that would come back identical; re-indexing a
    document costs a local write, so buying back that cost with a stored copy of
    the previous text and a comparison is no longer worth the machinery.
    """
    if not settings.OPENSEARCH_URL:
        logger.info("index_skipped_no_cluster", extra={"profile_id": profile_id})
        return

    client = get_search_client()

    # Checked on every event rather than once per process. Indexing runs in a
    # consumer, not the request path, so the extra HEAD is cheap — and it is
    # what stops OpenSearch from auto-creating the index with *inferred*
    # mappings if it ever goes missing, which would silently replace the
    # analyzers and boosts with guesses.
    client.ensure_index(body=INDEX_BODY)

    client.index_document(
        doc_id=profile_id,
        document=build_document(
            bio=bio,
            instruments=instruments,
            genres=genres,
            city=city,
            country=country,
            is_available=is_available,
        ),
    )
    logger.info("profile_indexed", extra={"profile_id": profile_id})


def remove_profile(*, profile_id: str) -> None:
    """
    Drop a profile from the index.

    The index is a separate store with no foreign key into it, so nothing
    cascades — removal has to be an explicit event. A stale document here would
    keep surfacing a profile that no longer exists, and would do so forever.
    """
    if not settings.OPENSEARCH_URL:
        logger.info("remove_skipped_no_cluster", extra={"profile_id": profile_id})
        return

    existed = get_search_client().delete_document(doc_id=profile_id)
    logger.info("profile_removed", extra={"profile_id": profile_id, "existed": existed})
