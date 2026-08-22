"""
The search app has no models.

It used to own `ProfileEmbedding` — a table of pgvector embeddings with a bare
UUID `profile_id` rather than a ForeignKey, precisely so that the app could one
day be moved to a store of its own. That day arrived from an unexpected
direction: the store is now an OpenSearch index rather than another Postgres
table, so there is nothing left for Django's ORM to describe.

The file stays, empty, because the absence is the interesting part. An app in
INSTALLED_APPS with no models is deliberate here, not an oversight — and the
migrations beside it are the record of the table that used to exist and the
migration that dropped it.
"""
