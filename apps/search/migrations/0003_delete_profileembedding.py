"""
Drop the embedding table and the pgvector extension with it.

**This is the irreversible step of the OpenSearch migration.** Up to here the
vectors were merely unused; from here they are gone. They cost real OpenAI calls
to produce and nothing regenerates them, so `migrate search 0002` will give the
table back empty and no further.

Order is load-bearing. The table has to go before the extension, because the
extension owns the `vector` type its column is declared with — reverse the two
and DROP EXTENSION fails on the dependency, taking the whole migration Job with
it and blocking the deploy.
"""

from django.db import migrations

# IF EXISTS on both sides: a database that never had the extension (a fresh CI
# run, which now never creates it) must not fail here, and neither must a rerun.
DROP_VECTOR_EXTENSION = "DROP EXTENSION IF EXISTS vector;"
CREATE_VECTOR_EXTENSION = "CREATE EXTENSION IF NOT EXISTS vector;"


class Migration(migrations.Migration):
    dependencies = [
        ("search", "0002_copy_embeddings_from_musicians"),
    ]

    operations = [
        migrations.DeleteModel(
            name="ProfileEmbedding",
        ),
        migrations.RunSQL(
            sql=DROP_VECTOR_EXTENSION,
            reverse_sql=CREATE_VECTOR_EXTENSION,
        ),
    ]
