from django.contrib import admin

from apps.musicians.models import Genre, Instrument, MusicianInstrument, MusicianProfile


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
