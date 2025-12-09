"""API views for the scrobbler endpoints."""

import json
import logging
from functools import wraps

from django.core.exceptions import ObjectDoesNotExist
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

import app
import users
from api.models import ScrobbleLog, ScrobbleSession, ScrobbleState
from app.models import Item, MediaTypes, Sources, Status

logger = logging.getLogger(__name__)

# Threshold for considering media as "watched" (percentage)
SCROBBLE_THRESHOLD = 80.0


def token_required(view_func):
    """Decorator to require and validate API token from Authorization header."""

    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        auth_header = request.headers.get("Authorization", "")

        if not auth_header.startswith("Bearer "):
            return JsonResponse(
                {
                    "error": "unauthorized",
                    "message": "Missing or invalid Authorization header. Use 'Bearer <token>'",
                },
                status=401,
            )

        token = auth_header[7:]  # Remove "Bearer " prefix

        try:
            user = users.models.User.objects.get(token=token)
            request.user = user
        except ObjectDoesNotExist:
            logger.warning("Invalid API token attempted: %s...", token[:8])
            return JsonResponse(
                {"error": "unauthorized", "message": "Invalid API token"},
                status=401,
            )

        return view_func(request, *args, **kwargs)

    return wrapper


def parse_json_body(request):
    """Parse JSON body from request."""
    try:
        return json.loads(request.body)
    except (json.JSONDecodeError, ValueError) as e:
        logger.warning("Invalid JSON in request body: %s", e)
        return None


@csrf_exempt
@require_GET
@token_required
def auth_test(request):
    """Test if the API token is valid."""
    return JsonResponse(
        {
            "authenticated": True,
            "user": {
                "username": request.user.username,
                "id": request.user.id,
            },
        }
    )


@csrf_exempt
@require_POST
@token_required
def scrobble_start(request):
    """Handle scrobble start event - called when playback begins."""
    data = parse_json_body(request)
    if data is None:
        return JsonResponse(
            {"error": "invalid_request", "message": "Invalid JSON body"},
            status=400,
        )

    user = request.user

    # Check if updating existing session
    scrobble_id = data.get("scrobble_id")
    if scrobble_id:
        try:
            session = ScrobbleSession.objects.get(id=scrobble_id, user=user)
            session.progress = data.get("progress", session.progress)
            session.state = ScrobbleState.WATCHING
            session.save()

            return JsonResponse(
                {
                    "action": "start",
                    "status": "watching",
                    "scrobble_id": str(session.id),
                    "progress": session.progress,
                    "media": _get_session_media_info(session),
                }
            )
        except (ObjectDoesNotExist, ValueError):
            return JsonResponse(
                {"error": "scrobble_not_found", "message": "Invalid scrobble_id"},
                status=404,
            )

    # Validate required fields for new session
    media_type = data.get("media_type")
    if not media_type:
        return JsonResponse(
            {"error": "invalid_request", "message": "media_type is required"},
            status=400,
        )

    # Map media type
    media_type_mapping = {
        "movie": MediaTypes.MOVIE.value,
        "tv": MediaTypes.TV.value,
        "anime": MediaTypes.ANIME.value,
    }
    mapped_type = media_type_mapping.get(media_type.lower())
    if not mapped_type:
        return JsonResponse(
            {
                "error": "invalid_request",
                "message": f"Invalid media_type. Use: {list(media_type_mapping.keys())}",
            },
            status=400,
        )

    # Extract IDs
    ids = data.get("ids", {})

    # Try to find matching media item
    item = _find_media_item(
        mapped_type,
        title=data.get("title"),
        year=data.get("year"),
        season=data.get("season"),
        episode=data.get("episode"),
        tmdb_id=ids.get("tmdb"),
        imdb_id=ids.get("imdb"),
        tvdb_id=ids.get("tvdb"),
        mal_id=ids.get("mal"),
    )

    # Close any existing active sessions for this user
    ScrobbleSession.objects.filter(
        user=user,
        state__in=[ScrobbleState.WATCHING, ScrobbleState.PAUSED],
    ).update(state=ScrobbleState.STOPPED)

    # Create new session
    session = ScrobbleSession.objects.create(
        user=user,
        item=item,
        media_type=mapped_type,
        title=data.get("title", ""),
        year=data.get("year"),
        season=data.get("season"),
        episode=data.get("episode"),
        tmdb_id=str(ids.get("tmdb", "")),
        imdb_id=str(ids.get("imdb", "")),
        tvdb_id=str(ids.get("tvdb", "")),
        mal_id=str(ids.get("mal", "")),
        progress=data.get("progress", 0.0),
        duration=data.get("duration"),
        state=ScrobbleState.WATCHING,
        player=data.get("player", ""),
        player_version=data.get("player_version", ""),
        client_id=data.get("client_id", ""),
    )

    logger.info(
        "Started scrobble session %s for user %s: %s",
        session.id,
        user.username,
        session.title or (item.title if item else "Unknown"),
    )

    response_data = {
        "action": "start",
        "status": "watching",
        "scrobble_id": str(session.id),
        "progress": session.progress,
        "media": _get_session_media_info(session),
    }

    if not item and data.get("title"):
        # Try to find suggestions if we couldn't match
        suggestions = _search_media(
            data.get("title"),
            mapped_type,
            limit=3,
        )
        if suggestions:
            response_data["suggestions"] = suggestions

    return JsonResponse(response_data)


