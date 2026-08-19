"""
Carry the existing embeddings across from musicians_profileembedding.

These vectors cost real OpenAI calls to produce (42 rows in production at the
time of writing), so the extraction copies them rather than regenerating. The
copy is plain SQL: the source model is being deleted in the same deploy, so
referring to it through the ORM historical-model machinery would be fragile.

`is_available` is denormalized here for the first time — it is joined in from
the profile row, which is the last time this app reads a musicians table. From
now on it arrives by event.
"""

from typing import Any

from django.db import migrations

COPY = """
INSERT INTO search_profileembedding
    (id, profile_id, embedding, embedding_text, is_available, generated_at)
SELECT
    e.id,
    e.profile_id,
    e.embedding,
    e.embedding_text,
    COALESCE(p.is_available, TRUE),
    e.generated_at
FROM musicians_profileembedding e
JOIN musicians_musicianprofile p ON p.id = e.profile_id
ON CONFLICT (profile_id) DO NOTHING;
"""

# Reversing drops the copies; the source table is restored by unapplying the
# musicians migration that removes it.
UNCOPY = "DELETE FROM search_profileembedding;"


def forwards(apps: Any, schema_editor: Any) -> None:
    # A fresh database (CI, a new environment) has no source table — skip
    # rather than fail. The table only exists where the old app ran.
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SELECT to_regclass('musicians_profileembedding');")
        if cursor.fetchone()[0] is None:
            return
        cursor.execute(COPY)


def backwards(apps: Any, schema_editor: Any) -> None:
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(UNCOPY)


class Migration(migrations.Migration):
    dependencies = [
        ("search", "0001_initial"),
        # The source table must still exist when this runs.
        ("musicians", "0007_musicianprofile_rating_avg_and_more"),
    ]

    operations = [migrations.RunPython(forwards, backwards)]
