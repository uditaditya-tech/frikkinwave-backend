from django.contrib import admin

from apps.reviews.models import Review


@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display = ["author", "subject", "rating", "context_type", "created_at"]
    list_filter = ["rating", "context_type"]
    raw_id_fields = ["author", "subject"]
    search_fields = ["author__username", "subject__username", "comment"]
