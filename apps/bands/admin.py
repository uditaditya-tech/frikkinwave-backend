from django.contrib import admin

from apps.bands.models import Band, BandMembership


@admin.register(Band)
class BandAdmin(admin.ModelAdmin):
    list_display = ["name", "slug", "owner", "city", "country", "is_active", "created_at"]
    list_filter = ["is_active", "country"]
    search_fields = ["name", "slug", "bio", "city", "country"]
    prepopulated_fields = {"slug": ("name",)}
    raw_id_fields = ["owner"]


@admin.register(BandMembership)
class BandMembershipAdmin(admin.ModelAdmin):
    list_display = ["band", "member", "role", "status", "created_at"]
    list_filter = ["status"]
    raw_id_fields = ["band", "member"]
