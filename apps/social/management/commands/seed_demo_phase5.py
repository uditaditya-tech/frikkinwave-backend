"""
Seed namespaced ``demo-*`` data that exercises Phase 5 end to end — the follow
graph, the activity feed, and ratings + reviews — so the (future) frontend has
rich, *paginated* data on every Phase 5 surface.

Design choices (this is tooling, but it still goes through the **service layer**
of every app — no cross-app model writes):

  - **Evenly spread, no hero.** Every demo musician follows every other, so
    ``/following/`` and ``/followers/`` both scroll for *any* demo account. Each
    user posts content (→ feed fan-out) and is reviewed by ~2·neighbours others
    (→ a scrolling reviews list). The frontend can log in as ANY ``demo-*`` user.

  - **Real pipelines, run in-process.** Celery is forced **eager** for this
    process so feed fan-out, follow-backfill, and profile embeddings all complete
    before the command exits (verifiable immediately) instead of flooding the
    prod broker with ~1k async tasks. The prod web/worker tasks are separate
    processes and are unaffected.

  - **No real emails.** Engagements are created through their real send → accept →
    complete flow, which emits notification tasks. We swap in the **dummy email
    backend** for this process so those (eager) tasks never actually send to the
    fake ``demo-*`` addresses.

  - **Namespaced + removable.** Everything hangs off ``demo-NN`` users; ``--reset``
    deletes them (FK cascade wipes their profiles, follows, activities, feed
    entries, engagements, and reviews) and reseeds. The demo graph is kept
    entirely within the ``demo-*`` set, so real profiles (jazzcat, udit94) are
    never touched.

Usage:
    python manage.py seed_demo_phase5 [--count 30] [--neighbours 12] [--reset]
"""

from __future__ import annotations

import random
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.bands.services import create_band
from apps.engagements.services import (
    accept_engagement_request,
    complete_engagement_request,
    send_engagement_request,
)
from apps.listings.services import create_listing
from apps.musicians.services import create_profile, list_genres, list_instruments
from apps.reviews.services import create_review
from apps.social.services import follow_user
from apps.users.models import User

PROFICIENCY_ADVANCED = "advanced"  # MusicianInstrument.Proficiency value (avoid model import)

DEMO_PREFIX = "demo-"
DEMO_PASSWORD = "DemoPass123!"
DEMO_EMAIL_DOMAIN = "demo.invalid"  # reserved TLD → never deliverable

CITIES = [
    ("Mumbai", "India"),
    ("Pune", "India"),
    ("Bengaluru", "India"),
    ("New Delhi", "India"),
    ("Chennai", "India"),
    ("Kolkata", "India"),
    ("Hyderabad", "India"),
    ("Goa", "India"),
]

FIRST_NAMES = [
    "Aria",
    "Kabir",
    "Mira",
    "Dev",
    "Tara",
    "Rohan",
    "Noor",
    "Vivaan",
    "Sana",
    "Arjun",
    "Isha",
    "Reyansh",
    "Anaya",
    "Veer",
    "Diya",
    "Kian",
    "Myra",
    "Aarav",
    "Zoya",
    "Ishaan",
    "Riya",
    "Neel",
    "Avni",
    "Ved",
    "Kyra",
    "Ansh",
    "Pari",
    "Yug",
    "Saira",
    "Om",
]

RATINGS_POOL = [5, 5, 5, 4, 4, 4, 3, 5, 4, 2]  # skewed positive, some spread

REVIEW_COMMENTS = [
    "Tight pocket, showed up prepared, great energy.",
    "Brilliant tone and super easy to work with.",
    "Locked in with the band from the first take.",
    "Creative ideas, professional throughout.",
    "Solid session — would book again.",
    "Great ear, nailed the parts quickly.",
    "",
]


