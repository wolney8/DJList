"""Catalogue matching, safety and degradation checks; fixtures use fictional songs."""
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import catalog
from catalog import CatalogClient, compare_offer, make_offer, price_for, safe_preview_url, safe_store_url
from spotify_api import AppError


REFERENCE = {"artists": "Example Artist; Guest", "title": "Night Window (Guest's Dreamer Dub)",
             "duration_ms": 193103, "isrc": "GBTEST000001"}


def item(identity=1, title=REFERENCE["title"], **overrides):
    return {"kind": "song", "trackId": identity, "artistName": "Example Artist",
            "trackName": title, "collectionName": "Fictional test release", "trackTimeMillis": 193103,
            "trackPrice": .99, "currency": "GBP", "primaryGenreName": "Electronic",
            "trackViewUrl": "https://music.apple.com/gb/album/example/100?i=%d" % identity,
            "previewUrl": "https://audio-ssl.itunes.apple.com/itunes-assets/AudioPreview/test.m4a",
            **overrides}


class CatalogueTests(unittest.TestCase):
    def test_auto_query_shortens_credits_but_ranks_complete_version(self):
        calls = []

        def fetch(query):
            calls.append(query)
            return [item(2, "Night Window"), item(3, "Night Window (Radio Edit)"), item()]

        result = CatalogClient(fetcher=fetch).search(REFERENCE["artists"] + " " + REFERENCE["title"], REFERENCE)
        self.assertEqual(calls, ["Example Artist Night Window"])
        self.assertEqual(result["offers"][0]["id"], "itunes:1")
        self.assertEqual(result["offers"][0]["title"], REFERENCE["title"])
        self.assertEqual(result["offers"][0]["match"]["status"], "likely_match")
        self.assertEqual(result["offers"][1]["match"]["status"], "different_version")
        self.assertEqual(result["offers"][0]["price"], {"amount": .99, "currency": "GBP", "display": "£0.99"})
        self.assertTrue(result["checked_at"])

    def test_edited_query_is_honoured_and_duration_mismatch_is_flagged(self):
        calls = []
        client = CatalogClient(fetcher=lambda query: calls.append(query) or [item(trackTimeMillis=250000)])
        result = client.search("Example Artist longer version", REFERENCE)
        self.assertEqual(calls, ["Example Artist longer version"])
        self.assertEqual(result["offers"][0]["match"]["status"], "check_duration")

    def test_artist_substrings_do_not_confirm_tribute_or_cover_performers(self):
        for artist in ("Example Artist Tribute Band", "The Example Artist Experience", "Example Artist & Another Act"):
            with self.subTest(artist=artist):
                offer = make_offer(item(artistName=artist), "test")
                match = compare_offer(offer, REFERENCE)
                self.assertEqual(match["status"], "check_artist")
                self.assertNotIn("Primary artist matches.", match["reasons"])

    def test_metadata_cache_is_reused_but_samples_are_never_fetched(self):
        calls = []
        clock = [100.0]
        client = CatalogClient(fetcher=lambda query: calls.append(query) or [item()], clock=lambda: clock[0])
        first = client.search("Example Artist Night Window")
        again = client.search("Example Artist Night Window")
        self.assertEqual(calls, ["Example Artist Night Window"])
        self.assertTrue(again["cached"])
        self.assertEqual(first["checked_at"], again["checked_at"])
        again["offers"][0]["title"] = "mutated response"
        self.assertEqual(client.search("Example Artist Night Window")["offers"][0]["title"], REFERENCE["title"])
        clock[0] += catalog.CACHE_SECONDS + 1
        self.assertFalse(client.search("Example Artist Night Window")["cached"])
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(not value.startswith("http") for value in calls))

    def test_bad_external_urls_and_unavailable_prices_do_not_become_offers(self):
        for url in ("http://music.apple.com/track", "https://music.apple.com.evil.example/track", "https://user:pass@music.apple.com/track", "https://music.apple.com:444/track", "javascript:alert(1)"):
            self.assertEqual(safe_store_url(url), "")
            self.assertIsNone(make_offer(item(trackViewUrl=url), "test"))
        for url in ("http://audio-ssl.itunes.apple.com/itunes-assets/AudioPreview/a.m4a", "https://audio-ssl.itunes.apple.com.evil.example/itunes-assets/AudioPreview/a.m4a", "https://other.apple.com/audio.m4a", "https://audio-ssl.itunes.apple.com/not-a-preview"):
            self.assertEqual(safe_preview_url(url), "")
        for price in (-1, True, float("nan"), "0.99", None):
            self.assertIsNone(price_for(item(trackPrice=price)))
        self.assertEqual(price_for(item(trackPrice=1.29, currency="USD"))["display"], "USD 1.29")

    def test_explicit_refresh_bypasses_cache_without_bypassing_request_limits(self):
        calls = []
        client = CatalogClient(fetcher=lambda query: calls.append(query) or [item(trackPrice=.99 + len(calls))], clock=lambda: 1)
        first = client.search("Example Artist Night Window")
        cached = client.search("Example Artist Night Window")
        self.assertEqual(first["offers"][0]["price"], cached["offers"][0]["price"])
        fresh = client.search("Example Artist Night Window", force=True)
        self.assertFalse(fresh["cached"])
        self.assertGreater(fresh["offers"][0]["price"]["amount"], first["offers"][0]["price"]["amount"])
        for _ in range(catalog.MAX_CALLS_PER_MINUTE - 2):
            client.search("Example Artist Night Window", force=True)
        limited = client.search("Example Artist Night Window", force=True)
        self.assertEqual(limited["errors"][0]["code"], "rate_limited")
        self.assertEqual(len(calls), catalog.MAX_CALLS_PER_MINUTE)

    def test_busy_provider_failure_and_rate_limit_are_visible_without_fabricated_data(self):
        client = CatalogClient(fetcher=lambda query: (_ for _ in ()).throw(URLError("unavailable")))
        result = client.search("Example")
        self.assertFalse(result["offers"])
        self.assertEqual(result["errors"][0]["code"], "unavailable")
        client.lock.acquire()
        try:
            self.assertEqual(client.search("Example")["errors"][0]["code"], "busy")
        finally:
            client.lock.release()
        calls = []
        limited = CatalogClient(fetcher=lambda query: calls.append(query) or [], clock=lambda: 1)
        for index in range(catalog.MAX_CALLS_PER_MINUTE):
            self.assertFalse(limited.search("Song %d" % index)["errors"])
        self.assertEqual(limited.search("Another song")["errors"][0]["code"], "rate_limited")
        self.assertEqual(len(calls), catalog.MAX_CALLS_PER_MINUTE)

    def test_http_rate_limit_does_not_repeat_requests_immediately(self):
        calls = []
        def fetch(query):
            calls.append(query)
            raise HTTPError(catalog.API_URL, 429, "Slow down", {}, None)
        client = CatalogClient(fetcher=fetch, clock=lambda: 1)
        self.assertEqual(client.search("One song")["errors"][0]["code"], "rate_limited")
        self.assertEqual(client.search("Two song")["errors"][0]["code"], "rate_limited")
        self.assertEqual(len(calls), 1)

    def test_invalid_input_is_rejected_before_any_network_call(self):
        client = CatalogClient(fetcher=lambda query: self.fail("Network should not be called"))
        for query in (None, "", " ", "x" * 501, "Artist\x00Song"):
            with self.assertRaises(AppError): client.search(query)
        for reference in (["bad"], {"unknown": "field"}, {"duration_ms": True}, {"duration_ms": float("nan")}, {"title": "bad\nvalue"}):
            with self.assertRaises(AppError): client.search("Example", reference)

    def test_network_request_is_fixed_uk_song_api_and_size_bounded(self):
        captured = []
        class Response(io.BytesIO):
            pass
        class Opener:
            def open(self, request, timeout):
                captured.append((request.full_url, timeout))
                return Response(json.dumps({"results": [item()]}).encode())
        with patch.object(catalog, "OPENER", Opener()):
            result = catalog.fetch_catalogue("Björk & Guest + Song")
        url, timeout = captured[0]
        parsed = urlsplit(url)
        self.assertEqual((parsed.scheme, parsed.netloc, parsed.path), ("https", "itunes.apple.com", "/search"))
        self.assertEqual(parse_qs(parsed.query)["term"], ["Björk & Guest + Song"])
        self.assertEqual(parse_qs(parsed.query)["country"], ["GB"])
        self.assertEqual(parse_qs(parsed.query)["entity"], ["song"])
        self.assertEqual(timeout, 8)
        self.assertEqual(result[0]["trackId"], 1)
        self.assertIsNone(catalog.NoRedirect().redirect_request(None, None, 302, "", {}, "https://other.example/"))


if __name__ == "__main__":
    unittest.main()