@csrf_exempt
@require_POST
@token_required
def scrobble_pause(request):
    """Handle scrobble pause event - called when playback is paused."""
    data = parse_json_body(request)
    if data is None:
        return JsonResponse(
            {"error": "invalid_request", "message": "Invalid JSON body"},
            status=400,
        )

    user = request.user

    # Find session by ID or by media info
    session = _find_session(user, data)
    if not session:
        return JsonResponse(
            {
                "error": "scrobble_not_found",
                "message": "No active scrobble session found",
            },
            status=404,
        )

    session.progress = data.get("progress", session.progress)
    session.state = ScrobbleState.PAUSED
    session.save()

    logger.info(
        "Paused scrobble session %s for user %s at %.1f%%",
        session.id,
        user.username,
        session.progress,
    )

    return JsonResponse(
        {
            "action": "pause",
            "status": "paused",
            "scrobble_id": str(session.id),
            "progress": session.progress,
        }
    )


@csrf_exempt
@require_POST
@token_required
def scrobble_stop(request):
    """Handle scrobble stop event - called when playback ends."""
    data = parse_json_body(request)
    if data is None:
        return JsonResponse(
            {"error": "invalid_request", "message": "Invalid JSON body"},
            status=400,
        )

    user = request.user

    # Find session by ID or by media info
    session = _find_session(user, data)
    if not session:
        return JsonResponse(
            {
                "error": "scrobble_not_found",
                "message": "No active scrobble session found",
            },
            status=404,
        )

    progress = data.get("progress", session.progress)
    session.progress = progress
    session.state = ScrobbleState.STOPPED
    session.save()

    # Determine if we should mark as watched
    was_scrobbled = progress >= SCROBBLE_THRESHOLD

    if was_scrobbled:
        _mark_as_watched(session, user)

    # Log the scrobble
    ScrobbleLog.objects.create(
        user=user,
        session_id=session.id,
        item=session.item,
        media_type=session.media_type,
        title=session.title or (session.item.title if session.item else "Unknown"),
        season=session.season,
        episode=session.episode,
        action="stop",
        final_progress=progress,
        was_scrobbled=was_scrobbled,
        started_at=session.started_at,
        ended_at=timezone.now(),
        player=session.player,
        client_id=session.client_id,
    )

    status_text = "scrobbled" if was_scrobbled else "cancelled"
    logger.info(
        "Stopped scrobble session %s for user %s at %.1f%% (%s)",
        session.id,
        user.username,
        progress,
        status_text,
    )

    return JsonResponse(
        {
            "action": "stop",
            "status": status_text,
            "progress": progress,
            "watched": was_scrobbled,
            "scrobble_id": str(session.id),
            "media": _get_session_media_info(session),
        }
    )


