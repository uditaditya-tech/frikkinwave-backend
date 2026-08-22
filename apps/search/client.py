"""
OpenSearch client seam.

The SDK is imported in exactly one module, and its exception types are converted
into a domain error, so nothing else knows what library is underneath. Same shape
as `apps/events/kafka.py` for the Kafka producer, and as the OpenAI client this
replaces.

The seam earns more here than it did there. `apps/search` is an extracted
service, and the promise of that extraction is that whatever answers `search()`
can change without a caller noticing — which is exactly what just happened, from
pgvector to OpenSearch. One module importing the SDK is what keeps that promise
available for the next swap.

**An empty `OPENSEARCH_URL` is a supported state, not a misconfiguration.** It is
how local dev and CI run with no cluster: callers treat "not configured" and
"cluster unreachable" as one case and degrade to an empty result set. Inherited
deliberately from the OpenAI path, where "no key" and "API down" were also one
case — an upstream failure must never 500 a user request.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache
from typing import TYPE_CHECKING, Any

from django.conf import settings

if TYPE_CHECKING:  # pragma: no cover
    from opensearchpy import OpenSearch

logger = logging.getLogger(__name__)


class SearchUnavailableError(RuntimeError):
    """
    The cluster could not be reached, or refused the request.

    A domain error rather than the SDK's, so callers degrade without importing
    `opensearchpy` — the same reason `KafkaUnavailableError` exists. A missing
    URL raises this too, so "never configured" and "died just now" take the same
    branch at every call site instead of only the one someone remembered.
    """


@contextmanager
def _translated(operation: str) -> Iterator[None]:
    """
    Convert any SDK exception into `SearchUnavailableError`.

    Catching `OpenSearchException` covers both halves of what can go wrong:
    transport failures (`ConnectionError`, `ConnectionTimeout`) and cluster
    rejections (`RequestError`) both descend from it. The import is inside the
    function so importing this module never requires the SDK.
    """
    from opensearchpy.exceptions import OpenSearchException

    try:
        yield
    except OpenSearchException as exc:
        raise SearchUnavailableError(f"{operation} failed: {exc}") from exc


class SearchClient:
    """
    Thin wrapper over the OpenSearch SDK. One method per operation we use.

    Takes and returns plain dicts. The query DSL crosses this boundary on
    purpose: it is the query *language*, not the library. Hiding it behind a
    homegrown query builder would survive no SDK swap that mattered, and would
    cost access to every OpenSearch feature the builder had not yet grown.
    """

    def __init__(self, client: OpenSearch, index: str) -> None:
        self._client = client
        self._index = index

    @property
    def index(self) -> str:
        return self._index

    def ensure_index(self, *, body: dict[str, Any]) -> bool:
        """
        Create the index with `body` (settings + mappings) if it is absent.

        Returns True if this call created it. Tolerates losing the race: every
        consumer pod and the reindex command all call this, so two creates
        landing together is ordinary rather than exceptional, and the loser gets
        `resource_already_exists_exception` back. Treating that as success is
        what makes the operation idempotent.
        """
        from opensearchpy.exceptions import RequestError

        with _translated("index exists check"):
            if self._client.indices.exists(index=self._index):
                return False

        try:
            with _translated("index create"):
                self._client.indices.create(index=self._index, body=body)
        except SearchUnavailableError as exc:
            cause = exc.__cause__
            if (
                isinstance(cause, RequestError)
                and cause.error == "resource_already_exists_exception"
            ):
                logger.info("search_index_already_exists", extra={"index": self._index})
                return False
            raise

        logger.info("search_index_created", extra={"index": self._index})
        return True

    def index_document(self, *, doc_id: str, document: dict[str, Any]) -> None:
        """
        Upsert one document under `doc_id`.

        Indexing by a caller-supplied id is what makes the write idempotent, and
        it has to be: Kafka delivery is at-least-once, so the same
        `profile.updated` event can arrive twice. A generated id would turn each
        redelivery into a duplicate search result.
        """
        with _translated("index document"):
            self._client.index(index=self._index, id=doc_id, body=document)

    def delete_document(self, *, doc_id: str) -> bool:
        """
        Delete `doc_id`. Returns False if it was not there.

        A missing document is a success, not an error — a delete replayed after
        it already applied must be a no-op for the same at-least-once reason.
        """
        from opensearchpy.exceptions import NotFoundError

        try:
            with _translated("delete document"):
                self._client.delete(index=self._index, id=doc_id)
        except SearchUnavailableError as exc:
            if isinstance(exc.__cause__, NotFoundError):
                return False
            raise
        return True

    def search(self, *, body: dict[str, Any]) -> list[dict[str, Any]]:
        """
        Run a query and return the raw hit dicts (`_id`, `_score`, `_source`).

        Returns hits rather than ids so the caller decides what a score means;
        this layer stays transport.
        """
        with _translated("search"):
            response = self._client.search(index=self._index, body=body)
        hits: list[dict[str, Any]] = response.get("hits", {}).get("hits", [])
        return hits

    def refresh(self) -> None:
        """
        Force pending writes to become visible to search.

        Indexing is near-real-time, not real-time — a document is searchable
        after the next refresh (1s by default), which is fine in production and
        useless in a test that indexes and immediately asserts. Called by tests
        and by the reindex command, never by the request path.
        """
        with _translated("refresh"):
            self._client.indices.refresh(index=self._index)


@lru_cache(maxsize=1)
def get_search_client() -> SearchClient:
    """
    Return a process-wide `SearchClient` built from settings.

    Cached so the underlying connection pool is reused across requests. Tests
    patch this getter to inject a fake, the same way they patched
    `get_openai_client` — which is also why the cache is not a problem for
    `override_settings`: nothing re-reads settings through it under test.
    """
    if not settings.OPENSEARCH_URL:
        raise SearchUnavailableError("OPENSEARCH_URL is empty — no cluster configured.")

    from opensearchpy import OpenSearch

    client = OpenSearch(
        hosts=[settings.OPENSEARCH_URL],
        # Short and bounded on purpose. `search()` runs synchronously in the
        # request path, so a slow cluster behind a generous timeout stops being
        # a search problem and becomes a worker-exhaustion problem — the pod
        # stops serving every other endpoint too. One retry, so the worst case a
        # request can wait is roughly twice this.
        timeout=settings.OPENSEARCH_TIMEOUT,
        max_retries=1,
        retry_on_timeout=False,
    )
    return SearchClient(client, settings.OPENSEARCH_INDEX)
