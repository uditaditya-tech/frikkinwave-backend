"""
Golden dataset for matching evals (Phase 2.8).

A small, curated set of synthetic musician profiles with labeled retrieval cases
(query → expected relevant usernames) and blurb pairs. Used by the eval runner
both for the real, key-backed quality measurement (management command) and for
the deterministic CI harness test (fake embedder).

Retrieval-case queries deliberately contain the profiles' vocabulary terms
(instrument / genre / city) so the deterministic fake embedder — which encodes
token overlap — can reproduce sensible rankings without a real model. Real
embeddings handle the same queries at least as well.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GoldenProfile:
    username: str
    bio: str
    city: str
    country: str
    instruments: tuple[tuple[str, str], ...]  # (name, proficiency)
    genres: tuple[str, ...]


@dataclass(frozen=True)
class RetrievalCase:
    query: str
    relevant: tuple[str, ...]  # expected usernames


GOLDEN_PROFILES: tuple[GoldenProfile, ...] = (
    GoldenProfile(
        "jazz-drummer-mumbai",
        "Jazz drummer chasing tight pockets and brushwork.",
        "Mumbai",
        "India",
        (("Drums", "advanced"),),
        ("Jazz",),
    ),
    GoldenProfile(
        "jazz-bassist-mumbai",
        "Upright and electric bass for jazz combos.",
        "Mumbai",
        "India",
        (("Bass", "advanced"),),
        ("Jazz",),
    ),
    GoldenProfile(
        "jazz-pianist-mumbai",
        "Pianist comping for jazz standards.",
        "Mumbai",
        "India",
        (("Piano", "intermediate"),),
        ("Jazz",),
    ),
    GoldenProfile(
        "rock-guitarist-delhi",
        "Rock guitar, riffs and solos.",
        "Delhi",
        "India",
        (("Guitar", "advanced"),),
        ("Rock",),
    ),
    GoldenProfile(
        "rock-vocalist-delhi",
        "Front-person and vocals for rock bands.",
        "Delhi",
        "India",
        (("Vocals", "advanced"),),
        ("Rock",),
    ),
    GoldenProfile(
        "violinist-chennai",
        "Hindustani violin, ragas and improvisation.",
        "Chennai",
        "India",
        (("Violin", "advanced"),),
        ("Hindustani",),
    ),
    GoldenProfile(
        "flautist-chennai",
        "Hindustani flute for classical sets.",
        "Chennai",
        "India",
        (("Flute", "intermediate"),),
        ("Hindustani",),
    ),
    GoldenProfile(
        "electronic-producer-bengaluru",
        "Electronic producer on synth and machines.",
        "Bengaluru",
        "India",
        (("Synth", "advanced"),),
        ("Electronic",),
    ),
    GoldenProfile(
        "metal-guitarist-pune",
        "Metal guitar, down-tuned and heavy.",
        "Pune",
        "India",
        (("Guitar", "advanced"),),
        ("Metal",),
    ),
    GoldenProfile(
        "folk-guitarist-pune",
        "Folk guitar and fingerstyle.",
        "Pune",
        "India",
        (("Guitar", "intermediate"),),
        ("Folk",),
    ),
)

RETRIEVAL_CASES: tuple[RetrievalCase, ...] = (
    RetrievalCase("jazz drums mumbai", ("jazz-drummer-mumbai",)),
    RetrievalCase("jazz bass mumbai", ("jazz-bassist-mumbai",)),
    RetrievalCase("rock guitar delhi", ("rock-guitarist-delhi",)),
    RetrievalCase("rock vocals delhi", ("rock-vocalist-delhi",)),
    RetrievalCase("violin hindustani chennai", ("violinist-chennai",)),
    RetrievalCase("electronic synth bengaluru", ("electronic-producer-bengaluru",)),
    RetrievalCase(
        "jazz mumbai",
        ("jazz-drummer-mumbai", "jazz-bassist-mumbai", "jazz-pianist-mumbai"),
    ),
)

# Pairs to generate compatibility blurbs for (must share at least one term).
BLURB_PAIRS: tuple[tuple[str, str], ...] = (
    ("jazz-drummer-mumbai", "jazz-bassist-mumbai"),
    ("rock-guitarist-delhi", "rock-vocalist-delhi"),
)


def golden_vocab() -> list[str]:
    """All instrument / genre / city tokens (lowercased), for the fake embedder."""
    terms: set[str] = set()
    for p in GOLDEN_PROFILES:
        terms.update(name.lower() for name, _ in p.instruments)
        terms.update(g.lower() for g in p.genres)
        terms.add(p.city.lower())
    return sorted(terms)


def shared_terms(a: GoldenProfile, b: GoldenProfile) -> list[str]:
    """Instrument/genre/city tokens common to both profiles (for blurb grounding)."""

    def terms(p: GoldenProfile) -> set[str]:
        return (
            {name.lower() for name, _ in p.instruments}
            | {g.lower() for g in p.genres}
            | {p.city.lower()}
        )

    return sorted(terms(a) & terms(b))
