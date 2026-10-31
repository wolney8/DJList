"""Manual community shop discovery uses stored identity and preserves user data."""
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import Application, Handler
from musicbrainz import MusicBrainzClient, MusicBrainzError, PURCHASE_RELATION
from spotify_api import AppError


TRACK = {"spotify_id": "fixture-shop-link", "artists": "Example Artist",
         "title_and_version": "Night Window (Original Mix)", "duration_ms": 193000,
         "isrc": "GBABC2600001", "spotify_url": ""}


def response(isrc):
    return {"isrc": isrc, "recordings": [{"id": "00000000-0000-4000-8000-000000000001",
            "title": TRACK["title_and_version"], "length": 193000,
            "artist-credit": [{"name": "Example Artist", "artist": {"name": "Example Artist"}}],
            "relations": [{"type-id": PURCHASE_RELATION, "target-type": "url", "direction": "forward",
                           "ended": False, "url": {"resource": "https://example-label.bandcamp.com/track/night-window"}}]}]}


class StreamSocket:
    def __init__(self, request):
        self.input, self.output = io.BytesIO(request), io.BytesIO()

    def makefile(self, mode, *args):
        return self.input if "r" in mode else self.output

    def sendall(self, data):
        self.output.write(data)


class ShopLinkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = Application(Path(self.temp.name) / "data")
        self.app.store.upsert([TRACK], "fixture-source")
        self.identity = self.app.store.rows("real")[0]["id"]
        self.path = "/api/tracks/%d/shop-links" % self.identity
        self.fetcher = Mock(side_effect=response)
        self.app.musicbrainz = MusicBrainzClient(fetcher=self.fetcher)
        self.app.store.patch(self.identity, "real", {"owned": True, "notes": "Keep my note",
            "tags": "my own tag", "bpm": "171", "musical_key": "8A",
            "purchase_url": "https://example.test/confirmed"})

    def tearDown(self):
        self.app.close()
        self.temp.cleanup()

    def request(self, body, authorised=True, path=None):
        payload = json.dumps(body).encode()
        headers = {"Host": "127.0.0.1:8888", "Content-Type": "application/json", "Content-Length": str(len(payload))}
        if authorised:
            headers.update({"Origin": self.app.origin, "X-CSRF-Token": self.app.csrf_token})
        raw = ("POST " + (path or self.path) + " HTTP/1.1\r\n" + "".join(k + ": " + v + "\r\n" for k, v in headers.items()) + "\r\n").encode() + payload
        socket = StreamSocket(raw)
        Handler(socket, ("127.0.0.1", 12001), type("Server", (), {"app": self.app})())
        head, data = socket.output.getvalue().split(b"\r\n\r\n", 1)
        return int(head.split()[1]), json.loads(data)

    def annotations(self):
        return self.app.store.get(self.identity, "real")["annotations"]

    def test_manual_lookup_returns_links_without_writing_user_fields(self):
        before = self.annotations()
        self.app.state()
        self.fetcher.assert_not_called()
        status, result = self.request({})
        self.assertEqual((status, result["status"]), (200, "found"))
        self.assertEqual(result["links"][0]["provider"], "bandcamp")
        self.fetcher.assert_called_once_with(TRACK["isrc"])
        self.assertEqual(self.annotations(), before)
        self.assertTrue(self.request({})[1]["cached"])
        self.fetcher.assert_called_once()

    def test_route_guards_prevent_unauthorised_or_spoofed_lookup(self):
        self.assertEqual(self.request({}, authorised=False)[0], 403)
        self.assertEqual(self.request({"reference": TRACK})[0], 400)
        self.assertEqual(self.request({"force": "yes"})[0], 400)
        self.assertEqual(self.request({}, path="/api/tracks/999999/shop-links")[0], 404)
        self.fetcher.assert_not_called()
        self.app.demo(True)
        self.assertEqual(self.request({})[0], 409)
        self.fetcher.assert_not_called()

    def test_changed_track_or_collection_discards_late_result(self):
        for change in ("metadata", "mode"):
            with self.subTest(change=change):
                self.app.demo(False)
                self.app.store.upsert([TRACK], "fixture-source")
                def late(reference, force=False):
                    if change == "metadata":
                        self.app.store.upsert([{**TRACK, "title_and_version": "A different version"}], "fixture-source")
                    else:
                        self.app.demo(True)
                    return {"status": "found", "links": []}
                self.app.musicbrainz.lookup = late
                before = self.annotations()
                status, result = self.request({})
                self.assertEqual(status, 409)
                self.assertIn("changed", result["error"])
                self.assertEqual(self.annotations(), before)

    def test_missing_identifier_and_provider_failure_remain_explicit(self):
        self.app.store.upsert([{**TRACK, "isrc": ""}], "fixture-source")
        self.assertEqual(self.request({})[1]["status"], "missing_isrc")
        self.fetcher.assert_not_called()
        self.app.store.upsert([TRACK], "fixture-source")
        self.fetcher.side_effect = MusicBrainzError("unavailable", "MusicBrainz is unavailable.")
        before = self.annotations()
        status, result = self.request({})
        self.assertEqual((status, result["status"]), (200, "unavailable"))
        self.assertEqual(self.annotations(), before)


if __name__ == "__main__":
    unittest.main()
