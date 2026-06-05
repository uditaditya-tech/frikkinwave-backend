"""
Run matching evals (retrieval quality + blurb grounding) against the real model.

Offline / manual — needs a real OPENAI_API_KEY and makes live API calls (costs a
little). Not run in CI; the deterministic harness test covers the wiring there.

    OPENAI_API_KEY=sk-... python manage.py eval_matching

Prints a JSON report, e.g.:

    {
      "retrieval": {"cases": 7, "recall@1": 1.0, "recall@3": 1.0, "mrr": 1.0},
      "blurbs": {"pairs": 2, "grounding_rate": 1.0}
    }
"""

import json
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.musicians.evals.runner import run_matching_eval


class Command(BaseCommand):
    help = "Run matching evals (retrieval + blurb grounding) against the real OpenAI model."

    def handle(self, *args: Any, **options: Any) -> None:
        if not settings.OPENAI_API_KEY:
            self.stderr.write(
                self.style.ERROR("OPENAI_API_KEY is not set — cannot run real evals.")
            )
            return
        report = run_matching_eval()
        self.stdout.write(json.dumps(report, indent=2))
