from django.contrib import admin

from apps.engagements.models import EngagementRequest


@admin.register(EngagementRequest)
class EngagementRequestAdmin(admin.ModelAdmin):
    list_display = ["requester", "musician", "status", "proposed_date", "created_at"]
    list_filter = ["status"]
    raw_id_fields = ["requester", "musician"]
