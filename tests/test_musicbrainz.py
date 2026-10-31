import copy
import io
import json
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

from musicbrainz import (CACHE_SECONDS, MAX_CACHE_ENTRIES, MAX_RESPONSE_BYTES, MIN_REQUEST_INTERVAL,
                        NOTICE, PURCHASE_RELATION, USER_AGENT, MusicBrainzClient, MusicBrainzError,
                        NoRedirect, fetch_recordings, shop_link)
from spotify_api import AppError


ISRC = "FR9W11418529"
RECORDING = "a991cb49-f0c0-459c-90a6-de47aaaf3de9"
OTHER_RECORDING = "b991cb49-f0c0-459c-90a6-de47aaaf3de9"
REFERENCE = {"isrc": ISRC, "artists": "Carpenter Brut", "title": "Turbo Killer", "duration_ms": 209000}
BANDCAMP = "https://carpenterbrut.bandcamp.com/track/turbo-killer-2"
BEATPORT = "https://www.beatport.com/track/turbo-killer/15725264"


def relation(url, **changes):
    return {"type-id": PURCHASE_RELATION, "type": "purchase for download", "direction": "forward",
            "target-type": "url", "ended": False, "end": None, "url": {"resource": url}, **changes}


def recording(**changes):
    return {"id": RECORDING, "title": "Turbo Killer", "length": 209000,
            "artist-credit": [{"name": "Carpenter Brut", "artist": {"name": "Carpenter Brut"}}],
            "relations": [relation(BANDCAMP), relation(BEATPORT)], **changes}


def payload(*records, isrc=ISRC):
    return {"isrc": isrc, "recordings": list(records) if records else [recording()]}


class Clock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, value):
        self.sleeps.append(value)
        self.now += value


class Response(io.BytesIO):
    def __init__(self, body, content_type="application/json", length=None):
        super().__init__(body)
        self.headers = {"Content-Type": content_type}
        if length is not None:
            self.headers["Content-Length"] = str(length)
        self.read_sizes = []

    def read(self, size=-1):
        self.read_sizes.append(size)
        return super().read(size)


