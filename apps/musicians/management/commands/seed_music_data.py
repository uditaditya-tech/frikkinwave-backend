"""
Management command: seed_music_data

Seeds Instrument and Genre lookup tables with an initial dataset.
Idempotent — safe to run multiple times (uses get_or_create).

Usage:
    python manage.py seed_music_data
    python manage.py seed_music_data --clear   # wipe and re-seed
"""

import argparse
import logging

from django.core.management.base import BaseCommand
from django.utils.text import slugify

from apps.musicians.models import Genre, Instrument

logger = logging.getLogger(__name__)

INSTRUMENTS: list[str] = [
    # Strings — fretted
    "Electric Guitar",
    "Acoustic Guitar",
    "Bass Guitar",
    "Classical Guitar",
    "Banjo",
    "Mandolin",
    "Ukulele",
    # Strings — bowed
    "Violin",
    "Viola",
    "Cello",
    "Double Bass",
    # Strings — plucked / other
    "Harp",
    "Sitar",
    "Sarod",
    "Veena",
    # Keyboards
    "Piano",
    "Keyboard / Synthesizer",
    "Organ",
    "Harmonium",
    # Percussion
    "Drums",
    "Cajon",
    "Tabla",
    "Mridangam",
    "Congas",
    "Djembe",
    "Xylophone / Marimba",
    # Brass
    "Trumpet",
    "Trombone",
    "French Horn",
    "Tuba",
    "Flugelhorn",
    # Woodwinds
    "Alto Saxophone",
    "Tenor Saxophone",
    "Soprano Saxophone",
    "Baritone Saxophone",
    "Flute",
    "Clarinet",
    "Oboe",
    "Bassoon",
    "Harmonica",
    # Voice & electronic
    "Vocals",
    "DJ / Turntables",
    "Accordion",
    "Bagpipes",
]

GENRES: list[str] = [
    # Western rock / metal
    "Rock",
    "Hard Rock",
    "Heavy Metal",
    "Punk",
    "Indie",
    "Progressive Rock",
    # Jazz / blues / soul
    "Jazz",
    "Blues",
    "R&B / Soul",
    "Funk",
    "Gospel",
    # Classical
    "Classical",
    "Chamber Music",
    # Electronic
    "Electronic / EDM",
    "Ambient",
    "Experimental",
    # Pop / hip-hop
    "Pop",
    "Hip-Hop",
    "K-Pop",
    # Country / folk
    "Country",
    "Folk",
    # Indian classical & film
    "Carnatic Classical",
    "Hindustani Classical",
    "Bollywood",
    # World
    "Reggae",
    "Ska",
    "Latin",
    "Flamenco",
    "Afrobeats",
    "Fusion",
    "World Music",
]


class Command(BaseCommand):
    help = "Seed Instrument and Genre lookup tables."

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--clear",
            action="store_true",
            help="Delete all existing instruments and genres before seeding.",
        )

    def handle(self, *args: object, **options: object) -> None:
        if options["clear"]:
            Instrument.objects.all().delete()
            Genre.objects.all().delete()
            self.stdout.write(self.style.WARNING("Cleared all instruments and genres."))

        created_instruments = 0
        for name in INSTRUMENTS:
            _, created = Instrument.objects.get_or_create(
                name=name,
                defaults={"slug": slugify(name)},
            )
            if created:
                created_instruments += 1

        created_genres = 0
        for name in GENRES:
            _, created = Genre.objects.get_or_create(
                name=name,
                defaults={"slug": slugify(name)},
            )
            if created:
                created_genres += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Done. Created {created_instruments} instruments, {created_genres} genres."
            )
        )
        logger.info(
            "seed_music_data_complete",
            extra={"instruments_created": created_instruments, "genres_created": created_genres},
        )
