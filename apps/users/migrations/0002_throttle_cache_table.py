"""
Create the table that throttle counters live in.

**A migration rather than `createcachetable`, deliberately.** The command is the
documented way to make this table, but it is a *separate* step from `migrate`,
and a separate step is one that has to be remembered on every fresh database:
the test database pytest builds each run, a CI job, and a rebuilt production
stack after a teardown. The failure mode when it is forgotten is bad and
non-obvious — the table is missing, so every throttled request raises on the
cache lookup and login returns 500 while every probe stays green.

Folding it into `migrate` removes the chance to forget. The Helm `pre-upgrade`
hook already runs migrations before any pod rolls, so a deploy gets the table
with no chart change at all.

**It lives in `users` rather than `events`** even though the cache is the global
`default` alias, because every scope that uses it (`auth_login`,
`auth_register`, `auth_refresh`) throttles a view in `apps/users/views.py`.
Someone debugging a throttle looks here first. If a non-auth scope is ever
added, this is the thing to revisit — a shared cache table owned by one domain
app is a compromise, not a principle.
"""

from django.core.management import call_command
from django.db import migrations

# Must match CACHES["default"]["LOCATION"] in config/settings/base.py.
CACHE_TABLE = "throttle_cache"


def create_cache_table(apps, schema_editor):  # type: ignore[no-untyped-def]
    """
    Build the table with Django's own command so its schema stays correct.

    Hand-writing the columns here would freeze today's layout into a file that
    Django is free to change under us; calling the command means the table is
    whatever this Django version expects. `database=` keeps it on the connection
    the migration is running against rather than the default one, which is what
    makes it land in the test database too.
    """
    call_command(
        "createcachetable",
        CACHE_TABLE,
        database=schema_editor.connection.alias,
        verbosity=0,
    )


def drop_cache_table(apps, schema_editor):  # type: ignore[no-untyped-def]
    """Reverse. Safe to lose: the contents are rate-limit counters, not data."""
    schema_editor.execute(f'DROP TABLE IF EXISTS "{CACHE_TABLE}"')


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(create_cache_table, drop_cache_table),
    ]