@csrf_exempt
@require_GET
@token_required
def watching(request):
    """Get currently active watching session."""
    user = request.user

    session = ScrobbleSession.objects.filter(
        user=user,
        state__in=[ScrobbleState.WATCHING, ScrobbleState.PAUSED],
    ).first()

    if not session:
        return JsonResponse({}, status=204)

    return JsonResponse(
        {
            "watching": {
                "scrobble_id": str(session.id),
                "media": _get_session_media_info(session),
                "progress": session.progress,
                "started_at": session.started_at.isoformat(),
                "status": session.state,
            },
        }
    )


@csrf_exempt
@require_GET
@token_required
def search(request):
    """Search for media by title."""
    query = request.GET.get("q", "").strip()
    if not query:
        return JsonResponse(
            {"error": "invalid_request", "message": "Query parameter 'q' is required"},
            status=400,
        )

    media_type = request.GET.get("type")
    year = request.GET.get("year")
    limit = min(int(request.GET.get("limit", 10)), 50)

    # Map media type
    mapped_type = None
    if media_type:
        media_type_mapping = {
            "movie": MediaTypes.MOVIE.value,
            "tv": MediaTypes.TV.value,
            "anime": MediaTypes.ANIME.value,
        }
        mapped_type = media_type_mapping.get(media_type.lower())

    results = _search_media(query, mapped_type, year, limit)

    return JsonResponse(
        {
            "results": results,
            "total": len(results),
        }
    )


@csrf_exempt
@require_GET
@token_required
def lookup(request):
    """Lookup media by external ID."""
    imdb_id = request.GET.get("imdb")
    tmdb_id = request.GET.get("tmdb")
    tvdb_id = request.GET.get("tvdb")
    mal_id = request.GET.get("mal")
    media_type_hint = request.GET.get("type")

    if not any([imdb_id, tmdb_id, tvdb_id, mal_id]):
        return JsonResponse(
            {
                "error": "invalid_request",
                "message": "At least one ID parameter required (imdb, tmdb, tvdb, mal)",
            },
            status=400,
        )

    result = _lookup_by_id(
        imdb_id=imdb_id,
        tmdb_id=tmdb_id,
        tvdb_id=tvdb_id,
        mal_id=mal_id,
        media_type_hint=media_type_hint,
    )

    if not result:
        return JsonResponse(
            {
                "error": "media_not_found",
                "message": "Could not find media with given IDs",
            },
            status=404,
        )

    return JsonResponse(result)


# Helper functions


def _find_session(user, data):
    """Find scrobble session by ID or media info."""
    scrobble_id = data.get("scrobble_id")

    if scrobble_id:
        try:
            return ScrobbleSession.objects.get(
                id=scrobble_id,
                user=user,
                state__in=[ScrobbleState.WATCHING, ScrobbleState.PAUSED],
            )
        except (ObjectDoesNotExist, ValueError):
            pass

    # Try to find by media info
    filters = {
        "user": user,
        "state__in": [ScrobbleState.WATCHING, ScrobbleState.PAUSED],
    }

    if data.get("media_type"):
        media_type_mapping = {
            "movie": MediaTypes.MOVIE.value,
            "tv": MediaTypes.TV.value,
            "anime": MediaTypes.ANIME.value,
        }
        filters["media_type"] = media_type_mapping.get(data["media_type"].lower())

    if data.get("title"):
        filters["title__icontains"] = data["title"]

    if data.get("season"):
        filters["season"] = data["season"]

    if data.get("episode"):
        filters["episode"] = data["episode"]

    return ScrobbleSession.objects.filter(**filters).first()


