from django.contrib import admin

from apps.connections.models import ContactRequest


@admin.register(ContactRequest)
class ContactRequestAdmin(admin.ModelAdmin):
    list_display = ["id", "sender", "recipient", "status", "created_at"]
    list_filter = ["status"]
    search_fields = ["sender__username", "recipient__username"]
    readonly_fields = ["id", "created_at", "updated_at"]
