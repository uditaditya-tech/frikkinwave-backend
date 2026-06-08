from django.contrib import admin

from apps.social.models import Follow


@admin.register(Follow)
class FollowAdmin(admin.ModelAdmin):
    list_display = ["follower", "followed", "created_at"]
    raw_id_fields = ["follower", "followed"]
    search_fields = ["follower__username", "followed__username"]
