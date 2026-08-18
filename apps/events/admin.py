from django.contrib import admin

from apps.events.models import OutboxEvent


@admin.register(OutboxEvent)
class OutboxEventAdmin(admin.ModelAdmin):
    list_display = ["topic", "created_at", "published_at", "attempts"]
    list_filter = ["topic", "published_at"]
    search_fields = ["topic", "last_error"]
    readonly_fields = ["id", "topic", "payload", "created_at"]
