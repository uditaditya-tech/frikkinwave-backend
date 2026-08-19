"""
Models for the search app.

The one thing to notice: `profile_id` is a bare UUID, **not** a ForeignKey.

A ForeignKey is a promise that both rows live in the same database. Search is
meant to become its own service with its own store, so the promise has to go
before the deploy does — a FK is exactly the coupling that makes an extraction
impossible to finish. Losing it costs the cascade delete and referential
integrity, which is why `is_available` is replicated here and stale rows are
pruned on the `profile.deleted` path rather than by the database.
"""

from __future__ import annotations

import uuid

import uuid6
from django.db import models
from pgvector.django import HnswIndex, VectorField

# text-embedding-3-small. Changing this needs a migration AND a re-embed of
# every row — vectors of different dimensions are not comparable.
EMBEDDING_DIMENSIONS = 1536


def _new_uuid() -> uuid.UUID:
    return uuid6.uuid7()


class ProfileEmbedding(models.Model):
    """
    A profile's vector embedding plus the minimum needed to filter on it.

    `is_available` is a REPLICA of the field on MusicianProfile. It lives here
    because the availability filter and the nearest-neighbour scan have to run
    in the same query — filtering after the fact would silently shrink a
    caller's requested limit (ask for 20, drop half, return 9).

    The cost of that replica is honest eventual consistency: between a profile
    toggling availability and the resulting event being relayed, search can
    return a stale answer. Acceptable for a discovery feed; it would not be for
    anything transactional.
    """

    id = models.UUIDField(primary_key=True, default=_new_uuid, editable=False)

    # Deliberately not a ForeignKey — see the module docstring.
    profile_id = models.UUIDField(unique=True, db_index=True)

    embedding = VectorField(dimensions=EMBEDDING_DIMENSIONS)
    # The exact text that was embedded. Compared before re-embedding so a save
    # that does not touch embeddable fields costs no OpenAI call.
    embedding_text = models.TextField()

    # Replicated filter field.
    is_available = models.BooleanField(default=True)

    generated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-generated_at"]
        indexes = [
            HnswIndex(
                name="search_embedding_hnsw",
                fields=["embedding"],
                m=16,
                ef_construction=64,
                opclasses=["vector_cosine_ops"],
            ),
        ]

    def __str__(self) -> str:
        return f"ProfileEmbedding({self.profile_id})"
