"""Helpers to sync ScrobbleSession state with manual tracking."""

import logging

from django.db.models import Q

from api.models import ScrobbleSession, ScrobbleState
from app.models import Item

logger = logging.getLogger(__name__)


def stop_active_sessions_for_item(user, item):
    """Stop active scrobble sessions for a given user and item.

    Called when a manual action (marking completed, toggling
    episode, deleting tracking) has already accounted for the
    watch, so the real-time session should be closed.

    Args:
        user: The User instance.
        item: The Item instance to match sessions against.
    """
    active_sessions = ScrobbleSession.objects.filter(
        user=user,
        item=item,
        state__in=[ScrobbleState.WATCHING, ScrobbleState.PAUSED],
    )

    count = active_sessions.update(
        state=ScrobbleState.STOPPED,
        already_scrobbled=True,
    )

    if count:
        logger.info(
            "Stopped %d active scrobble session(s) for user %s, "
            "item '%s' (manual action)",
            count,
            user.username,
            item.title,
        )


def stop_active_sessions_for_media(
    user,
    media_type,
    media_id,
    source,
):
    """Stop active sessions matching media metadata.

    Useful when we have media identification info but need to
    match sessions that may or may not have an Item FK set.

    Args:
        user: The User instance.
        media_type: Media type string (e.g. 'movie', 'tv').
        media_id: The external media ID (e.g. TMDB ID).
        source: The source string (e.g. 'tmdb').
    """
    matching_items = Item.objects.filter(
        media_id=media_id,
        source=source,
        media_type=media_type,
    ).values_list("id", flat=True)

    active_sessions = ScrobbleSession.objects.filter(
        user=user,
        state__in=[ScrobbleState.WATCHING, ScrobbleState.PAUSED],
    ).filter(
        Q(item_id__in=matching_items) | Q(media_type=media_type, tmdb_id=media_id),
    )

    count = active_sessions.update(
        state=ScrobbleState.STOPPED,
        already_scrobbled=True,
    )

    if count:
        logger.info(
            "Stopped %d active scrobble session(s) for "
            "user %s, media %s/%s (manual action)",
            count,
            user.username,
            media_type,
            media_id,
        )
