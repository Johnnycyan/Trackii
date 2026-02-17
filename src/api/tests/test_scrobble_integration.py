"""Tests for scrobble session synchronization with manual tracking."""

from django.contrib.auth import get_user_model
from django.test import TestCase

from api.models import ScrobbleSession, ScrobbleState
from api.scrobble_helpers import (
    stop_active_sessions_for_item,
    stop_active_sessions_for_media,
)
from app.models import Item, MediaTypes, Sources, Status

User = get_user_model()


class StopActiveSessionsForItemTests(TestCase):
    """Test stop_active_sessions_for_item helper."""

    credentials = {"username": "testuser", "password": "testpass"}

    def setUp(self):
        self.user = User.objects.create_user(**self.credentials)
        self.item = Item.objects.create(
            media_id="12345",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Test Movie",
        )

    def test_stops_watching_session(self):
        """Active watching session is stopped."""
        session = ScrobbleSession.objects.create(
            user=self.user,
            item=self.item,
            state=ScrobbleState.WATCHING,
            progress=50.0,
        )

        stop_active_sessions_for_item(self.user, self.item)

        session.refresh_from_db()
        self.assertEqual(session.state, ScrobbleState.STOPPED)
        self.assertTrue(session.already_scrobbled)

    def test_stops_paused_session(self):
        """Paused session is also stopped."""
        session = ScrobbleSession.objects.create(
            user=self.user,
            item=self.item,
            state=ScrobbleState.PAUSED,
            progress=30.0,
        )

        stop_active_sessions_for_item(self.user, self.item)

        session.refresh_from_db()
        self.assertEqual(session.state, ScrobbleState.STOPPED)
        self.assertTrue(session.already_scrobbled)

    def test_ignores_already_stopped_session(self):
        """Already-stopped sessions are not touched."""
        session = ScrobbleSession.objects.create(
            user=self.user,
            item=self.item,
            state=ScrobbleState.STOPPED,
            progress=90.0,
            already_scrobbled=False,
        )

        stop_active_sessions_for_item(self.user, self.item)

        session.refresh_from_db()
        self.assertEqual(session.state, ScrobbleState.STOPPED)
        self.assertFalse(session.already_scrobbled)

    def test_only_affects_matching_user(self):
        """Sessions for other users are not stopped."""
        other_user = User.objects.create_user(
            username="otheruser",
            password="testpass",
        )
        my_session = ScrobbleSession.objects.create(
            user=self.user,
            item=self.item,
            state=ScrobbleState.WATCHING,
            progress=50.0,
        )
        other_session = ScrobbleSession.objects.create(
            user=other_user,
            item=self.item,
            state=ScrobbleState.WATCHING,
            progress=50.0,
        )

        stop_active_sessions_for_item(self.user, self.item)

        my_session.refresh_from_db()
        self.assertEqual(my_session.state, ScrobbleState.STOPPED)

        other_session.refresh_from_db()
        self.assertEqual(
            other_session.state,
            ScrobbleState.WATCHING,
        )

    def test_only_affects_matching_item(self):
        """Sessions for other items are not stopped."""
        other_item = Item.objects.create(
            media_id="99999",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Other Movie",
        )
        session_mine = ScrobbleSession.objects.create(
            user=self.user,
            item=self.item,
            state=ScrobbleState.WATCHING,
            progress=50.0,
        )
        session_other = ScrobbleSession.objects.create(
            user=self.user,
            item=other_item,
            state=ScrobbleState.WATCHING,
            progress=50.0,
        )

        stop_active_sessions_for_item(self.user, self.item)

        session_mine.refresh_from_db()
        self.assertEqual(
            session_mine.state,
            ScrobbleState.STOPPED,
        )

        session_other.refresh_from_db()
        self.assertEqual(
            session_other.state,
            ScrobbleState.WATCHING,
        )

    def test_stops_multiple_sessions(self):
        """Multiple active sessions for same item are stopped."""
        s1 = ScrobbleSession.objects.create(
            user=self.user,
            item=self.item,
            state=ScrobbleState.WATCHING,
            progress=20.0,
        )
        s2 = ScrobbleSession.objects.create(
            user=self.user,
            item=self.item,
            state=ScrobbleState.PAUSED,
            progress=40.0,
        )

        stop_active_sessions_for_item(self.user, self.item)

        s1.refresh_from_db()
        s2.refresh_from_db()
        self.assertEqual(s1.state, ScrobbleState.STOPPED)
        self.assertEqual(s2.state, ScrobbleState.STOPPED)
        self.assertTrue(s1.already_scrobbled)
        self.assertTrue(s2.already_scrobbled)

    def test_noop_when_no_sessions(self):
        """No error when there are no matching sessions."""
        stop_active_sessions_for_item(self.user, self.item)


