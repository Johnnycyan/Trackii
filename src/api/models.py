"""Models for the scrobbler API."""

import uuid

from django.conf import settings
from django.db import models

from app.models import Item, MediaTypes


class ScrobbleState(models.TextChoices):
    """Choices for scrobble session state."""

    WATCHING = "watching", "Watching"
    PAUSED = "paused", "Paused"
    STOPPED = "stopped", "Stopped"


class ScrobbleSession(models.Model):
    """Model to track active scrobble sessions."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="scrobble_sessions",
    )

    # Media info - can link to existing Item or store info for new media
    item = models.ForeignKey(
        Item,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        help_text="Linked Yamtrack item (if found)",
    )

    # Media identification fallback (when item not yet created)
    media_type = models.CharField(
        max_length=10,
        choices=MediaTypes.choices,
        blank=True,
    )
    title = models.CharField(max_length=500, blank=True)
    year = models.PositiveIntegerField(null=True, blank=True)
    season = models.PositiveIntegerField(null=True, blank=True)
    episode = models.PositiveIntegerField(null=True, blank=True)

    # External IDs for matching
    tmdb_id = models.CharField(max_length=20, blank=True)
    imdb_id = models.CharField(max_length=20, blank=True)
    tvdb_id = models.CharField(max_length=20, blank=True)
    mal_id = models.CharField(max_length=20, blank=True)

    # Playback info
    progress = models.FloatField(
        default=0.0,
        help_text="Progress percentage (0-100)",
    )
    duration = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Total duration in seconds",
    )

    # State tracking
    state = models.CharField(
        max_length=20,
        choices=ScrobbleState.choices,
        default=ScrobbleState.WATCHING,
    )
    already_scrobbled = models.BooleanField(
        default=False,
        help_text="Whether this session has already been marked as watched",
    )

    # Client info
    player = models.CharField(max_length=100, blank=True)
    player_version = models.CharField(max_length=50, blank=True)
    client_id = models.CharField(max_length=100, blank=True)

    # Timestamps
    started_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        """Meta options for the model."""

        ordering = ["-started_at"]
        indexes = [
            models.Index(fields=["user", "state"]),
            models.Index(fields=["updated_at"]),
        ]

    def __str__(self):
        """Return string representation."""
        if self.item:
            return f"{self.user.username} - {self.item.title} ({self.state})"
        return f"{self.user.username} - {self.title} ({self.state})"

    @property
    def is_active(self):
        """Check if session is still active (watching or paused)."""
        return self.state in (ScrobbleState.WATCHING, ScrobbleState.PAUSED)


class ScrobbleLog(models.Model):
    """Log of completed scrobbles for history and debugging."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="scrobble_logs",
    )

    # Session reference (optional - may be deleted)
    session_id = models.UUIDField(null=True, blank=True)

    # Media info
    item = models.ForeignKey(
        Item,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    media_type = models.CharField(max_length=10, choices=MediaTypes.choices)
    title = models.CharField(max_length=500)
    season = models.PositiveIntegerField(null=True, blank=True)
    episode = models.PositiveIntegerField(null=True, blank=True)

    # Scrobble result
    action = models.CharField(max_length=20)  # start, pause, stop
    final_progress = models.FloatField()
    was_scrobbled = models.BooleanField(
        default=False,
        help_text="Whether this resulted in marking the item as watched",
    )

    # Timestamps
    started_at = models.DateTimeField()
    ended_at = models.DateTimeField()

    # Client info
    player = models.CharField(max_length=100, blank=True)
    client_id = models.CharField(max_length=100, blank=True)

    class Meta:
        """Meta options for the model."""

        ordering = ["-ended_at"]
        indexes = [
            models.Index(fields=["user", "ended_at"]),
        ]

    def __str__(self):
        """Return string representation."""
        status = "scrobbled" if self.was_scrobbled else "cancelled"
        return f"{self.user.username} - {self.title} ({status})"
