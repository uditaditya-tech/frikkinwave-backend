"""
Kafka subscriptions for the reviews app (KAFKA.md stage 4).

See apps/search/consumers.py for why these live per-app rather than in a central
table.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from apps.reviews import services


def propagate_profile_rating(**payload: Any) -> None:
    """Recompute the subject's rating rollup and push it onto their profile."""
    services.propagate_rating_to_profile(subject_user_id=payload["subject_user_id"])


SUBSCRIPTIONS: dict[str, Callable[..., None]] = {
    "review.created": propagate_profile_rating,
}
