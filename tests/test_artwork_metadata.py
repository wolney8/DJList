"""Provider metadata stays attributable and image URLs stay on known origins."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from catalog import make_offer, safe_artwork_url
from spotify_api import SpotifyClient, spotify_artwork


class ArtworkMetadataTests(unittest.TestCase):
    def test_spotify_sync_retains_artwork_and_release_metadata(self):
        image = "https://i.scdn.co/image/fictional-cover"
        track = {"id": "example", "name": "Night Window", "type": "track", "duration_ms": 193103,
                 "artists": [{"name": "Example Artist"}], "explicit": False,
                 "album": {"name": "Example Release", "release_date": "2026-01-01",
                           "images": [{"url": image, "width": 300, "height": 300}]}}
        client = SpotifyClient("test-client", {})
        client.get = lambda url: {"items": [{"item": track}], "next": None, "total": 1}
        tracks, unknown = client.tracks("liked")
        self.assertEqual(unknown, 0)
        self.assertEqual(tracks[0]["artwork_url"], image)
        self.assertEqual(tracks[0]["release_date"], "2026-01-01")
        self.assertIs(tracks[0]["explicit"], False)
        self.assertEqual(tracks[0]["duration_ms"], 193103)

    def test_spotify_artwork_rejects_untrusted_origins_and_handles_missing_images(self):
        for value in ("https://i.scdn.co.evil.example/image/a", "https://user@i.scdn.co/image/a",
                      "http://i.scdn.co/image/a", "https://i.scdn.co:444/image/a",
                      "data:image/png;base64,x", "https://i.scdn.co/image/a\n"):
            self.assertEqual(spotify_artwork({"images": [{"url": value}]}), "")
        for album in (None, {}, {"images": None}, {"images": "bad"}, {"images": [None, {}]}):
            self.assertEqual(spotify_artwork(album), "")
        small = "https://i.scdn.co/image/small"
        medium = "https://i.scdn.co/image/medium"
        large = "https://i.scdn.co/image/large"
        self.assertEqual(spotify_artwork({"images": [{"url": large, "width": 640},
                                                    {"url": medium, "width": 300},
                                                    {"url": small, "width": 64}]}), medium)

    def test_store_metadata_does_not_invent_tempo_or_key(self):
        source = {"kind": "song", "trackId": 123, "trackName": "Night Window", "artistName": "Example Artist",
                  "trackViewUrl": "https://music.apple.com/gb/album/example/1?i=123",
                  "artworkUrl100": "https://is1-ssl.mzstatic.com/image/thumb/example/100x100bb.jpg",
                  "primaryGenreName": "Electronic", "releaseDate": "2026-01-01T08:00:00Z",
                  "trackExplicitness": "explicit", "bpm": 172, "key": "C minor"}
        offer = make_offer(source, "test")
        self.assertEqual(offer["artwork_url"], source["artworkUrl100"])
        self.assertEqual(offer["genre"], "Electronic")
        self.assertTrue(offer["explicit"])
        self.assertIsNone(offer["bpm"])
        self.assertEqual(offer["musical_key"], "")
        self.assertIsNone(make_offer({**source, "trackExplicitness": []}, "test")["explicit"])

    def test_store_artwork_requires_known_https_cdn(self):
        for value in ("https://is1-ssl.mzstatic.com.evil.example/a", "http://is1-ssl.mzstatic.com/a",
                      "https://user:password@is1-ssl.mzstatic.com/a", "https://is1-ssl.mzstatic.com:444/a",
                      "https://example.com/a", "data:image/svg+xml,<svg/>"):
            self.assertEqual(safe_artwork_url(value), "")
        self.assertEqual(safe_artwork_url("https://is5-ssl.mzstatic.com/image/example.jpg"),
                         "https://is5-ssl.mzstatic.com/image/example.jpg")


if __name__ == "__main__":
    unittest.main()