class MusicBrainzTests(unittest.TestCase):
    def client(self, data=None):
        clock = Clock()
        calls = []

        def fetch(isrc):
            calls.append((isrc, clock()))
            return copy.deepcopy(data if data is not None else payload())

        return MusicBrainzClient(fetcher=fetch, clock=clock, sleeper=clock.sleep), clock, calls

    def test_direct_purchase_links_retain_recording_evidence_and_no_shop_metadata(self):
        client, _, calls = self.client()
        result = client.lookup(REFERENCE)
        self.assertEqual(result["status"], "found")
        self.assertEqual(result["supported_providers"], ["Bandcamp", "Beatport"])
        self.assertEqual(len(calls), 1)
        self.assertEqual({link["purchase_url"] for link in result["links"]}, {BANDCAMP, BEATPORT})
        for link in result["links"]:
            self.assertEqual(link["match"]["status"], "likely_match")
            self.assertEqual(link["source_url"], "https://musicbrainz.org/recording/" + RECORDING)
            self.assertEqual(link["recording_id"], RECORDING)
            self.assertTrue(link["checked_at"])
            self.assertTrue({"price", "preview_url", "bpm", "musical_key", "genre"}.isdisjoint(link))
        self.assertIn("have not been checked", NOTICE)

    def test_only_safe_direct_bandcamp_and_beatport_urls_are_returned(self):
        for url in (BANDCAMP, BEATPORT, "https://beatport.com/track/a-track/123", "https://a1.bandcamp.com/track/a-track/"):
            with self.subTest(url=url):
                self.assertIsNotNone(shop_link(url))
        invalid = [
            "http://carpenterbrut.bandcamp.com/track/turbo-killer-2", BANDCAMP + "?affiliate=1", BANDCAMP + "#download",
            "https://carpenterbrut.bandcamp.com/album/trilogy", "https://www.beatport.com/release/trilogy/3512318",
            "https://evil.bandcamp.com.attacker.test/track/title", "https://a.b.bandcamp.com/track/title",
            "https://carpenterbrut.bandcamp.com@attacker.test/track/title", "https://me@carpenterbrut.bandcamp.com/track/title",
            "https://www.beatport.com:444/track/turbo-killer/15725264", "https://www.beatport.com/track/../../15725264",
            "https://www.beatport.com/track/turbo%2fkiller/15725264", "https://www.beatport.com/track/name/0",
            "https://www.beatsource.com/track/name/123", "https://open.qobuz.com/track/123",
            "https://www.qobuz.com/gb-en/album/title/abc", "https://music.apple.com/gb/song/123", "https://127.0.0.1/track/a/1",
            "javascript:alert(1)", "https://www.beatport.com/track/name/1\n", None, 5,
        ]
        for url in invalid:
            with self.subTest(url=url):
                self.assertIsNone(shop_link(url))

    def test_streaming_expired_backward_and_album_relations_are_ignored(self):
        urls = [relation(BANDCAMP, **{"type-id": "streaming-type"}), relation(BEATPORT, ended=True),
                relation(BANDCAMP, end="2025"), relation(BANDCAMP, direction="backward"),
                relation(BEATPORT, **{"target-type": "recording"}),
                relation("https://carpenterbrut.bandcamp.com/album/trilogy")]
        client, _, _ = self.client(payload(recording(relations=urls)))
        result = client.lookup(REFERENCE)
        self.assertEqual((result["status"], result["links"]), ("not_found", []))

    def test_wrong_artist_or_base_title_is_not_exposed_despite_same_isrc(self):
        variants = [recording(**{"artist-credit": [{"name": "Carpenter Brut Tribute"}]}),
                    recording(title="Turbo Lover")]
        for record in variants:
            with self.subTest(record=record):
                client, _, _ = self.client(payload(record))
                result = client.lookup(REFERENCE)
                self.assertEqual(result["status"], "not_found")
                self.assertEqual(result["links"], [])

    def test_same_isrc_different_version_and_duration_require_review(self):
        for record, expected in ((recording(title="Turbo Killer (live)"), "different_version"),
                                 (recording(length=235000), "check_duration"),
                                 (recording(length=None), "possible_match")):
            with self.subTest(expected=expected):
                client, _, _ = self.client(payload(record))
                result = client.lookup(REFERENCE)
                self.assertEqual(result["status"], "review")
                self.assertTrue(all(link["match"]["status"] == expected for link in result["links"]))

    def test_multiple_recordings_require_review_and_duplicate_urls_are_not_repeated(self):
        client, _, _ = self.client(payload(recording(), recording(id=OTHER_RECORDING)))
        result = client.lookup(REFERENCE)
        self.assertEqual(result["status"], "review")
        self.assertEqual(result["recordings_checked"], 2)
        self.assertEqual(len(result["links"]), 2)
        self.assertTrue(all(link["match"]["status"] == "possible_match" for link in result["links"]))
        self.assertIn("multiple", " ".join(result["links"][0]["match"]["reasons"]))

    def test_recording_disambiguation_is_visible_and_requires_review(self):
        for note in ("radio edit", "original master", "live version with identical duration"):
            with self.subTest(note=note):
                client, _, _ = self.client(payload(recording(disambiguation=note)))
                result = client.lookup(REFERENCE)
                self.assertEqual(result["status"], "review")
                self.assertFalse(result["incomplete"])
                self.assertTrue(all(link["disambiguation"] == note for link in result["links"]))
                self.assertTrue(all(link["match"]["status"] == "possible_match" for link in result["links"]))
                self.assertIn(note, " ".join(result["links"][0]["match"]["reasons"]))

    def test_skipped_records_make_evidence_incomplete_and_are_not_cached(self):
        for records, expected in (([recording(), {"id": "invalid"}], "review"),
                                  ([{"id": "invalid"}], "unavailable"),
                                  ([recording(), recording()], "review")):
            with self.subTest(expected=expected, count=len(records)):
                client, _, calls = self.client({"isrc": ISRC, "recordings": records})
                result = client.lookup(REFERENCE)
                self.assertEqual(result["status"], expected)
                self.assertTrue(result["incomplete"])
                self.assertEqual(result["skipped_recordings"], 1)
                self.assertIn("incomplete", result["message"])
                self.assertFalse(client.lookup(REFERENCE)["cached"])
                self.assertEqual(len(calls), 2)
                if result["links"]:
                    self.assertTrue(all(link["match"]["status"] == "possible_match" for link in result["links"]))

    def test_non_list_recording_or_relation_schema_is_unavailable_without_cache(self):
        for data in ({"isrc": ISRC, "recordings": {}}, payload(recording(relations={})),
                     payload(recording(relations=None))):
            with self.subTest(data=data):
                client, _, calls = self.client(data)
                result = client.lookup(REFERENCE)
                self.assertEqual(result["status"], "unavailable")
                self.assertEqual(result["error_code"], "invalid_response")
                self.assertEqual(result["links"], [])
                client.lookup(REFERENCE)
                self.assertEqual(len(calls), 2)

    def test_truncated_and_malformed_relations_are_visibly_incomplete_without_negative_cache(self):
        ignored = relation("https://example.com/ignored", **{"type-id": "other-type"})
        cases = [([relation(BANDCAMP)] + [ignored] * 200, "review", True),
                 ([ignored] * 200 + [relation(BANDCAMP)], "unavailable", True),
                 ([{}], "unavailable", False), ([relation(BANDCAMP), None], "review", False),
                 ([{**relation(BANDCAMP), "url": {}}], "unavailable", False)]
        for relations, expected, truncated in cases:
            with self.subTest(expected=expected, truncated=truncated, count=len(relations)):
                client, _, calls = self.client(payload(recording(relations=relations)))
                result = client.lookup(REFERENCE)
                self.assertEqual(result["status"], expected)
                self.assertTrue(result["incomplete"])
                self.assertEqual(result["truncated"], truncated)
                self.assertFalse(client.lookup(REFERENCE)["cached"])
                self.assertEqual(len(calls), 2)

    def test_missing_invalid_isrc_or_metadata_never_makes_network_requests(self):
        client, _, calls = self.client()
        for value, status in (("", "missing_isrc"), (None, "missing_isrc"), ("not-an-isrc", "invalid_isrc"),
                              ("../../other", "invalid_isrc")):
            with self.subTest(value=value):
                result = client.lookup({**REFERENCE, "isrc": value})
                self.assertEqual(result["status"], status)
                self.assertIsNone(result["checked_at"])
        self.assertEqual(client.lookup({**REFERENCE, "title": ""})["status"], "not_found")
        self.assertEqual(calls, [])
        with self.assertRaises(AppError):
            client.lookup(REFERENCE, force="yes")
        with self.assertRaises(AppError):
            client.lookup({**REFERENCE, "unexpected": "field"})

    def test_result_cache_expiry_force_and_manual_copy_isolation(self):
        client, clock, calls = self.client()
        first = client.lookup(REFERENCE)
        first["links"][0]["title"] = "Mutated client value"
        cached = client.lookup(REFERENCE)
        self.assertTrue(cached["cached"])
        self.assertEqual(cached["links"][0]["title"], "Turbo Killer")
        self.assertEqual(len(calls), 1)
        forced = client.lookup(REFERENCE, force=True)
        self.assertFalse(forced["cached"])
        self.assertEqual(len(calls), 2)
        self.assertGreaterEqual(calls[1][1] - calls[0][1], MIN_REQUEST_INTERVAL)
        clock.now += CACHE_SECONDS + 1
        self.assertFalse(client.lookup(REFERENCE)["cached"])
        self.assertEqual(len(calls), 3)

    def test_reference_change_is_compared_again_and_negative_results_are_cached(self):
        client, _, calls = self.client()
        client.lookup(REFERENCE)
        changed = client.lookup({**REFERENCE, "title": "Turbo Killer (live)"})
        self.assertEqual(changed["status"], "review")
        self.assertEqual(len(calls), 2)
        empty, _, requests = self.client({"isrc": ISRC, "recordings": []})
        self.assertEqual(empty.lookup(REFERENCE)["status"], "not_found")
        self.assertTrue(empty.lookup(REFERENCE)["cached"])
        self.assertEqual(len(requests), 1)

    def test_concurrent_different_lookups_are_serialised_and_paced(self):
        clock, calls, barrier = Clock(), [], threading.Barrier(3)

        def fetch(isrc):
            calls.append(clock())
            return payload(isrc=isrc)

        client = MusicBrainzClient(fetcher=fetch, clock=clock, sleeper=clock.sleep)
        failures = []

        def lookup(isrc):
            try:
                barrier.wait(timeout=2)
                client.lookup({**REFERENCE, "isrc": isrc})
            except Exception as error:
                failures.append(error)

        threads = [threading.Thread(target=lookup, args=(isrc,)) for isrc in (ISRC, "FR9W11418530")]
        for thread in threads:
            thread.start()
        barrier.wait(timeout=2)
        for thread in threads:
            thread.join(timeout=2)
        self.assertFalse(failures)
        self.assertEqual(len(calls), 2)
        self.assertGreaterEqual(calls[1] - calls[0], MIN_REQUEST_INTERVAL)

    def test_errors_are_not_cached_and_previous_cached_success_is_preserved(self):
        client, _, calls = self.client()
        client.lookup(REFERENCE)

        def failed(_):
            raise MusicBrainzError("unavailable", "Provider unavailable.")

        client.fetcher = failed
        self.assertEqual(client.lookup(REFERENCE, force=True)["status"], "unavailable")
        self.assertEqual(client.lookup(REFERENCE)["status"], "found")
        no_success = MusicBrainzClient(fetcher=failed, clock=Clock(), sleeper=lambda delay: None)
        self.assertEqual(no_success.lookup(REFERENCE)["status"], "unavailable")
        no_success.fetcher = lambda _: payload()
        self.assertEqual(no_success.lookup(REFERENCE)["status"], "found")
        self.assertEqual(len(calls), 1)

    def test_busy_response_cooldown_prevents_repeat_requests(self):
        clock, calls = Clock(), []

        def busy(_):
            calls.append(clock())
            raise MusicBrainzError("rate_limited", "Busy", 120)

        client = MusicBrainzClient(fetcher=busy, clock=clock, sleeper=clock.sleep)
        self.assertEqual(client.lookup(REFERENCE)["retry_after"], 120)
        clock.now += 10
        self.assertEqual(client.lookup(REFERENCE, force=True)["retry_after"], 110)
        self.assertEqual(len(calls), 1)
        clock.now += 111
        client.lookup(REFERENCE, force=True)
        self.assertEqual(len(calls), 2)

    def test_network_uses_fixed_isrc_endpoint_identifiable_agent_and_no_redirects(self):
        response = Response(json.dumps(payload()).encode())
        with patch("musicbrainz.OPENER.open", return_value=response) as opened:
            data = fetch_recordings(ISRC)
        self.assertEqual(data["isrc"], ISRC)
        request = opened.call_args.args[0]
        url = urlsplit(request.full_url)
        self.assertEqual((url.scheme, url.netloc, url.path), ("https", "musicbrainz.org", "/ws/2/isrc/" + ISRC))
        self.assertEqual(parse_qs(url.query), {"inc": ["artist-credits+url-rels"], "fmt": ["json"]})
        self.assertEqual(request.get_header("User-agent"), USER_AGENT)
        self.assertLessEqual(opened.call_args.kwargs["timeout"], 15)
        self.assertEqual(response.read_sizes, [MAX_RESPONSE_BYTES + 1])
        self.assertIsNone(NoRedirect().redirect_request(request, None, 302, "Found", {}, "https://example.test/"))

    def test_html_malformed_oversized_or_wrong_isrc_responses_are_unavailable(self):
        cases = [Response(b"<html>Site Unavailable</html>", "text/html"), Response(b"{broken"),
                 Response(b"{}"), Response(json.dumps(payload(isrc="FR9W11418530")).encode()),
                 Response(b"{}", length=MAX_RESPONSE_BYTES + 1), Response(b" " * (MAX_RESPONSE_BYTES + 1))]
        for response in cases:
            with self.subTest(response=response), patch("musicbrainz.OPENER.open", return_value=response):
                result = MusicBrainzClient().lookup(REFERENCE)
                self.assertEqual(result["status"], "unavailable")
                self.assertEqual(result["links"], [])
                self.assertIsNone(result["checked_at"])

    def test_not_found_busy_network_and_redirect_errors_are_distinguished(self):
        for error, expected in ((HTTPError("https://musicbrainz.org", 404, "Not Found", {}, None), "not_found"),
                                (HTTPError("https://musicbrainz.org", 429, "Busy", {"Retry-After": "90"}, None), "unavailable"),
                                (HTTPError("https://musicbrainz.org", 302, "Redirect", {}, None), "unavailable"),
                                (URLError("offline"), "unavailable")):
            with self.subTest(error=error), patch("musicbrainz.OPENER.open", side_effect=error):
                result = MusicBrainzClient().lookup(REFERENCE)
                self.assertEqual(result["status"], expected)
                if getattr(error, "code", None) == 429:
                    self.assertEqual(result["retry_after"], 90)

    def test_cache_and_recording_work_are_bounded(self):
        client, clock, _ = self.client()
        for index in range(MAX_CACHE_ENTRIES + 2):
            client.lookup({**REFERENCE, "title": "Turbo Killer (%s)" % index})
        self.assertEqual(len(client._cache), MAX_CACHE_ENTRIES)
        many = payload(*[recording(id="%08x-f0c0-459c-90a6-de47aaaf3de9" % index) for index in range(25)])
        bounded, _, _ = self.client(many)
        result = bounded.lookup(REFERENCE)
        self.assertTrue(result["truncated"])
        self.assertTrue(result["incomplete"])
        self.assertEqual(result["status"], "review")
        self.assertEqual(result["recordings_checked"], 20)
        self.assertFalse(bounded.lookup(REFERENCE)["cached"])


if __name__ == "__main__":
    unittest.main()
