"""
Pure metric functions for matching evals (Phase 2.8).

No DB, no OpenAI — deterministic functions over ranked id lists, unit-tested in
CI. `retrieved` is the ranked list of result identifiers (best first);
`relevant` is the set of ids that should have been retrieved.
"""

from __future__ import annotations

from collections.abc import Sequence


def recall_at_k(retrieved: Sequence[str], relevant: Sequence[str], k: int) -> float:
    """Fraction of relevant items found in the top k."""
    relevant_set = set(relevant)
    if not relevant_set:
        return 0.0
    top_k = set(retrieved[:k])
    return len(top_k & relevant_set) / len(relevant_set)


def precision_at_k(retrieved: Sequence[str], relevant: Sequence[str], k: int) -> float:
    """Fraction of the top k that are relevant (denominator = items actually returned)."""
    top_k = retrieved[:k]
    if not top_k:
        return 0.0
    relevant_set = set(relevant)
    return len([r for r in top_k if r in relevant_set]) / len(top_k)


def reciprocal_rank(retrieved: Sequence[str], relevant: Sequence[str]) -> float:
    """1 / rank of the first relevant item (1-indexed), or 0 if none retrieved."""
    relevant_set = set(relevant)
    for index, item in enumerate(retrieved, start=1):
        if item in relevant_set:
            return 1.0 / index
    return 0.0


def mean(values: Sequence[float]) -> float:
    """Mean of a sequence, 0.0 when empty (avoids ZeroDivisionError in reports)."""
    return sum(values) / len(values) if values else 0.0


def blurb_is_grounded(blurb: str, shared_terms: Sequence[str]) -> bool:
    """
    True if the blurb references at least one term the pair actually shares.

    A cheap relevance proxy: a grounded blurb names a real shared instrument,
    genre, or city rather than generic filler. No shared terms → not grounded.
    """
    text = blurb.lower()
    return any(term.lower() in text for term in shared_terms)
