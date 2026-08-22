"""
A test double for the search client.

Lives in the app rather than in a conftest because two apps now need it: the
search tests, and the musicians rebuild command's tests. The app that defines
the seam is the right owner of the stand-in for it — the same reason
`rest_framework.test` ships beside `rest_framework`.

It records rather than simulates. A fake that tried to answer queries would only
ever prove that the fake works; anything that is a claim about OpenSearch is
tested against a real cluster instead.
"""

from __future__ import annotations

from typing import Any


class FakeSearchClient:
    """Mirrors the SearchClient surface, so tests break if the seam's shape changes."""

    def __init__(self) -> None:
        self.indexed: dict[str, dict[str, Any]] = {}
        self.deleted: list[str] = []
        self.queries: list[dict[str, Any]] = []
        self.delete_queries: list[dict[str, Any]] = []
        self.ensured: int = 0
        self.hits: list[dict[str, Any]] = []
        self.index = "fake-index"

    def ensure_index(self, *, body: dict[str, Any]) -> bool:
        self.ensured += 1
        return True

    def delete_index(self) -> bool:
        self.indexed.clear()
        return True

    def index_document(self, *, doc_id: str, document: dict[str, Any]) -> None:
        self.indexed[doc_id] = document

    def delete_document(self, *, doc_id: str) -> bool:
        self.deleted.append(doc_id)
        return self.indexed.pop(doc_id, None) is not None

    def delete_by_query(self, *, body: dict[str, Any], conflicts: str = "proceed") -> int:
        self.delete_queries.append({"body": body, "conflicts": conflicts})
        return 0

    def search(self, *, body: dict[str, Any]) -> list[dict[str, Any]]:
        self.queries.append(body)
        return self.hits

    def refresh(self) -> None:
        pass
