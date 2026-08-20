"""
Space the relay's retries out.

Retries previously ran at the poll interval — one per second, unspaced — so
MAX_ATTEMPTS was spent in ~10 seconds and any broker outage longer than that
stranded every pending event permanently.

`default=timezone.now` means existing rows are all immediately due, so there is
no backfill and no behaviour change for a healthy outbox. The index is rebuilt
because next_attempt_at is now the relay query's leading filter.
"""

import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("events", "0001_initial"),
    ]

    operations = [
        migrations.RemoveIndex(
            model_name="outboxevent",
            name="outbox_pending_idx",
        ),
        migrations.AddField(
            model_name="outboxevent",
            name="next_attempt_at",
            field=models.DateTimeField(default=django.utils.timezone.now),
        ),
        migrations.AddIndex(
            model_name="outboxevent",
            index=models.Index(
                condition=models.Q(("published_at__isnull", True)),
                fields=["next_attempt_at", "created_at"],
                name="outbox_pending_idx",
            ),
        ),
    ]
