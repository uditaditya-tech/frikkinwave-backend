"""
Service layer for the search app.

The contract with the rest of the system is deliberately narrow:

    search(query=...) -> [(profile_id, similarity), ...]

**IDs and scores, never objects.** Returning `MusicianProfile` instances is what
the old implementation did, and it is precisely what a service boundary cannot
carry: an ORM instance is not serializable across a network, and producing one
requires search to own the profile tables. The caller hydrates from its own
store, in the order given here.

That split is why `is_available` is replicated into this app: the availability
filter has to run inside the same query as the nearest-neighbour scan. Filter
afterwards and a caller asking for 20 results silently gets 9.
"""

from __future__ import annotations

import logging
import uuid

from django.conf import settings
from pgvector.django import CosineDistance

from apps.ai.client import OpenAIUnavailableError, get_openai_client
from apps.search.models import ProfileEmbedding

logger = logging.getLogger(__name__)

# (profile_id, similarity) — similarity is 0..1, higher is closer.
SearchHit = tuple[uuid.UUID, float]


def search(
    *,
    query: str,
    limit: int = 20,
    available_only: bool = False,
    similarity_threshold: float | None = None,
) -> list[SearchHit]:
    """
    Embed `query` and return the nearest profile ids by cosine distance.

    Degrades to `[]` rather than raising when AI is unavailable — no key, quota
    exhausted, upstream down are all treated identically. An upstream failure
    must never 500 a user request; an empty result set is a survivable answer.

    `similarity_threshold` is a floor in 0..1; pass 0.0 to disable it (the eval
    harness does, so it measures ranking rather than the gate).
    """
    if not settings.OPENAI_API_KEY:
        logger.warning("search_skipped_no_api_key")
        return []

    try:
        query_vector = get_openai_client().embed(query)
    except OpenAIUnavailableError:
        logger.warning("search_skipped_openai_unavailable")
        return []

    queryset = ProfileEmbedding.objects.annotate(
        distance=CosineDistance("embedding", query_vector)
    ).order_by("distance")

    if available_only:
        queryset = queryset.filter(is_available=True)

    # similarity = 1 - distance, so a floor of T keeps distance <= 1 - T.
    threshold = (
        settings.SEARCH_SIMILARITY_THRESHOLD
        if similarity_threshold is None
        else similarity_threshold
    )
    if threshold > 0:
        queryset = queryset.filter(distance__lte=1.0 - threshold)

    hits: list[SearchHit] = [
        (row.profile_id, 1.0 - float(row.distance)) for row in queryset.only("profile_id")[:limit]
    ]
    logger.info(
        "profiles_searched",
        extra={"result_count": len(hits), "limit": limit, "threshold": threshold},
    )
    return hits


def index_profile(*, profile_id: str, embedding_text: str, is_available: bool) -> None:
    """
    Create or refresh a profile's embedding from a self-contained payload.

    Takes the already-composed text rather than a profile id to read: this app
    must not know how a profile turns into text, and must not touch the
    musicians tables to find out. `build_embedding_text` stays with the data it
    describes; only its output crosses.

    Skips the OpenAI call when the text is unchanged, so toggling availability
    or re-saving an untouched profile costs nothing. That dedupe depends on
    `build_embedding_text` being deterministic — keep it that way.
    """
    if not settings.OPENAI_API_KEY:
        logger.info("embedding_skipped_no_api_key", extra={"profile_id": profile_id})
        return

    existing = ProfileEmbedding.objects.filter(profile_id=profile_id).first()

    if existing is not None and existing.embedding_text == embedding_text:
        # Text unchanged — but availability may not be, and it is a filter field.
        if existing.is_available != is_available:
            ProfileEmbedding.objects.filter(pk=existing.pk).update(is_available=is_available)
            logger.info("embedding_availability_updated", extra={"profile_id": profile_id})
        else:
            logger.info("embedding_skipped_unchanged", extra={"profile_id": profile_id})
        return

    if not embedding_text:
        logger.info("embedding_skipped_empty_text", extra={"profile_id": profile_id})
        return

    vector = get_openai_client().embed(embedding_text)

    ProfileEmbedding.objects.update_or_create(
        profile_id=profile_id,
        defaults={
            "embedding": vector,
            "embedding_text": embedding_text,
            "is_available": is_available,
        },
    )
    logger.info("embedding_generated", extra={"profile_id": profile_id})


def remove_profile(*, profile_id: str) -> None:
    """
    Drop a profile from the index.

    Without a ForeignKey there is no cascade delete, so removal is an explicit
    operation. A stale row here would keep surfacing a profile that no longer
    exists.
    """
    deleted, _ = ProfileEmbedding.objects.filter(profile_id=profile_id).delete()
    logger.info("embedding_removed", extra={"profile_id": profile_id, "deleted": deleted})
