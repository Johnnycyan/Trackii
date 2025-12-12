from unittest.mock import patch, MagicMock
from django.test import TestCase
from api.views import _find_media_item
from app.models import MediaTypes, Sources

class MediaMatchingTests(TestCase):
    @patch("api.views.app.providers.tmdb")
    @patch("api.views.Item.objects")
    def test_find_media_item_resolves_imdb_id(self, mock_items, mock_tmdb):
        """Test that IMDB ID is resolved to TMDB ID and used for lookup."""
        # Setup:
        # 1. find("tt12345", "imdb_id") returns TMDB ID 12345
        mock_tmdb.find.return_value = {
            "movie_results": [{"id": 12345}]
        }
        
        # 2. Item.objects.filter().first()
        # First call (initial check with no TMDB ID) -> should be skipped or return None
        # Second call (check with resolved TMDB ID 12345) -> returns Mock Item
        mock_item = MagicMock()
        mock_item.id = 999
        mock_items.filter.return_value.first.return_value = mock_item

        # Execute
        item, tmdb_id = _find_media_item(
            media_type=MediaTypes.MOVIE.value,
            imdb_id="tt12345"
        )

        # Verify
        mock_tmdb.find.assert_called_with("tt12345", "imdb_id")
        self.assertEqual(tmdb_id, 12345)
        self.assertEqual(item, mock_item)
        
        # Verify that filter was called with the resolved ID
        # We expect filter to be called at least once with media_id="12345"
        calls = mock_items.filter.call_args_list
        # The last call should be the one using the resolved ID
        args, kwargs = calls[-1]
        self.assertEqual(kwargs['media_id'], "12345")
        self.assertEqual(kwargs['source'], Sources.TMDB.value)

    @patch("api.views.app.providers.tmdb")
    @patch("api.views.Item.objects")
    def test_find_media_item_fallback_to_title_search(self, mock_items, mock_tmdb):
        """Test that title search is used if IDs are missing."""
        # Setup:
        # 1. No IDs provided, only title
        # 2. search("The X Files") returns TMDB ID 4087
        mock_tmdb.search.return_value = {
            "results": [{"media_id": 4087}]
        }
        
        # 3. DB lookup with resolved ID returns None (not imported yet)
        mock_items.filter.return_value.first.return_value = None

        # Execute
        item, tmdb_id = _find_media_item(
            media_type=MediaTypes.TV.value,
            title="The X Files"
        )

        # Verify
        mock_tmdb.search.assert_called_with("The X Files", media_type=MediaTypes.TV.value)
        self.assertEqual(tmdb_id, 4087)
        self.assertIsNone(item)

    @patch("api.views.app.providers.tmdb")
    @patch("api.views.Item.objects")
    def test_find_media_item_existing_tmdb_id(self, mock_items, mock_tmdb):
        """Test that provided TMDB ID is used directly."""
        mock_item = MagicMock()
        mock_items.filter.return_value.first.return_value = mock_item

        item, tmdb_id = _find_media_item(
            media_type=MediaTypes.MOVIE.value,
            tmdb_id="12345"
        )

        self.assertEqual(tmdb_id, "12345")
        self.assertEqual(item, mock_item)
        # Should not call find or search
        mock_tmdb.find.assert_not_called()
        mock_tmdb.search.assert_not_called()
