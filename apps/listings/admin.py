from django.contrib import admin

from apps.listings.models import Listing


@admin.register(Listing)
class ListingAdmin(admin.ModelAdmin):
    list_display = ["title", "listing_type", "author", "city", "country", "is_active", "created_at"]
    list_filter = ["listing_type", "is_active", "is_paid", "country"]
    search_fields = ["title", "description", "city", "country"]
    raw_id_fields = ["author"]