class StopActiveSessionsForMediaTests(TestCase):
    """Test stop_active_sessions_for_media helper."""

    credentials = {"username": "testuser", "password": "testpass"}

    def setUp(self):
        self.user = User.objects.create_user(**self.credentials)
        self.item = Item.objects.create(
            media_id="12345",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Test Movie",
        )

    def test_stops_session_by_item_fk(self):
        """Session linked via item FK is stopped."""
        session = ScrobbleSession.objects.create(
            user=self.user,
            item=self.item,
            state=ScrobbleState.WATCHING,
            progress=50.0,
        )

        stop_active_sessions_for_media(
            self.user,
            MediaTypes.MOVIE.value,
            "12345",
            Sources.TMDB.value,
        )

        session.refresh_from_db()
        self.assertEqual(session.state, ScrobbleState.STOPPED)
        self.assertTrue(session.already_scrobbled)

    def test_stops_session_by_metadata(self):
        """Session matched by media_type + tmdb_id is stopped."""
        session = ScrobbleSession.objects.create(
            user=self.user,
            item=None,
            media_type=MediaTypes.MOVIE.value,
            tmdb_id="12345",
            state=ScrobbleState.WATCHING,
            progress=50.0,
        )

        stop_active_sessions_for_media(
            self.user,
            MediaTypes.MOVIE.value,
            "12345",
            Sources.TMDB.value,
        )

        session.refresh_from_db()
        self.assertEqual(session.state, ScrobbleState.STOPPED)
        self.assertTrue(session.already_scrobbled)

    def test_ignores_different_media(self):
        """Sessions for different media are not stopped."""
        session = ScrobbleSession.objects.create(
            user=self.user,
            item=None,
            media_type=MediaTypes.MOVIE.value,
            tmdb_id="99999",
            state=ScrobbleState.WATCHING,
            progress=50.0,
        )

        stop_active_sessions_for_media(
            self.user,
            MediaTypes.MOVIE.value,
            "12345",
            Sources.TMDB.value,
        )

        session.refresh_from_db()
        self.assertEqual(
            session.state,
            ScrobbleState.WATCHING,
        )


class HomePageFilterTests(TestCase):
    """Test that completed items are excluded from watching."""

    credentials = {"username": "testuser", "password": "testpass"}

    def setUp(self):
        from unittest.mock import patch

        from app.models import Movie

        self.user = User.objects.create_user(**self.credentials)
        self.item = Item.objects.create(
            media_id="12345",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Test Movie",
            image="http://example.com/image.jpg",
        )
        # Mock metadata API to avoid TMDB calls during save
        with patch(
            "app.models.providers.services.get_media_metadata",
            return_value={"max_progress": 1},
        ):
            Movie.objects.create(
                item=self.item,
                user=self.user,
                progress=1,
                status=Status.COMPLETED.value,
            )
        self.session = ScrobbleSession.objects.create(
            user=self.user,
            item=self.item,
            media_type=MediaTypes.MOVIE.value,
            title="Test Movie",
            state=ScrobbleState.WATCHING,
            progress=50.0,
        )

    def test_completed_item_excluded_from_home(self):
        """Completed items don't appear in watching sessions."""
        self.client.login(**self.credentials)
        response = self.client.get("/")

        watching = response.context.get("watching_sessions", [])
        session_items = [s.item_id for s in watching]
        self.assertNotIn(self.item.id, session_items)