def _find_media_item(
    media_type,
    title=None,
    year=None,
    season=None,
    episode=None,
    tmdb_id=None,
    imdb_id=None,
    tvdb_id=None,
    mal_id=None,
):
    """Try to find matching Item in database or via provider lookup."""
    # Try direct lookup by external IDs first
    if mal_id and media_type == MediaTypes.ANIME.value:
        item = Item.objects.filter(
            media_id=str(mal_id),
            source=Sources.MAL.value,
            media_type=MediaTypes.ANIME.value,
        ).first()
        if item:
            return item

    if tmdb_id:
        if media_type == MediaTypes.MOVIE.value:
            item = Item.objects.filter(
                media_id=str(tmdb_id),
                source=Sources.TMDB.value,
                media_type=MediaTypes.MOVIE.value,
            ).first()
            if item:
                return item
        elif media_type == MediaTypes.TV.value:
            item = Item.objects.filter(
                media_id=str(tmdb_id),
                source=Sources.TMDB.value,
                media_type=MediaTypes.TV.value,
            ).first()
            if item:
                return item

    # Try IMDB lookup if no TMDB ID
    if imdb_id and not tmdb_id:
        try:
            response = app.providers.tmdb.find(imdb_id, "imdb_id")
            if media_type == MediaTypes.MOVIE.value and response.get("movie_results"):
                tmdb_id = response["movie_results"][0]["id"]
            elif media_type == MediaTypes.TV.value and response.get("tv_results"):
                tmdb_id = response["tv_results"][0]["id"]
        except Exception as e:
            logger.warning("IMDB lookup failed: %s", e)

    # Try TVDB lookup
    if tvdb_id and not tmdb_id and media_type == MediaTypes.TV.value:
        try:
            response = app.providers.tmdb.find(tvdb_id, "tvdb_id")
            if response.get("tv_results"):
                tmdb_id = response["tv_results"][0]["id"]
        except Exception as e:
            logger.warning("TVDB lookup failed: %s", e)

    return None


def _get_session_media_info(session):
    """Get media info dictionary from session."""
    if session.item:
        return {
            "id": session.item.id,
            "media_type": session.item.media_type,
            "title": session.item.title,
            "image": session.item.image,
            "season": session.season,
            "episode": session.episode,
        }

    return {
        "id": None,
        "media_type": session.media_type,
        "title": session.title,
        "image": None,
        "season": session.season,
        "episode": session.episode,
    }


def _mark_as_watched(session, user):
    """Mark the media as watched based on session info."""
    now = timezone.now().replace(second=0, microsecond=0)

    if session.media_type == MediaTypes.MOVIE.value:
        _mark_movie_watched(session, user, now)
    elif session.media_type == MediaTypes.TV.value:
        _mark_tv_watched(session, user, now)
    elif session.media_type == MediaTypes.ANIME.value:
        _mark_anime_watched(session, user, now)


def _mark_movie_watched(session, user, now):
    """Mark movie as watched."""
    if not session.item and session.tmdb_id:
        # Try to create item from TMDB
        try:
            movie_metadata = app.providers.tmdb.movie(int(session.tmdb_id))
            session.item, _ = Item.objects.get_or_create(
                media_id=session.tmdb_id,
                source=Sources.TMDB.value,
                media_type=MediaTypes.MOVIE.value,
                defaults={
                    "title": movie_metadata["title"],
                    "image": movie_metadata["image"],
                },
            )
            session.save()
        except Exception as e:
            logger.warning("Failed to create movie item: %s", e)
            return

    if not session.item:
        logger.warning("Cannot mark movie as watched: no item linked")
        return

    movie_instances = app.models.Movie.objects.filter(item=session.item, user=user)
    current_instance = movie_instances.first()

    if current_instance and current_instance.status != Status.COMPLETED.value:
        current_instance.progress = 1
        current_instance.end_date = now
        current_instance.status = Status.COMPLETED.value
        current_instance.save()
        logger.info("Updated movie to completed: %s", session.item.title)
    elif not current_instance:
        app.models.Movie.objects.create(
            item=session.item,
            user=user,
            progress=1,
            status=Status.COMPLETED.value,
            end_date=now,
        )
        logger.info("Created completed movie: %s", session.item.title)