class Command(BaseCommand):
    help = "Seed namespaced demo-* data exercising Phase 5 (follows, feed, reviews)."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--count", type=int, default=30, help="Number of demo musicians.")
        parser.add_argument(
            "--neighbours",
            type=int,
            default=12,
            help="Forward neighbours each user is reviewed-engaged with (~2x reviews each).",
        )
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Delete existing demo-* users (cascades) before seeding.",
        )

    def handle(self, *args: Any, **opts: Any) -> None:
        count: int = opts["count"]
        neighbours: int = min(opts["neighbours"], max(count - 1, 1))
        rng = random.Random(42)  # deterministic spread

        self._make_eager_and_silence_email()

        if opts["reset"]:
            deleted, _ = User.objects.filter(username__startswith=DEMO_PREFIX).delete()
            self.stdout.write(f"Reset: deleted {deleted} demo-* rows (cascade).")
        elif User.objects.filter(username__startswith=DEMO_PREFIX).exists():
            raise CommandError(
                "demo-* users already exist. Re-run with --reset to wipe and reseed."
            )

        instruments = list(list_instruments())
        genres = list(list_genres())

        users = self._create_users_and_profiles(count, instruments, genres, rng)
        self._create_follow_graph(users)
        self._create_activities(users, rng)
        n_eng, n_rev = self._create_engagements_and_reviews(users, neighbours, rng)

        self.stdout.write(self.style.SUCCESS("Demo seed complete."))
        self.stdout.write(
            f"  users={len(users)}  follows={len(users) * (len(users) - 1)}  "
            f"engagements={n_eng}  reviews={n_rev}"
        )
        self.stdout.write(
            f"  sample logins (any works): {users[0].username} / {users[1].username} "
            f"/ {users[2].username}  — password: {DEMO_PASSWORD}"
        )

    # ------------------------------------------------------------------ setup

    def _make_eager_and_silence_email(self) -> None:
        """Run Celery in-process and drop all outgoing mail for THIS process only."""
        from config.celery import app as celery_app

        celery_app.conf.task_always_eager = True
        celery_app.conf.task_eager_propagates = True
        settings.EMAIL_BACKEND = "django.core.mail.backends.dummy.EmailBackend"

    # ----------------------------------------------------------------- users

    def _create_users_and_profiles(
        self,
        count: int,
        instruments: list[Any],
        genres: list[Any],
        rng: random.Random,
    ) -> list[User]:
        users: list[User] = []
        for i in range(count):
            username = f"{DEMO_PREFIX}{i:02d}"
            name = FIRST_NAMES[i % len(FIRST_NAMES)]
            city, country = CITIES[i % len(CITIES)]
            user = User.objects.create_user(
                email=f"{username}@{DEMO_EMAIL_DOMAIN}",
                username=username,
                password=DEMO_PASSWORD,
            )

            picked_instruments = rng.sample(instruments, k=min(2, len(instruments)))
            picked_genres = rng.sample(genres, k=min(2, len(genres)))
            inst_names = (
                ", ".join(ins.name for ins in picked_instruments) or "multi-instrumentalist"
            )
            genre_names = ", ".join(g.name for g in picked_genres) or "many genres"

            data: dict[str, Any] = {
                "bio": (
                    f"{name} — {inst_names} player based in {city}. Into {genre_names}. "
                    f"Available for jams, sessions, and the occasional late-night gig."
                ),
                "city": city,
                "country": country,
                "is_available": True,
                "is_open_to_session_work": True,
                "session_rate": f"₹{(i % 5 + 2) * 1000}/session",
                "instruments": [
                    {"instrument": ins, "proficiency": PROFICIENCY_ADVANCED}
                    for ins in picked_instruments
                ],
                "genres": picked_genres,
            }
            create_profile(user=user, data=data)
            users.append(user)
        self.stdout.write(f"Created {len(users)} demo users + profiles.")
        return users

    # --------------------------------------------------------------- follows

    def _create_follow_graph(self, users: list[User]) -> None:
        """Everyone follows everyone else — dense + even, so both lists scroll."""
        for follower in users:
            for followed in users:
                if follower.pk != followed.pk:
                    follow_user(follower=follower, username=followed.username)
        self.stdout.write(f"Wired {len(users) * (len(users) - 1)} follow edges.")

    # ------------------------------------------------------------ activities

    def _create_activities(self, users: list[User], rng: random.Random) -> None:
        """Each user posts a listing; every 3rd also starts a band → feed fan-out."""
        listing_types = ["gig", "audition"]
        for i, user in enumerate(users):
            city, country = CITIES[i % len(CITIES)]
            create_listing(
                author=user,
                listing_type=rng.choice(listing_types),
                title=f"{rng.choice(['Drummer', 'Bassist', 'Vocalist', 'Keys', 'Guitarist'])} "
                f"wanted in {city}",
                description="Looking to fill a spot for an upcoming set. DM if keen.",
                city=city,
                country=country,
            )
            if i % 3 == 0:
                create_band(
                    owner=user,
                    name=f"{FIRST_NAMES[i % len(FIRST_NAMES)]} & the {city} Collective",
                    city=city,
                    country=country,
                    bio="A rotating cast of local players.",
                )
        self.stdout.write("Posted listings + bands (feed fan-out done eagerly).")

    # -------------------------------------------------------- engagements/reviews

    def _create_engagements_and_reviews(
        self, users: list[User], neighbours: int, rng: random.Random
    ) -> tuple[int, int]:
        """
        For each user i and its next `neighbours` users j (wrapping), run one real
        completed engagement and have BOTH parties review each other. Each user
        ends up with ~2·neighbours reviews received → the reviews list scrolls.
        """
        n = len(users)
        n_eng = 0
        n_rev = 0
        for i in range(n):
            for k in range(1, neighbours + 1):
                j = (i + k) % n
                requester, musician = users[i], users[j]
                engagement = send_engagement_request(
                    requester=requester,
                    musician_username=musician.username,
                    message="Keen to have you on this one.",
                    rate_offer="₹4000",
                )
                eid = str(engagement.id)
                accept_engagement_request(user=musician, engagement_id=eid)
                complete_engagement_request(user=requester, engagement_id=eid)
                n_eng += 1

                # Both directions (bidirectional reviews on one completed engagement).
                create_review(
                    author=requester,
                    subject_username=musician.username,
                    engagement_id=eid,
                    rating=rng.choice(RATINGS_POOL),
                    comment=rng.choice(REVIEW_COMMENTS),
                )
                create_review(
                    author=musician,
                    subject_username=requester.username,
                    engagement_id=eid,
                    rating=rng.choice(RATINGS_POOL),
                    comment=rng.choice(REVIEW_COMMENTS),
                )
                n_rev += 2
        self.stdout.write(f"Created {n_eng} completed engagements + {n_rev} reviews.")
        return n_eng, n_rev
