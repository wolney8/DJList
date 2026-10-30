"""Automatic catalogue lookup and library route checks with no network calls."""
import copy
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import Application, Handler
from spotify_api import AppError


SOURCE = "0123456789ABCDEFGHIJKL"


def track(identity="one", title="Night Window (Original Mix)"):
    return {"spotify_id": identity, "artists": "Example Artist", "title_and_version": title,
            "album": "Example album", "duration": "5:12", "duration_ms": 312000,
            "isrc": "TEST00000001", "spotify_url": ""}


def result(offers=None, errors=None):
    if offers is None:
        offers = [{"id": "itunes:123", "title": "Night Window (Original Mix)", "artist": "Example Artist",
                   "album": "Example album", "duration_ms": 312000, "genre": "Electronic", "bpm": None,
                   "musical_key": "", "price": {"amount": .99, "currency": "GBP", "display": "£0.99"},
                   "purchase_url": "https://music.apple.com/gb/album/example/122?i=123", "preview_url": None,
                   "match": {"status": "likely_match", "score": 100, "reasons": ["Title and artist match."]}}]
    return {"offers": copy.deepcopy(offers), "errors": errors or [],
            "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}


class StreamSocket:
    def __init__(self, request):
        self.input = io.BytesIO(request)
        self.output = io.BytesIO()

    def makefile(self, mode, *args):
        return self.input if "r" in mode else self.output

    def sendall(self, data):
        self.output.write(data)


class AutomationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.music = self.root / "Music"
        self.music.mkdir()
        self.app = Application(self.root / "data")
        self.app.change_settings({"music_root": str(self.music), "spotify_client_id": "a" * 32,
                                  "playlist_url": SOURCE})
        self.app.store.upsert([track()], SOURCE)
        self.identity = self.app.store.rows("real")[0]["id"]
        self.app.lookup.interval = 0
        self.app.lookup.retry_delay = 0
        self.app.catalog.search = Mock(return_value=result())
        self.gates = []

    def tearDown(self):
        for gate in self.gates:
            gate.set()
        self.app.close()
        self.temp.cleanup()

    def settle(self):
        worker = self.app.lookup.thread
        if worker is not None:
            worker.join(timeout=3)
        self.assertFalse(self.app.lookup.status()["running"], "The fake catalogue worker did not finish")

    def lookup(self, identity=None):
        identity = self.identity if identity is None else identity
        return next(row["store_lookup"] for row in self.app.state()["tracks"] if row["id"] == identity)

    def blocking_search(self):
        entered, release = threading.Event(), threading.Event()
        self.gates.append(release)

        def search(query, reference, force=False):
            entered.set()
            if not release.wait(timeout=3):
                raise AssertionError("Test did not release its fake store request")
            return result()

        self.app.catalog.search = Mock(side_effect=search)
        return entered, release

    def request(self, path, method="GET", body=None, headers=None):
        values = {"Host": "127.0.0.1:8888", **(headers or {})}
        payload = b"" if body is None else json.dumps(body).encode()
        values["Content-Length"] = str(len(payload))
        if body is not None:
            values["Content-Type"] = "application/json"
        raw = (method + " " + path + " HTTP/1.1\r\n" + "".join(key + ": " + value + "\r\n" for key, value in values.items()) + "\r\n").encode() + payload
        socket = StreamSocket(raw)
        Handler(socket, ("127.0.0.1", 12001), type("Server", (), {"app": self.app})())
        head, payload = socket.output.getvalue().split(b"\r\n\r\n", 1)
        return int(head.split()[1]), json.loads(payload)

    def auth(self):
        return {"Origin": self.app.origin, "X-CSRF-Token": self.app.csrf_token}

    def spotify_session(self):
        self.app.tokens = {"access_token": "test-memory-only"}
        self.app.token_client_id = "a" * 32

    def test_successful_spotify_refresh_enqueues_without_waiting_for_store(self):
        entered, release = self.blocking_search()
        self.spotify_session()
        with patch("app.SpotifyClient.tracks", return_value=([track()], 0)):
            synced = self.app.sync()
        self.assertEqual(synced["store_lookup_queued"], 1)
        self.assertTrue(entered.wait(timeout=2))
        self.assertTrue(self.app.lookup.status()["running"])
        self.app.edit_track(self.identity, {"notes": "edited during lookup", "owned": True})
        release.set()
        self.settle()
        self.assertEqual(self.lookup()["status"], "matched")
        saved = self.app.store.rows("real")[0]
        self.assertEqual(saved["notes"], "edited during lookup")
        self.assertTrue(saved["owned"])

    def test_auto_lookup_can_be_disabled_and_failed_sync_never_starts_it(self):
        self.spotify_session()
        self.app.change_settings({"auto_store_lookup": False})
        with patch("app.SpotifyClient.tracks", return_value=([track()], 0)):
            self.assertNotIn("store_lookup_queued", self.app.sync())
        self.app.change_settings({"auto_store_lookup": True})
        with patch("app.SpotifyClient.tracks", side_effect=AppError("Incomplete response", 502)):
            with self.assertRaises(AppError):
                self.app.sync()
        self.app.catalog.search.assert_not_called()

    def test_store_results_persist_without_overwriting_annotations(self):
        manual = {"purchase_url": "https://example.com/my-confirmed-track", "tags": "jungle; warmup", "bpm": "172",
                  "musical_key": "5A", "owned": False, "notes": "manual note", "cue_notes": "keep cue plan"}
        self.app.edit_track(self.identity, manual)
        self.app.refresh_stores(force=True)
        self.settle()
        self.assertEqual(self.lookup()["offer"]["genre"], "Electronic")
        saved = self.app.store.rows("real")[0]
        self.assertEqual({key: saved[key] for key in manual}, manual)
        self.app.close()
        fresh = Application(self.root / "data")
        try:
            lookup = fresh.state()["tracks"][0]["store_lookup"]
            self.assertEqual(lookup["status"], "matched")
            self.assertEqual(lookup["offer"]["id"], "itunes:123")
            self.assertFalse(fresh.lookup.status()["running"])
        finally:
            fresh.close()

    def test_daily_freshness_skips_repeated_lookup_but_force_rechecks(self):
        self.app.refresh_stores()
        self.settle()
        self.assertEqual(self.app.refresh_stores()["queued"], 0)
        self.assertEqual(self.app.catalog.search.call_count, 1)
        self.app.refresh_stores(force=True, identity=self.identity)
        self.settle()
        self.assertTrue(self.app.catalog.search.call_args.kwargs["force"])
        with self.app.store.connect() as db:
            db.execute("UPDATE store_lookups SET checked_at='2000-01-01T00:00:00+00:00'")
        self.assertEqual(self.app.refresh_stores()["queued"], 1)
        self.settle()
        self.assertFalse(self.app.catalog.search.call_args.kwargs["force"])

    def test_ambiguous_or_different_version_is_not_automatically_attached(self):
        offers = result()["offers"]
        offers.append({**offers[0], "id": "itunes:456"})
        self.app.catalog.search.return_value = result(offers)
        self.app.refresh_stores()
        self.settle()
        self.assertEqual(self.lookup()["status"], "review")
        self.assertIsNone(self.lookup()["offer"])
        self.assertFalse(self.app.store.rows("real")[0]["owned"])
        offers = [{**offers[0], "match": {"status": "different_version", "score": 55}}]
        self.app.catalog.search.return_value = result(offers)
        self.app.refresh_stores(force=True)
        self.settle()
        self.assertEqual(self.lookup()["status"], "review")
        self.assertIsNone(self.lookup()["offer"])

    def test_transient_failure_retries_boundedly_and_keeps_previous_result(self):
        self.app.refresh_stores()
        self.settle()
        previous = self.lookup()
        self.app.catalog.search.reset_mock()
        self.app.catalog.search.return_value = result([], [{"code": "rate_limited", "message": "Try later."}])
        self.app.refresh_stores(force=True)
        self.settle()
        current = self.lookup()
        self.assertEqual(self.app.catalog.search.call_count, 3)
        self.assertEqual(current["status"], "error")
        self.assertEqual(current["offer"], previous["offer"])
        self.assertEqual(current["checked_at"], previous["checked_at"])
        self.assertEqual(self.app.lookup.status()["failed"], 1)

    def test_transient_retry_then_success_counts_one_completed_track(self):
        self.app.catalog.search.side_effect = [result([], [{"code": "busy", "message": "Busy"}]), result()]
        self.app.refresh_stores()
        self.settle()
        self.assertEqual(self.app.catalog.search.call_count, 2)
        self.assertEqual(self.app.lookup.status()["completed"], 1)
        self.assertEqual(self.lookup()["status"], "matched")

    def test_invalid_catalogue_input_is_visible_and_not_retried(self):
        self.app.catalog.search.side_effect = AppError("The track title is too long for a catalogue search.")
        self.app.refresh_stores()
        self.settle()
        self.assertEqual(self.app.catalog.search.call_count, 1)
        self.assertEqual(self.lookup()["status"], "error")
        self.assertIn("too long", self.lookup()["message"])

    def test_cancel_stops_pending_work_and_discards_current_network_result(self):
        self.app.store.upsert([track(), track("two")], SOURCE)
        entered, release = self.blocking_search()
        self.app.refresh_stores()
        self.assertTrue(entered.wait(timeout=2))
        self.app.lookup.cancel()
        release.set()
        self.settle()
        self.assertEqual(self.app.catalog.search.call_count, 1)
        for row in self.app.state()["tracks"]:
            self.assertEqual(row["store_lookup"]["status"], "interrupted")
            self.assertIsNone(row["store_lookup"]["offer"])
        self.assertEqual(self.app.lookup.status()["cancelled"], 2)
        self.app.catalog.search = Mock(return_value=result())
        self.app.refresh_stores()
        self.settle()
        self.assertEqual(self.app.lookup.status()["completed"], 2)

    def test_stale_result_cannot_attach_to_changed_spotify_metadata(self):
        entered, release = self.blocking_search()
        self.app.refresh_stores()
        self.assertTrue(entered.wait(timeout=2))
        self.app.store.upsert([track(title="Different Recording (Extended Mix)")], SOURCE)
        release.set()
        self.settle()
        self.assertIsNone(self.lookup()["offer"])
        self.assertEqual(self.lookup()["status"], "not_checked")

    def test_changed_metadata_can_requeue_while_old_request_is_running(self):
        entered, release = self.blocking_search()
        first = self.app.catalog.search.side_effect

        def search(query, reference, force=False):
            if reference["title"] == "Night Window (Original Mix)":
                return first(query, reference, force)
            response = result()
            response["offers"][0]["title"] = reference["title"]
            return response

        self.app.catalog.search.side_effect = search
        self.app.refresh_stores()
        self.assertTrue(entered.wait(timeout=2))
        self.app.store.upsert([track(title="Night Window (Extended Mix)")], SOURCE)
        self.assertEqual(self.app.refresh_stores()["queued"], 1)
        release.set()
        self.settle()
        self.assertEqual(self.lookup()["offer"]["title"], "Night Window (Extended Mix)")
        self.assertEqual(self.app.lookup.status()["completed"], 2)

    def test_successful_sync_stays_successful_when_previous_queue_is_stopping(self):
        entered, release = self.blocking_search()
        self.app.refresh_stores()
        self.assertTrue(entered.wait(timeout=2))
        self.app.lookup.cancel()
        self.spotify_session()
        with patch("app.SpotifyClient.tracks", return_value=([track(), track("new")], 0)):
            synced = self.app.sync()
        self.assertEqual(synced["added"], 1)
        self.assertEqual(synced["store_lookup_queued"], 0)
        self.assertIn("stopping", synced["store_lookup_error"])
        release.set()
        self.settle()

    def test_active_queue_deduplicates_and_accepts_new_track_work(self):
        self.app.store.upsert([track(), track("two")], SOURCE)
        second = self.app.store.rows("real")[1]["id"]
        entered, release = self.blocking_search()
        self.app.refresh_stores(identity=self.identity)
        self.assertTrue(entered.wait(timeout=2))
        self.assertEqual(self.app.refresh_stores(force=True, identity=self.identity)["queued"], 0)
        self.assertEqual(self.app.refresh_stores(identity=second)["queued"], 1)
        release.set()
        self.settle()
        self.assertEqual(self.app.catalog.search.call_count, 2)
        self.assertEqual(self.app.lookup.status()["completed"], 2)

    def test_restarting_marks_unfinished_lookup_interrupted_without_network(self):
        self.app.refresh_stores()
        self.settle()
        self.app.close()
        with self.app.store.connect() as db:
            db.execute("UPDATE store_lookups SET status='searching'")
        fresh = Application(self.root / "data")
        try:
            saved = fresh.state()["tracks"][0]["store_lookup"]
            self.assertEqual(saved["status"], "interrupted")
            self.assertEqual(saved["offer"]["id"], "itunes:123")
            self.assertIsNone(fresh.lookup.thread)
        finally:
            fresh.close()

    def test_demo_cancels_real_lookup_and_cannot_start_store_or_library_work(self):
        entered, release = self.blocking_search()
        self.app.refresh_stores()
        self.assertTrue(entered.wait(timeout=2))
        self.app.demo(True)
        release.set()
        self.settle()
        self.assertEqual(self.app.state()["suggestions"], [])
        self.assertFalse(self.app.state()["store_job"]["running"])
        for action in (lambda: self.app.refresh_stores(), lambda: self.app.refresh_libraries(), lambda: self.app.library_review({})):
            with self.assertRaises(AppError):
                action()

    def test_partial_store_response_is_review_not_a_confirmed_candidate(self):
        self.app.catalog.search.return_value = result(errors=[{"code": "unavailable", "message": "Search was incomplete."}])
        self.app.refresh_stores()
        self.settle()
        self.assertEqual(self.lookup()["status"], "review")
        self.assertIsNone(self.lookup()["offer"])

    def test_new_routes_keep_local_csrf_and_argument_guards(self):
        for path in ("/api/store-job", "/api/library-review"):
            self.assertEqual(self.request(path, headers={"Host": "other.example:8888"})[0], 403)
        for path in ("/api/store-refresh", "/api/store-cancel", "/api/refresh-libraries", f"/api/tracks/{self.identity}/refresh-store"):
            self.assertEqual(self.request(path, "POST", {})[0], 403)
        self.assertEqual(self.request("/api/store-refresh", "POST", {"force": "yes"}, self.auth())[0], 400)
        self.assertEqual(self.request("/api/refresh-libraries", "POST", {"media": False, "xml": False}, self.auth())[0], 400)
        self.assertEqual(self.request("/api/library-review?page=0")[0], 400)
        self.assertEqual(self.request("/api/library-review?q=a&q=b")[0], 400)
        self.assertEqual(self.request("/api/library-review?page_size=101")[0], 400)
        self.assertEqual(self.request("/api/library-review?filter=unknown")[0], 400)
        with patch.object(self.app, "refresh_stores", return_value={"queued": 1}) as refresh:
            status, data = self.request(f"/api/tracks/{self.identity}/refresh-store", "POST", {}, self.auth())
            self.assertEqual((status, data["queued"]), (200, 1))
            refresh.assert_called_once_with(force=True, identity=self.identity)

    def test_new_settings_validate_types_and_xml_path_atomically(self):
        self.assertTrue(self.app.store.settings()["auto_store_lookup"])
        for values in ({"auto_store_lookup": "false"}, {"auto_store_lookup": False, "rekordbox_xml": str(self.root / "master.db")},
                       {"rekordbox_xml": "relative.xml"}):
            with self.assertRaises(AppError):
                self.app.change_settings(values)
        self.assertTrue(self.app.store.settings()["auto_store_lookup"])
        self.assertEqual(self.app.store.settings()["rekordbox_xml"], "")

    def test_xml_refresh_can_succeed_when_media_drive_is_unavailable(self):
        file_path = self.music / "Example Artist - Night Window (Original Mix).flac"
        xml = self.root / "collection.xml"
        xml.write_text('<DJ_PLAYLISTS><COLLECTION><TRACK TrackID="1" Name="Night Window (Original Mix)" Artist="Example Artist" Location="%s" Tonality="C minor" AverageBpm="132"/></COLLECTION></DJ_PLAYLISTS>' % file_path.as_uri())
        self.app.change_settings({"rekordbox_xml": str(xml)})
        self.music.rename(self.root / "Disconnected")
        refreshed = self.app.refresh_libraries()
        self.assertTrue(refreshed["partial"])
        self.assertEqual([error["source"] for error in refreshed["errors"]], ["media"])
        self.assertIsNotNone(refreshed["xml"])
        self.assertEqual(refreshed["library"]["entry_count"], 1)
        self.assertEqual(refreshed["library"]["missing"], 1)
        self.assertFalse(self.app.store.rows("real")[0]["owned"])
        status, review = self.request("/api/library-review?page=1&page_size=25&filter=missing")
        self.assertEqual(status, 200)
        self.assertEqual(review["total"], 1)


if __name__ == "__main__":
    unittest.main()
