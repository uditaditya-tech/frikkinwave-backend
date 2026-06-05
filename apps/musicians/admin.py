from django.contrib import admin

from apps.musicians.models import (
    CompatibilityBlurb,
    Genre,
    Instrument,
    MusicianInstrument,
    MusicianProfile,
    ProfileEmbedding,
)


@admin.register(Instrument)
class InstrumentAdmin(admin.ModelAdmin):
    list_display = ["name", "slug"]
    prepopulated_fields = {"slug": ("name",)}
    search_fields = ["name"]


@admin.register(Genre)
class GenreAdmin(admin.ModelAdmin):
    list_display = ["name", "slug"]
    prepopulated_fields = {"slug": ("name",)}
    search_fields = ["name"]


@admin.register(MusicianProfile)
class MusicianProfileAdmin(admin.ModelAdmin):
    list_display = ["user", "city", "country", "is_available", "created_at"]
    list_filter = ["is_available", "country"]
    search_fields = ["user__email", "user__username", "city"]


@admin.register(MusicianInstrument)
class MusicianInstrumentAdmin(admin.ModelAdmin):
    list_display = ["profile", "instrument", "proficiency"]
    list_filter = ["proficiency"]


@admin.register(ProfileEmbedding)
class ProfileEmbeddingAdmin(admin.ModelAdmin):
    # The raw 1536-dim vector is unusable in a form, so exclude it; show only
    # the metadata. Embeddings are written by a Celery task, not by hand.
    list_display = ["profile", "generated_at"]
    readonly_fields = ["profile", "embedding_text", "generated_at"]
    exclude = ["embedding"]
    search_fields = ["profile__user__username", "profile__user__email"]


@admin.register(CompatibilityBlurb)
class CompatibilityBlurbAdmin(admin.ModelAdmin):
    list_display = ["profile_a", "profile_b", "generated_at"]
    readonly_fields = ["profile_a", "profile_b", "blurb", "generated_at"]
