from django.contrib import admin

from apps.social.models import Activity, FeedEntry, Follow


@admin.register(Follow)
class FollowAdmin(admin.ModelAdmin):
    list_display = ["follower", "followed", "created_at"]
    raw_id_fields = ["follower", "followed"]
    search_fields = ["follower__username", "followed__username"]


@admin.register(Activity)
class ActivityAdmin(admin.ModelAdmin):
    list_display = ["actor", "verb", "summary", "target_type", "created_at"]
    list_filter = ["verb", "target_type"]
    raw_id_fields = ["actor"]
    search_fields = ["actor__username", "summary"]


@admin.register(FeedEntry)
class FeedEntryAdmin(admin.ModelAdmin):
    list_display = ["owner", "activity", "created_at"]
    raw_id_fields = ["owner", "activity"]
    search_fields = ["owner__username"]