def _mark_tv_watched(session, user, now):
    """Mark TV episode as watched."""
    if not session.tmdb_id:
        logger.warning("Cannot mark TV as watched: no TMDB ID")
        return

    season_number = session.season
    episode_number = session.episode

    if not season_number or not episode_number:
        logger.warning("Cannot mark TV as watched: missing season/episode")
        return

    try:
        tv_metadata = app.providers.tmdb.tv_with_seasons(
            int(session.tmdb_id),
            [season_number],
        )
        season_metadata = tv_metadata.get(f"season/{season_number}", {})

        tv_item, _ = Item.objects.get_or_create(
            media_id=session.tmdb_id,
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            defaults={
                "title": tv_metadata["title"],
                "image": tv_metadata["image"],
            },
        )

        tv_instance, _ = app.models.TV.objects.get_or_create(
            item=tv_item,
            user=user,
            defaults={"status": Status.IN_PROGRESS.value},
        )

        season_item, _ = Item.objects.get_or_create(
            media_id=session.tmdb_id,
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            season_number=season_number,
            defaults={
                "title": tv_metadata["title"],
                "image": season_metadata.get("image", tv_metadata["image"]),
            },
        )

        season_instance, _ = app.models.Season.objects.get_or_create(
            item=season_item,
            user=user,
            related_tv=tv_instance,
            defaults={"status": Status.IN_PROGRESS.value},
        )

        episode_item = season_instance.get_episode_item(episode_number, season_metadata)

        app.models.Episode.objects.create(
            item=episode_item,
            related_season=season_instance,
            end_date=now,
        )

        logger.info(
            "Marked TV episode as watched: %s S%02dE%02d",
            tv_metadata["title"],
            season_number,
            episode_number,
        )
    except Exception as e:
        logger.error("Failed to mark TV as watched: %s", e)


def _mark_anime_watched(session, user, now):
    """Mark anime episode as watched."""
    if not session.mal_id:
        logger.warning("Cannot mark anime as watched: no MAL ID")
        return

    try:
        anime_metadata = app.providers.mal.anime(int(session.mal_id))
        anime_item, _ = Item.objects.get_or_create(
            media_id=session.mal_id,
            source=Sources.MAL.value,
            media_type=MediaTypes.ANIME.value,
            defaults={
                "title": anime_metadata["title"],
                "image": anime_metadata["image"],
            },
        )

        anime_instances = app.models.Anime.objects.filter(item=anime_item, user=user)
        current_instance = anime_instances.first()

        episode_number = session.episode or 1
        is_completed = episode_number == anime_metadata.get("max_progress")
        status = Status.COMPLETED.value if is_completed else Status.IN_PROGRESS.value

        if current_instance and current_instance.status != Status.COMPLETED.value:
            current_instance.progress = episode_number
            if is_completed:
                current_instance.end_date = now
                current_instance.status = status
            current_instance.save()
            logger.info(
                "Updated anime progress: %s ep %d",
                anime_metadata["title"],
                episode_number,
            )
        elif not current_instance:
            app.models.Anime.objects.create(
                item=anime_item,
                user=user,
                progress=episode_number,
                status=status,
                start_date=now if not is_completed else None,
                end_date=now if is_completed else None,
            )
            logger.info(
                "Created anime entry: %s ep %d", anime_metadata["title"], episode_number
            )
    except Exception as e:
        logger.error("Failed to mark anime as watched: %s", e)


