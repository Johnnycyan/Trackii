"""Admin configuration for the API app."""

from django.contrib import admin

from api.models import ScrobbleLog, ScrobbleSession


@admin.register(ScrobbleSession)
class ScrobbleSessionAdmin(admin.ModelAdmin):
    """Admin for ScrobbleSession model."""

    list_display = [
        "id",
        "user",
        "get_title",
        "media_type",
        "state",
        "progress",
        "started_at",
        "updated_at",
    ]
    list_filter = ["state", "media_type", "started_at"]
    search_fields = ["user__username", "title", "item__title"]
    readonly_fields = ["id", "started_at", "updated_at"]
    raw_id_fields = ["user", "item"]

    def get_title(self, obj):
        """Get display title from item or session."""
        return obj.item.title if obj.item else obj.title

    get_title.short_description = "Title"


@admin.register(ScrobbleLog)
class ScrobbleLogAdmin(admin.ModelAdmin):
    """Admin for ScrobbleLog model."""

    list_display = [
        "id",
        "user",
        "title",
        "media_type",
        "action",
        "final_progress",
        "was_scrobbled",
        "ended_at",
    ]
    list_filter = ["was_scrobbled", "media_type", "action", "ended_at"]
    search_fields = ["user__username", "title"]
    readonly_fields = ["id", "started_at", "ended_at"]
    raw_id_fields = ["user", "item"]
