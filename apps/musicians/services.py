"""
Service layer for the musicians app.

All business logic lives here. Views call services; services call models.
apps.users.models.User is imported only under TYPE_CHECKING — no runtime coupling.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from django.db.models import QuerySet

    from apps.users.models import User

from apps.musicians.models import (
    Genre,
    Instrument,
    MusicianInstrument,
    MusicianProfile,
)

logger = logging.getLogger(__name__)


class ProfileAlreadyExistsError(Exception):
    """Raised when a user tries to create a second MusicianProfile."""


def list_instruments() -> QuerySet[Instrument]:
    """Return all instruments, name-ordered (Instrument.Meta.ordering)."""
    return Instrument.objects.all()


def list_genres() -> QuerySet[Genre]:
    """Return all genres, name-ordered (Genre.Meta.ordering)."""
    return Genre.objects.all()


def create_profile(*, user: User, data: dict[str, Any]) -> MusicianProfile:
    """
    Create a MusicianProfile for the given user.

    `data` is the validated output of MusicianProfileWriteSerializer.
    Raises ProfileAlreadyExistsError if the user already has a profile.
    """
    if MusicianProfile.objects.filter(user=user).exists():
        raise ProfileAlreadyExistsError

    profile = MusicianProfile.objects.create(
        user=user,
        bio=data.get("bio", ""),
        city=data.get("city", ""),
        country=data.get("country", ""),
        is_available=data.get("is_available", True),
        sound_url=data.get("sound_url", ""),
        is_open_to_session_work=data.get("is_open_to_session_work", False),
        session_rate=data.get("session_rate", ""),
    )

    _set_instruments(profile, data.get("instruments", []))
    _set_genres(profile, data.get("genres", []))

    _enqueue_index(profile)

    logger.info("profile_created", extra={"profile_id": str(profile.id), "user_id": str(user.pk)})
    return profile


def update_profile(*, profile: MusicianProfile, data: dict[str, Any]) -> MusicianProfile:
    """
    Partially update a MusicianProfile.

    Only keys present in `data` are updated — absent keys are left untouched.
    `data` is the validated output of MusicianProfileWriteSerializer (partial=True).
    """
    scalar_fields = (
        "bio",
        "city",
        "country",
        "is_available",
        "sound_url",
        "is_open_to_session_work",
        "session_rate",
    )
    changed = False
    for field in scalar_fields:
        if field in data:
            setattr(profile, field, data[field])
            changed = True
    if changed:
        profile.save()

    if "instruments" in data:
        _set_instruments(profile, data["instruments"])

    if "genres" in data:
        _set_genres(profile, data["genres"])

    _enqueue_index(profile)

    logger.info("profile_updated", extra={"profile_id": str(profile.id)})
    return profile


def set_profile_rating(*, user_id: str, average_rating: float | None, count: int) -> bool:
    """
    Write the denormalized review rollup onto a user's profile.

    Public cross-app *write* entry point: the reviews app pushes its freshly
    computed aggregate here after a review commits, so rendering a profile never
    has to query the reviews tables. Returns False when the user has no profile
    (a normal case -- reviews can exist for users without a musician profile).

    Uses .update() rather than save() so it is a single UPDATE, cannot race with
    a concurrent profile edit, and does not bump `updated_at`.
    """
    updated = MusicianProfile.objects.filter(user_id=user_id).update(
        rating_avg=average_rating,
        rating_count=count,
    )
    if updated:
        logger.info(
            "profile_rating_updated",
            extra={"user_id": str(user_id), "avg": average_rating, "count": count},
        )
    return bool(updated)


def list_profiles(*, filters: dict[str, Any]) -> QuerySet[MusicianProfile]:
    """
    Return the public discovery queryset, narrowed by the provided filters.

    All filter keys are optional and combinable. Only keys present in `filters`
    are applied. Recognised keys:
      - city      → case-insensitive exact match
      - country   → case-insensitive exact match
      - instrument→ instrument slug
      - genre     → genre slug
      - available → True restricts to is_available=True; any other value is ignored
      - open_to_session → True restricts to is_open_to_session_work=True

    Ordering is left to the caller's paginator (CursorPagination orders by
    -created_at). The queryset prefetches related rows to keep the nested
    serializer free of N+1 queries.
    """
    queryset = MusicianProfile.objects.select_related("user").prefetch_related(
        "musician_instruments__instrument",
        "genres",
    )

    if city := filters.get("city"):
        queryset = queryset.filter(city__iexact=city)
    if country := filters.get("country"):
        queryset = queryset.filter(country__iexact=country)
    if instrument := filters.get("instrument"):
        queryset = queryset.filter(instruments__slug=instrument)
    if genre := filters.get("genre"):
        queryset = queryset.filter(genres__slug=genre)
    if filters.get("available") is True:
        queryset = queryset.filter(is_available=True)
    if filters.get("open_to_session") is True:
        queryset = queryset.filter(is_open_to_session_work=True)

    # M2M filters can duplicate rows across joins.
    queryset = queryset.distinct()

    logger.info("profiles_listed", extra={"filter_keys": sorted(filters.keys())})
    return queryset


def search_profiles(
    *,
    query: str,
    limit: int = 20,
    available_only: bool = False,
) -> list[MusicianProfile]:
    """
    Full-text search, best match first.

    The matching belongs to the search service: it returns ids and scores, and
    this hydrates them from the profile tables it owns. That split is the whole
    point — an ORM instance cannot cross a service boundary — and it is what let
    the backend change from pgvector to OpenSearch without this function's
    contract moving at all.

    Each returned profile carries a `score` attribute. It is a BM25 relevance
    score: use it to order results, never to threshold them, because its scale
    depends on the query. Returns [] when search is unavailable, so the feed
    degrades rather than erroring.
    """
    from apps.search import services as search_services

    hits = search_services.search(
        query=query,
        limit=limit,
        available_only=available_only,
    )
    if not hits:
        return []

    scores = dict(hits)
    profiles = (
        MusicianProfile.objects.filter(id__in=list(scores))
        .select_related("user")
        .prefetch_related("musician_instruments__instrument", "genres")
    )
    by_id = {profile.id: profile for profile in profiles}

    results: list[MusicianProfile] = []
    for profile_id, score in hits:
        profile = by_id.get(profile_id)
        if profile is None:
            # The index outlived the profile. Without a FK there is no cascade
            # delete, so this is a real state rather than an impossible one —
            # skip it and let the pruning path catch up.
            logger.warning("search_hit_missing_profile", extra={"profile_id": str(profile_id)})
            continue
        profile.score = score  # type: ignore[attr-defined]
        results.append(profile)
    return results


def get_public_profile(*, username: str) -> MusicianProfile | None:
    """
    Return a single public profile by its owner's username, or None if absent.

    Username match is case-insensitive. Related rows are prefetched so the
    nested serializer stays free of N+1 queries.
    """
    profile = (
        MusicianProfile.objects.select_related("user")
        .prefetch_related(
            "musician_instruments__instrument",
            "genres",
        )
        .filter(user__username__iexact=username)
        .first()
    )
    logger.info("public_profile_viewed", extra={"username": username, "found": profile is not None})
    return profile


# ---------------------------------------------------------------------------
# Profile coach (2.7)
# ---------------------------------------------------------------------------

# Minimum bio length (chars) to count the bio as "complete" for scoring.
_BIO_MIN_LENGTH = 30

# (field, weight, message-when-missing). Weights sum to 100.
_COMPLETENESS_RULES = [
    ("bio", 30, "Add a bio describing your style and influences."),
    ("instruments", 25, "Add at least one instrument so others can find you."),
    ("genres", 20, "List a few genres you play."),
    ("city", 10, "Add your city for local discovery."),
    ("sound_url", 10, "Link a track so people can hear you."),
    ("country", 5, "Add your country."),
]


def coach_profile(*, profile: MusicianProfile) -> dict[str, Any]:
    """
    Evaluate a profile's completeness and return actionable suggestions.

    A deterministic completeness score (0-100) plus structured per-field
    suggestions. There used to be an LLM `tip` alongside these; it is gone with
    the rest of the AI work, and the response no longer carries the key.

    What remains is the half that was always doing the work. The rules below are
    specific, ordered by weight, and identical for the same input — which is
    more than the generated tip could claim, and costs nothing to produce.
    """
    score = 0
    suggestions: list[dict[str, str]] = []
    for field, weight, message in _COMPLETENESS_RULES:
        if _field_is_complete(profile, field):
            score += weight
        else:
            suggestions.append({"field": field, "message": message})

    logger.info(
        "profile_coached",
        extra={"profile_id": str(profile.id), "completeness": score},
    )
    return {"completeness": score, "suggestions": suggestions}


def _field_is_complete(profile: MusicianProfile, field: str) -> bool:
    if field == "bio":
        return len(profile.bio.strip()) >= _BIO_MIN_LENGTH
    if field == "instruments":
        return profile.musician_instruments.exists()
    if field == "genres":
        return profile.genres.exists()
    # Remaining fields are plain CharFields — non-blank means complete.
    return bool(getattr(profile, field, "").strip())


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _set_instruments(
    profile: MusicianProfile,
    instruments: list[dict[str, Any]],
) -> None:
    """Replace all instruments on a profile."""
    profile.musician_instruments.all().delete()
    MusicianInstrument.objects.bulk_create(
        [
            MusicianInstrument(
                profile=profile,
                instrument=item["instrument"],
                proficiency=item.get("proficiency", MusicianInstrument.Proficiency.INTERMEDIATE),
            )
            for item in instruments
        ]
    )


def _set_genres(profile: MusicianProfile, genres: list[Genre]) -> None:
    """Replace all genres on a profile."""
    profile.genres.set(genres)


def _enqueue_index(profile: MusicianProfile) -> None:
    """
    Tell the search service this profile changed, and hand it everything needed
    to index — the facts themselves, never an id to read back. The consumer is a
    separate service; it must not touch these tables.

    The facts go across as separate fields rather than one composed string. That
    is what the move to a text index bought: the consumer can weight an
    instrument match above a passing mention in a bio, which is impossible once
    everything has been blended into a single blob for an embedding.

    Every field here is non-nullable at the model level — `bio` is a TextField
    and `city`/`country` are CharFields, all `blank=True`, so they are `""` and
    never `None`; the two relations yield lists, empty at worst. That check is
    the point, not a formality: building the payload runs in the request path,
    so a `None` sneaking in would 500 the user's own save rather than failing
    harmlessly in a background retry — which is exactly how
    `engagement.proposed_date` broke.

    Written to the transactional outbox inside the caller's transaction, so the
    event and the profile row commit together.
    """
    from apps.events.services import publish

    publish(topic="profile.updated", payload=build_search_payload(profile))


def build_search_payload(profile: MusicianProfile) -> dict[str, Any]:
    """
    The facts the search service needs about a profile.

    Shared by the event path and by `reindex_profiles`, deliberately. They are
    two routes to the same index, and if each composed its own payload they
    could disagree — a rebuild would silently "fix" profiles into a different
    shape than live updates produce, and the difference would only show up as
    search results that change depending on how a profile was last touched.

    Reads prefetched relations; callers that loop should prefetch both.
    """
    return {
        "profile_id": str(profile.id),
        "bio": profile.bio,
        "instruments": [mi.instrument.name for mi in profile.musician_instruments.all()],
        "genres": [genre.name for genre in profile.genres.all()],
        "city": profile.city,
        "country": profile.country,
        "is_available": profile.is_available,
    }
