"""
OpenAI client wrapper for the musicians app.

A thin, swappable seam over the OpenAI SDK so the rest of the code never imports
`openai` directly — services depend on this interface, and tests patch
`get_openai_client` to inject a fake (no network, no API key needed in CI).

All Phase 2 AI features (embeddings now; compatibility blurbs and the profile
coach later) live in the musicians app, so the client lives here for now. If AI
is extracted into its own service, this module moves with it.
"""

from __future__ import annotations

import logging
from functools import lru_cache

from django.conf import settings

logger = logging.getLogger(__name__)


class OpenAIClient:
    """Wraps the OpenAI SDK. One method per capability we use."""

    def __init__(self, api_key: str, embedding_model: str) -> None:
        # Imported lazily so the SDK is only required when a client is built —
        # tests that patch get_openai_client never import it.
        from openai import OpenAI

        self._client = OpenAI(api_key=api_key)
        self._embedding_model = embedding_model

    def embed(self, text: str) -> list[float]:
        """Return the embedding vector for `text` (text-embedding-3-small → 1536 dims)."""
        response = self._client.embeddings.create(model=self._embedding_model, input=text)
        return response.data[0].embedding


@lru_cache(maxsize=1)
def get_openai_client() -> OpenAIClient:
    """
    Return a process-wide OpenAIClient built from settings.

    Cached so the underlying HTTP client is reused. Only call this when
    settings.OPENAI_API_KEY is set — callers guard on that and skip otherwise.
    """
    return OpenAIClient(
        api_key=settings.OPENAI_API_KEY,
        embedding_model=settings.OPENAI_EMBEDDING_MODEL,
    )
