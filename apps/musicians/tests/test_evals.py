"""
Eval harness tests (Phase 2.8).

Two layers:
  - pure metric-function unit tests (no DB, no model);
  - an end-to-end harness test that runs the real eval runner with a deterministic
    fake embedder (token overlap → vector), so retrieval genuinely ranks similar
    profiles together and we can assert sensible metrics — without a key or
    network. The management command swaps in the real client for live numbers.
"""

import pytest
from pytest_django.fixtures import SettingsWrapper

from apps.musicians import services
from apps.musicians.evals import runner
from apps.musicians.evals.golden import golden_vocab
from apps.musicians.evals.metrics import (
    blurb_is_grounded,
    mean,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
)
from apps.musicians.models import EMBEDDING_DIMENSIONS

# ---------------------------------------------------------------------------
# Pure metric functions
# ---------------------------------------------------------------------------


class TestMetrics:
    def test_recall_at_k(self) -> None:
        assert recall_at_k(["a", "b", "c"], ["a", "x"], k=1) == 0.5  # found a of {a,x}
        assert recall_at_k(["a", "b", "c"], ["a", "x"], k=3) == 0.5
        assert recall_at_k(["a", "b"], ["a", "b"], k=2) == 1.0
        assert recall_at_k(["a"], [], k=1) == 0.0  # no relevant → 0

    def test_precision_at_k(self) -> None:
        assert precision_at_k(["a", "b"], ["a"], k=2) == 0.5
        assert precision_at_k(["a", "b"], ["a", "b"], k=2) == 1.0
        assert precision_at_k([], ["a"], k=2) == 0.0

    def test_reciprocal_rank(self) -> None:
        assert reciprocal_rank(["x", "a"], ["a"]) == 0.5
        assert reciprocal_rank(["a", "x"], ["a"]) == 1.0
        assert reciprocal_rank(["x", "y"], ["a"]) == 0.0

    def test_mean(self) -> None:
        assert mean([1.0, 0.0, 0.5]) == 0.5
        assert mean([]) == 0.0

    def test_blurb_is_grounded(self) -> None:
        assert blurb_is_grounded("You both love jazz in Mumbai", ["jazz", "drums"]) is True
        assert blurb_is_grounded("A generic friendly message", ["jazz"]) is False
        assert blurb_is_grounded("", ["jazz"]) is False
        assert blurb_is_grounded("mentions jazz", []) is False  # no shared terms


# ---------------------------------------------------------------------------
# End-to-end harness with a deterministic fake embedder
# ---------------------------------------------------------------------------

_VOCAB = golden_vocab()


class DeterministicClient:
    """embed() encodes token overlap; complete() returns a grounded-for-all blurb."""

    def embed(self, text: str) -> list[float]:
        lowered = text.lower()
        vec = [0.0] * EMBEDDING_DIMENSIONS
        for i, term in enumerate(_VOCAB):
            if term in lowered:
                vec[i] = 1.0
        return vec

    def complete(self, prompt: str) -> str:
        return "You both bring jazz, rock, mumbai, and delhi energy — you'll click."


@pytest.mark.django_db
class TestEvalHarness:
    def test_runner_reports_sensible_metrics(
        self, monkeypatch: pytest.MonkeyPatch, settings: SettingsWrapper
    ) -> None:
        settings.OPENAI_API_KEY = "test-key"
        monkeypatch.setattr(services, "get_openai_client", lambda: DeterministicClient())

        report = runner.run_matching_eval()

        retrieval = report["retrieval"]
        assert retrieval["cases"] == 7
        # Single-relevant cases rank the right profile first; the one multi-relevant
        # case dilutes recall@1, so expect high-but-not-perfect.
        assert retrieval["recall@1"] >= 0.85
        assert retrieval["recall@3"] >= 0.95
        assert retrieval["mrr"] >= 0.95

        blurbs = report["blurbs"]
        assert blurbs["pairs"] == 2
        assert blurbs["grounding_rate"] == 1.0

    def test_runner_rolls_back_seeded_data(
        self, monkeypatch: pytest.MonkeyPatch, settings: SettingsWrapper
    ) -> None:
        from apps.musicians.models import MusicianProfile

        settings.OPENAI_API_KEY = "test-key"
        monkeypatch.setattr(services, "get_openai_client", lambda: DeterministicClient())

        runner.run_matching_eval()

        # The eval wraps everything in a rolled-back transaction.
        assert MusicianProfile.objects.count() == 0
