"""
Drop the cached compatibility blurbs.

These were gpt-4o-mini output, cached per unordered profile pair so a blurb was
paid for once and read from either side. With the AI work removed there is
nothing to regenerate them from, so this is a one-way deletion — reversing gives
back an empty table.

The unique constraint goes first: dropping a table with a named constraint still
attached is fine in Postgres, but Django's state has to shed it in the same
order it was built or the reverse migration cannot be described.
"""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("musicians", "0008_remove_profileembedding_profile_embedding_hnsw_and_more"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="compatibilityblurb",
            name="unique_compatibility_pair",
        ),
        migrations.DeleteModel(
            name="CompatibilityBlurb",
        ),
    ]