def _search_media(query, media_type=None, year=None, limit=10):
    """Search for media using TMDB/MAL providers."""
    results = []

    # Search TMDB for movies/TV
    if not media_type or media_type in [MediaTypes.MOVIE.value, MediaTypes.TV.value]:
        try:
            tmdb_results = app.providers.tmdb.search(query, page=1)
            for item in tmdb_results.get("results", [])[:limit]:
                result_type = item.get("media_type", "movie")
                if media_type and result_type != media_type:
                    continue

                results.append(
                    {
                        "id": item.get("id"),
                        "media_type": result_type,
                        "title": item.get("title") or item.get("name"),
                        "year": (
                            item.get("release_date") or item.get("first_air_date", "")
                        )[:4],
                        "image": f"https://image.tmdb.org/t/p/w500{item.get('poster_path')}"
                        if item.get("poster_path")
                        else None,
                        "ids": {"tmdb": item.get("id")},
                    }
                )
        except Exception as e:
            logger.warning("TMDB search failed: %s", e)

    # Search MAL for anime
    if not media_type or media_type == MediaTypes.ANIME.value:
        try:
            mal_results = app.providers.mal.search(query, MediaTypes.ANIME.value)
            for item in mal_results[:limit]:
                results.append(
                    {
                        "id": item.get("media_id"),
                        "media_type": MediaTypes.ANIME.value,
                        "title": item.get("title"),
                        "year": item.get("year"),
                        "image": item.get("image"),
                        "ids": {"mal": item.get("media_id")},
                    }
                )
        except Exception as e:
            logger.warning("MAL search failed: %s", e)

    # Filter by year if specified
    if year:
        year_str = str(year)
        results = [r for r in results if r.get("year", "").startswith(year_str)]

    return results[:limit]


def _lookup_by_id(
    imdb_id=None, tmdb_id=None, tvdb_id=None, mal_id=None, media_type_hint=None
):
    """Lookup media by external ID."""
    # MAL lookup for anime
    if mal_id:
        try:
            anime_metadata = app.providers.mal.anime(int(mal_id))
            return {
                "id": mal_id,
                "media_type": MediaTypes.ANIME.value,
                "title": anime_metadata.get("title"),
                "year": anime_metadata.get("year"),
                "image": anime_metadata.get("image"),
                "ids": {"mal": mal_id},
            }
        except Exception as e:
            logger.warning("MAL lookup failed: %s", e)

    # TMDB direct lookup
    if tmdb_id:
        try:
            if media_type_hint == "movie":
                movie_metadata = app.providers.tmdb.movie(int(tmdb_id))
                return {
                    "id": tmdb_id,
                    "media_type": MediaTypes.MOVIE.value,
                    "title": movie_metadata.get("title"),
                    "year": movie_metadata.get("year"),
                    "image": movie_metadata.get("image"),
                    "ids": {"tmdb": tmdb_id},
                }
            tv_metadata = app.providers.tmdb.tv(int(tmdb_id))
            return {
                "id": tmdb_id,
                "media_type": MediaTypes.TV.value,
                "title": tv_metadata.get("title"),
                "year": tv_metadata.get("year"),
                "image": tv_metadata.get("image"),
                "ids": {"tmdb": tmdb_id},
            }
        except Exception as e:
            logger.warning("TMDB lookup failed: %s", e)

    # IMDB/TVDB lookup via TMDB find
    ext_id = imdb_id or tvdb_id
    ext_type = "imdb_id" if imdb_id else "tvdb_id"

    if ext_id:
        try:
            response = app.providers.tmdb.find(ext_id, ext_type)

            if response.get("movie_results"):
                movie = response["movie_results"][0]
                return {
                    "id": movie.get("id"),
                    "media_type": MediaTypes.MOVIE.value,
                    "title": movie.get("title"),
                    "year": (movie.get("release_date") or "")[:4],
                    "image": f"https://image.tmdb.org/t/p/w500{movie.get('poster_path')}"
                    if movie.get("poster_path")
                    else None,
                    "ids": {
                        "tmdb": movie.get("id"),
                        ext_type.replace("_id", ""): ext_id,
                    },
                }

            if response.get("tv_results"):
                tv = response["tv_results"][0]
                return {
                    "id": tv.get("id"),
                    "media_type": MediaTypes.TV.value,
                    "title": tv.get("name"),
                    "year": (tv.get("first_air_date") or "")[:4],
                    "image": f"https://image.tmdb.org/t/p/w500{tv.get('poster_path')}"
                    if tv.get("poster_path")
                    else None,
                    "ids": {"tmdb": tv.get("id"), ext_type.replace("_id", ""): ext_id},
                }
        except Exception as e:
            logger.warning("External ID lookup failed: %s", e)

    return None
