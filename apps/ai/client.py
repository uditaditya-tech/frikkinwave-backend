"""
OpenAI client wrapper.

A thin, swappable seam over the OpenAI SDK so the rest of the code never imports
`openai` directly — callers depend on this interface, and tests patch
`get_openai_client` to inject a fake (no network, no API key needed in CI).

Lives in `apps/ai` rather than inside a domain app because two now depend on it:
`apps/search` embeds profiles and queries, while `apps/musicians` still owns the
compatibility blurbs and the profile coach. Whichever one had held it would have
made the other import across a boundary that is meant to become a network.

This package has no models and is not in INSTALLED_APPS — it is infrastructure,
the same category as `apps/events`.
"""

from __future__ import annotations

import logging
from functools import lru_cache

from django.conf import settings

logger = logging.getLogger(__name__)


class OpenAIUnavailableError(Exception):
    """
    An OpenAI API call failed (quota exhausted, rate limit, timeout, outage).

    Raised by OpenAIClient so callers can degrade gracefully without importing
    the `openai` SDK's exception types. Lets the rest of the code treat "no key"
    and "API down" the same way.
    """


class OpenAIClient:
    """Wraps the OpenAI SDK. One method per capability we use."""

    def __init__(self, api_key: str, embedding_model: str, chat_model: str) -> None:
        # Imported lazily so the SDK is only required when a client is built —
        # tests that patch get_openai_client never import it.
        from openai import OpenAI

        self._client = OpenAI(api_key=api_key)
        self._embedding_model = embedding_model
        self._chat_model = chat_model

    def embed(self, text: str) -> list[float]:
        """Return the embedding vector for `text` (text-embedding-3-small → 1536 dims)."""
        from openai import OpenAIError

        try:
            response = self._client.embeddings.create(model=self._embedding_model, input=text)
        except OpenAIError as exc:
            raise OpenAIUnavailableError(str(exc)) from exc
        return response.data[0].embedding

    def complete(self, prompt: str) -> str:
        """Return a chat completion for `prompt` (gpt-4o-mini)."""
        from openai import OpenAIError

        try:
            response = self._client.chat.completions.create(
                model=self._chat_model,
                messages=[{"role": "user", "content": prompt}],
            )
        except OpenAIError as exc:
            raise OpenAIUnavailableError(str(exc)) from exc
        return (response.choices[0].message.content or "").strip()


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
        chat_model=settings.OPENAI_CHAT_MODEL,
    )
