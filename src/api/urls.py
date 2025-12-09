"""URL configuration for the scrobbler API."""

from django.urls import path

from api import views

app_name = "api"

urlpatterns = [
    # Authentication
    path("scrobbler/v1/auth/test", views.auth_test, name="auth_test"),
    # Scrobble events
    path("scrobbler/v1/scrobble/start", views.scrobble_start, name="scrobble_start"),
    path("scrobbler/v1/scrobble/pause", views.scrobble_pause, name="scrobble_pause"),
    path("scrobbler/v1/scrobble/stop", views.scrobble_stop, name="scrobble_stop"),
    # Current watching status
    path("scrobbler/v1/watching", views.watching, name="watching"),
    # Search and lookup
    path("scrobbler/v1/search", views.search, name="search"),
    path("scrobbler/v1/lookup", views.lookup, name="lookup"),
]
