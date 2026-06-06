"""
Eval runner for matching quality (Phase 2.8).

Seeds the golden profiles, generates their embeddings, runs semantic search for
each labeled case, generates compatibility blurbs for the labeled pairs, and
computes a metrics report. Everything happens inside a transaction that is
rolled back, so the eval never pollutes the database.

The OpenAI calls go through the normal services, which use the patchable
`get_openai_client()` seam: the management command runs against the real model;
the CI harness test patches in a deterministic fake embedder.
"""

from __future__ import annotations

from typing import Any

from django.db import transaction

from apps.musicians.evals.golden import (
    BLURB_PAIRS,
    GOLDEN_PROFILES,
    RETRIEVAL_CASES,
    GoldenProfile,
    shared_terms,
)
from apps.musicians.evals.metrics import (
    blurb_is_grounded,
    mean,
    recall_at_k,
    reciprocal_rank,
)
from apps.musicians.models import Genre, Instrument, MusicianInstrument, MusicianProfile
from apps.musicians.services import (
    generate_profile_embedding,
    get_compatibility_blurb,
    search_profiles,
)
from apps.users.models import User


def run_matching_eval() -> dict[str, Any]:
    """Run the full matching eval and return a metrics report (DB rolled back)."""
    golden_by_username = {p.username: p for p in GOLDEN_PROFILES}

    with transaction.atomic():
        profiles = _seed_profiles()
        for profile in profiles.values():
            generate_profile_embedding(profile_id=str(profile.id))

        recall1: list[float] = []
        recall3: list[float] = []
        rr: list[float] = []
        for case in RETRIEVAL_CASES:
            # threshold=0.0: the eval measures ranking/recall, not the production
            # relevance floor, so it must not drop weak-but-correctly-ranked hits.
            results = search_profiles(query=case.query, limit=10, similarity_threshold=0.0)
            retrieved = [p.user.username for p in results]
            recall1.append(recall_at_k(retrieved, case.relevant, 1))
            recall3.append(recall_at_k(retrieved, case.relevant, 3))
            rr.append(reciprocal_rank(retrieved, case.relevant))

        grounded: list[float] = []
        for username_a, username_b in BLURB_PAIRS:
            blurb = get_compatibility_blurb(
                viewer_profile=profiles[username_a],
                other_profile=profiles[username_b],
            )
            terms = shared_terms(golden_by_username[username_a], golden_by_username[username_b])
            grounded.append(float(blurb_is_grounded(blurb or "", terms)))

        report = {
            "retrieval": {
                "cases": len(RETRIEVAL_CASES),
                "recall@1": round(mean(recall1), 4),
                "recall@3": round(mean(recall3), 4),
                "mrr": round(mean(rr), 4),
            },
            "blurbs": {
                "pairs": len(BLURB_PAIRS),
                "grounding_rate": round(mean(grounded), 4),
            },
        }

        # Never persist eval data — roll back the whole run.
        transaction.set_rollback(True)

    return report


def _seed_profiles() -> dict[str, MusicianProfile]:
    """Create the golden profiles (instruments/genres included) and return them."""
    profiles: dict[str, MusicianProfile] = {}
    for golden in GOLDEN_PROFILES:
        profiles[golden.username] = _create_profile(golden)
    return profiles


def _create_profile(golden: GoldenProfile) -> MusicianProfile:
    user = User.objects.create_user(
        email=f"{golden.username}@eval.local",
        username=golden.username,
        password="eval-not-a-login",
    )
    profile = MusicianProfile.objects.create(
        user=user,
        bio=golden.bio,
        city=golden.city,
        country=golden.country,
    )
    for name, proficiency in golden.instruments:
        instrument, _ = Instrument.objects.get_or_create(name=name, defaults={"slug": name.lower()})
        MusicianInstrument.objects.create(
            profile=profile, instrument=instrument, proficiency=proficiency
        )
    for genre_name in golden.genres:
        genre, _ = Genre.objects.get_or_create(
            name=genre_name, defaults={"slug": genre_name.lower()}
        )
        profile.genres.add(genre)
    return profile
