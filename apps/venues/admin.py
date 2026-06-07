from django.contrib import admin

from apps.venues.models import Venue


@admin.register(Venue)
class VenueAdmin(admin.ModelAdmin):
    list_display = [
        "name",
        "slug",
        "owner",
        "city",
        "country",
        "capacity",
        "is_active",
        "created_at",
    ]
    list_filter = ["is_active", "country"]
    search_fields = ["name", "slug", "description", "city", "country"]
    prepopulated_fields = {"slug": ("name",)}
    raw_id_fields = ["owner"]
