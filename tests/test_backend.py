"""Focused local integrity/security checks. No live Spotify or user audio is used."""
import base64
from contextlib import contextmanager
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlencode, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import Application, Handler, Store, MAX_BODY
from spotify_api import AppError, SpotifyClient, begin_authorization, callback_code, parse_source

SOURCE = "0123456789ABCDEFGHIJKL"


def track(identity="track-one", title="Night Window (Original Mix)"):
    return {"spotify_id": identity, "artists": "Example Artist", "title_and_version": title,
            "album": "Test album", "duration": "5:12", "isrc": "TEST00000001", "spotify_url": ""}


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.music = self.root / "Music"
        self.music.mkdir()
        self.master = self.root / "master.db"
        self.master.write_bytes(b"Do not read or modify Rekordbox database")
        self.application = Application(self.root / "data")
        self.application.change_settings({"spotify_client_id": "a" * 32, "music_root": str(self.music), "rekordbox_db": str(self.master), "playlist_url": SOURCE})
        self.application.store.upsert([track()], SOURCE)
        self.identity = self.application.state()["tracks"][0]["id"]

    def tearDown(self):
        self.application.close()
        self.temp.cleanup()

    def current(self):
        return self.application.store.rows("real")[0]

    def make_audio(self, name="Example Artist - Night Window (Original Mix).flac"):
        target = self.music / name
        target.write_bytes(b"test fixture, not an actual audio recording")
        return target

class AppCase(Fixture):
    def test_repeat_sync_preserves_annotations_and_ids_after_restart(self):
        updates = {"owned": True, "tags": "garage; warmup", "musical_key": "5A", "bpm": "130", "energy": "3",
                   "purchase_url": "https://example.com/track", "notes": "keep me", "cue_notes": "mix out at 3:00", "prep_status": "reviewed"}
        self.application.edit_track(self.identity, updates)
        self.application.store.upsert([track(title="Night Window (Updated Mix)"), track("second", "Night Window (Updated Mix)")], SOURCE)
        fresh = Application(self.root / "data")
        row = fresh.store.rows("real")[0]
        self.assertEqual(row["id"], self.identity)
        self.assertEqual({key: row[key] for key in updates}, updates)
        self.assertEqual(row["title_and_version"], "Night Window (Updated Mix)")
        self.assertEqual(len(fresh.store.rows("real")), 2)

    def test_removal_preserves_record_and_unknown_is_not_confirmed_missing(self):
        self.application.store.upsert([], SOURCE, unknown=1)
        self.assertIsNone(self.current()["in_source"])
        self.application.store.upsert([], SOURCE)
        self.assertFalse(self.current()["in_source"])
        self.application.store.upsert([track()], SOURCE)
        self.assertTrue(self.current()["in_source"])

    def test_failed_sync_preserves_prior_state(self):
        self.application.tokens = {"access_token": "temporary-token"}
        self.application.token_client_id = self.application.store.settings()["spotify_client_id"]
        before = self.current()
        with patch("app.SpotifyClient.tracks", side_effect=AppError("incomplete pages", 502)):
            with self.assertRaises(AppError):
                self.application.sync()
        self.assertEqual(self.current(), before)

    def test_upsert_rolls_back_all_rows_on_failure(self):
        before = self.current()
        invalid = track("broken")
        invalid["title_and_version"] = object()
        with self.assertRaises(TypeError):
            self.application.store.upsert([track(title="would change"), invalid], SOURCE)
        self.assertEqual(self.current(), before)

    def test_playlist_binding_rejects_source_swap(self):
        with self.assertRaises(AppError):
            self.application.change_settings({"playlist_url": "AAAAAAAAAAAAAAAAAAAAAA"})
        with self.assertRaises(AppError):
            self.application.store.upsert([track("new")], "different")
        self.assertEqual(len(self.application.store.rows("real")), 1)

    def test_demo_is_persistent_but_separate(self):
        real = self.current()
        self.application.demo(True)
        demo_id = self.application.state()["tracks"][0]["id"]
        self.application.edit_track(demo_id, {"tags": "demo edit"})
        with self.assertRaises(AppError):
            self.application.edit_track(self.identity, {"notes": "wrong namespace"})
        with self.assertRaises(AppError):
            self.application.scan()
        self.application.demo(False)
        self.assertEqual(self.current(), real)
        self.application.demo(True)
        self.assertEqual(self.application.state()["tracks"][0]["tags"], "demo edit")

    def test_scan_is_read_only_and_match_needs_confirmation(self):
        audio = self.make_audio()
        original_audio, original_db = audio.read_bytes(), self.master.read_bytes()
        result = self.application.scan()
        self.assertEqual(result["indexed"], 1)
        self.assertEqual(result["suggestions"][0]["track_id"], self.identity)
        self.assertFalse(self.current()["owned"])
        self.assertEqual(self.current()["local_file"], "")
        self.application.link(self.identity, str(audio))
        self.assertTrue(self.current()["owned"])
        self.assertTrue(self.current()["file_available"])
        self.assertEqual(audio.read_bytes(), original_audio)
        self.assertEqual(self.master.read_bytes(), original_db)

    def test_missing_drive_preserves_ownership_and_path(self):
        audio = self.make_audio()
        self.application.link(self.identity, str(audio))
        # Linking stores the canonical path; macOS temporary paths can use /var aliases.
        saved_path = self.current()["local_file"]
        self.assertEqual(saved_path, str(audio.resolve(strict=True)))
        self.music.rename(self.root / "Disconnected")
        with self.assertRaises(AppError):
            self.application.scan()
        row = self.current()
        self.assertTrue(row["owned"])
        self.assertFalse(row["file_available"])
        self.assertEqual(row["local_file"], saved_path)
        self.assertEqual(self.application.state()["settings"]["rekordbox_db"], str(self.master))

    def test_partial_scan_warning_survives_restart_and_failed_refresh(self):
        self.make_audio()
        self.application.scan()

        def partial_walk(root, followlinks=False, onerror=None):
            onerror(PermissionError("Test folder could not be read"))
            return iter(())

        with patch("app.os.walk", side_effect=partial_walk):
            scanned = self.application.scan()
        self.assertEqual(scanned["skipped"], 1)
        library = self.application.state()["library"]
        self.assertEqual(library["media_skipped"], 1)
        self.assertTrue(library["media_refreshed_at"])
        with self.application.store.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM files").fetchone()[0], 1)
        self.music.rename(self.root / "Disconnected")
        with self.assertRaises(AppError):
            self.application.scan()
        fresh = Application(self.root / "data")
        try:
            restored = fresh.state()["library"]
            self.assertEqual(restored["media_skipped"], 1)
            self.assertEqual(restored["media_refreshed_at"], library["media_refreshed_at"])
        finally:
            fresh.close()

    def test_link_rejects_outside_root_and_symlinks(self):
        outside = self.root / "outside.mp3"
        outside.write_bytes(b"fixture")
        link = self.music / "linked.mp3"
        link.symlink_to(outside)
        escape = self.music / "external"
        escape.symlink_to(self.root, target_is_directory=True)
        for path in (outside, link, escape / "outside.mp3", self.master):
            with self.subTest(path=path), self.assertRaises(AppError):
                self.application.link(self.identity, str(path))
        self.assertFalse(self.current()["owned"])

    def test_xml_inventory_needs_confirmation_and_keeps_manual_metadata(self):
        audio = self.make_audio()
        self.application.edit_track(self.identity, {"musical_key": "manual key"})
        xml = '<DJ_PLAYLISTS><COLLECTION><TRACK Name="Night Window (Original Mix)" Artist="Example Artist" Location="%s" Tonality="C minor" AverageBpm="130.00"><POSITION_MARK Name="Intro" Start="0"/></TRACK></COLLECTION></DJ_PLAYLISTS>' % audio.as_uri()
        result = self.application.import_xml(xml)
        self.assertEqual(result["indexed"], 1)
        self.assertEqual(result["suggestions"][0]["candidates"][0]["cue_count"], 1)
        self.assertFalse(self.current()["owned"])
        saved = self.application.link(self.identity, str(audio))
        self.assertEqual(saved["prep_status"], "imported")
        self.assertEqual(self.current()["musical_key"], "manual key")
        self.assertEqual(self.current()["bpm"], "130.00")
        self.application.edit_track(self.identity, {"prep_status": "reviewed"})
        self.application.link(self.identity, str(audio))
        self.assertEqual(self.current()["prep_status"], "reviewed")

    def test_xml_entities_rejected(self):
        with self.assertRaises(AppError):
            self.application.import_xml('<!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><DJ_PLAYLISTS><COLLECTION/></DJ_PLAYLISTS>')

    def test_atomic_path_edit_preserves_notes_on_invalid_link(self):
        before = self.current()
        with self.assertRaises(AppError):
            self.application.edit_track(self.identity, {"notes": "must not partially save", "local_file": str(self.music / "missing.mp3")})
        self.assertEqual(self.current(), before)
        audio = self.make_audio()
        self.application.edit_track(self.identity, {"notes": "now saved", "local_file": str(audio)})
        self.assertEqual(self.current()["notes"], "now saved")
        self.assertTrue(self.current()["owned"])
        self.assertEqual(self.current()["prep_status"], "downloaded")
        self.application.edit_track(self.identity, {"local_file": ""})
        self.assertEqual(self.current()["local_file"], "")
        self.assertTrue(self.current()["owned"])

    def test_atomic_xml_link_infers_only_unspecified_fields(self):
        audio = self.make_audio()
        xml = '<DJ_PLAYLISTS><COLLECTION><TRACK Location="%s" Tonality="C minor" AverageBpm="130"/></COLLECTION></DJ_PLAYLISTS>' % audio.as_uri()
        self.application.import_xml(xml)
        self.application.edit_track(self.identity, {"local_file": str(audio), "notes": "imported selection", "musical_key": "manual", "prep_status": "reviewed"})
        row = self.current()
        self.assertEqual((row["musical_key"], row["bpm"], row["prep_status"]), ("manual", "130", "reviewed"))

    def test_offline_notes_save_does_not_require_relinking(self):
        audio = self.make_audio()
        self.application.link(self.identity, str(audio))
        self.music.rename(self.root / "Disconnected")
        self.application.edit_track(self.identity, {"notes": "works while SSD offline"})
        self.assertEqual(self.current()["notes"], "works while SSD offline")
        self.assertFalse(self.current()["file_available"])

    def test_simultaneous_edits_preserve_distinct_manual_fields(self):
        original = self.application.store.connect
        barrier = threading.Barrier(2)

        class CoordinatedConnection:
            def __init__(self, db):
                self.db = db

            def execute(self, sql, *args):
                cursor = self.db.execute(sql, *args)
                if sql.startswith("SELECT annotations") and not self.db.in_transaction:
                    class CoordinatedCursor:
                        def fetchone(inner):
                            row = cursor.fetchone()
                            barrier.wait(timeout=3)
                            return row
                    return CoordinatedCursor()
                return cursor

        @contextmanager
        def coordinated():
            with original() as db:
                yield CoordinatedConnection(db)

        errors = []

        def update(values):
            try:
                self.application.store.patch(self.identity, "real", values)
            except Exception as error:
                errors.append(error)

        with patch.object(self.application.store, "connect", coordinated):
            threads = [threading.Thread(target=update, args=(values,)) for values in
                       ({"tags": "retained tag"}, {"notes": "retained note"})]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(5)
            self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertFalse(errors, errors)
        self.assertEqual((self.current()["tags"], self.current()["notes"]), ("retained tag", "retained note"))

    def test_csv_neutralizes_formulas(self):
        self.application.edit_track(self.identity, {"notes": "=HYPERLINK(\"bad\")", "tags": "+unsafe"})
        exported = self.application.export_csv().decode("utf-8")
        self.assertIn("'=HYPERLINK", exported)
        self.assertIn("'+unsafe", exported)

    def test_tokens_are_memory_only_and_callback_single_use(self):
        self.application.connect_spotify()
        query = urlencode({"state": self.application.pending["state"], "code": "code-one"})
        with patch("app.exchange_code", return_value={"access_token": "SENTINEL_ACCESS", "refresh_token": "SENTINEL_REFRESH"}):
            self.application.oauth_callback(query)
        with self.assertRaises(AppError):
            self.application.oauth_callback(query)
        self.assertTrue(self.application.state()["connection"]["connected"])
        self.assertNotIn(b"SENTINEL", self.application.store.path.read_bytes())
        self.assertFalse(Application(self.root / "data").state()["connection"]["connected"])


class SpotifyTests(unittest.TestCase):
    def test_pkce_scopes_callback_and_invalid_state(self):
        pending, url = begin_authorization("a" * 32, "http://127.0.0.1:8888/callback")
        params = parse_qs(urlsplit(url).query)
        expected = base64.urlsafe_b64encode(hashlib.sha256(pending["verifier"].encode()).digest()).decode().rstrip("=")
        self.assertEqual(params["code_challenge"], [expected])
        self.assertEqual(params["scope"], ["playlist-read-private user-library-read"])
        self.assertNotIn("client_secret", params)
        valid = urlencode({"state": pending["state"], "code": "test-code"})
        self.assertEqual(callback_code(valid, pending), "test-code")
        for query in ("state=wrong&code=test", valid + "&state=duplicate", "code=missing-state"):
            with self.assertRaises(AppError):
                callback_code(query, pending)
        pending["expires"] = time.monotonic() - 1
        with self.assertRaises(AppError):
            callback_code(valid, pending)

    def test_relink_uses_original_id_and_full_pagination(self):
        client = SpotifyClient("a" * 32, {"access_token": "test"})
        song = {"type": "track", "id": "market-id", "linked_from": {"id": "original-id"}, "name": "Example", "artists": [{"name": "Artist"}], "duration_ms": 123000, "album": {"name": "Album"}}
        pages = [{"items": [{"item": song}], "total": 2, "next": "https://api.spotify.com/v1/next"}, {"items": [{"track": song}], "total": 2, "next": None}]
        with patch.object(client, "get", side_effect=pages):
            tracks, unknown = client.tracks(SOURCE)
        self.assertEqual(len(tracks), 2)
        self.assertEqual(unknown, 0)
        self.assertEqual(tracks[0]["spotify_id"], "original-id")
        self.assertEqual(tracks[0]["spotify_current_id"], "market-id")

    def test_incomplete_and_changing_pagination_fail(self):
        client = SpotifyClient("a" * 32, {"access_token": "test"})
        cases = [[{"items": [], "total": 1, "next": None}],
                 [{"items": [], "total": 2, "next": "https://api.spotify.com/v1/page"}, {"items": [], "total": 1, "next": None}],
                 [{"items": [], "total": 0}]]
        for pages in cases:
            with self.subTest(pages=pages), patch.object(client, "get", side_effect=pages), self.assertRaises(AppError):
                client.tracks(SOURCE)

    def test_pagination_cannot_send_token_to_other_origins(self):
        client = SpotifyClient("a" * 32, {"access_token": "test"})
        with patch("spotify_api.request_json") as request:
            for url in ("https://attacker.example/v1/", "http://api.spotify.com/v1/", "https://api.spotify.com@attacker.example/v1/"):
                with self.assertRaises(AppError):
                    client.get(url)
            request.assert_not_called()

    def test_playlist_input_parsing(self):
        for value in (SOURCE, "spotify:playlist:" + SOURCE, "https://open.spotify.com/playlist/" + SOURCE + "?si=shared"):
            self.assertEqual(parse_source(value)[0], SOURCE)
        self.assertEqual(parse_source("Liked Songs"), ("liked", "liked"))
        for value in ("https://attacker.example/playlist/" + SOURCE, "https://spotify.com/short", ""):
            with self.assertRaises(AppError):
                parse_source(value)


class FakeSocket:
    def __init__(self, request):
        self.request = io.BytesIO(request)
        self.output = io.BytesIO()

    def makefile(self, mode, *args):
        return self.request

    def sendall(self, value):
        self.output.write(value)


class RequestGuardTests(Fixture):
    def request(self, method="GET", path="/api/state", payload=None, headers=None):
        data = json.dumps(payload).encode() if payload is not None else b""
        items = {"Host": "127.0.0.1:8888", "Content-Length": str(len(data))}
        if payload is not None:
            items["Content-Type"] = "application/json"
        items.update(headers or {})
        raw = (method + " " + path + " HTTP/1.1\r\n" + "".join(name + ": " + value + "\r\n" for name, value in items.items()) + "\r\n").encode() + data
        socket = FakeSocket(raw)
        server = type("Server", (), {"app": self.application})()
        Handler(socket, ("127.0.0.1", 10000), server)
        response = socket.output.getvalue()
        head, body = response.split(b"\r\n\r\n", 1)
        return int(head.split()[1]), head, body

    def test_state_requires_local_host_and_does_not_enable_cors(self):
        status, headers, body = self.request()
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["csrf_token"], self.application.csrf_token)
        self.assertNotIn(b"Access-Control-Allow-Origin", headers)
        for headers in ({"Host": "evil.example:8888"}, {"Origin": "https://evil.example"}, {"Sec-Fetch-Site": "cross-site"}):
            self.assertEqual(self.request(headers=headers)[0], 403)

    def test_mutation_needs_csrf_and_same_origin(self):
        path, payload = "/api/tracks/%d" % self.identity, {"owned": True}
        self.assertEqual(self.request("PATCH", path, payload)[0], 403)
        token = {"X-CSRF-Token": self.application.csrf_token}
        self.assertEqual(self.request("PATCH", path, payload, {**token, "Origin": "https://evil.example"})[0], 403)
        self.assertFalse(self.current()["owned"])
        self.assertEqual(self.request("PATCH", path, payload, {**token, "Origin": self.application.origin})[0], 200)
        self.assertTrue(self.current()["owned"])

    def test_provider_search_preserves_exact_query_without_changing_records(self):
        self.application.edit_track(self.identity, {"notes": "keep this draft source", "owned": True})
        before = self.application.store.path.read_bytes()
        query = "Bj\u00f6rk & Artist + Guest / Song (Extended Mix) #2"
        status, _, body = self.request(path="/api/provider-search?" + urlencode({"q": query}))
        self.assertEqual(status, 200)
        plan = json.loads(body)
        self.assertEqual(plan["query"], query)
        providers = {item["id"]: item for item in plan["providers"]}
        for name, hostname in (("bandcamp", "bandcamp.com"), ("beatport", "www.beatport.com")):
            item = providers[name]
            url = urlsplit(item["url"])
            self.assertEqual((url.scheme, url.netloc, url.path), ("https", hostname, "/search"))
            self.assertEqual(parse_qs(url.query), {"q": [query]})
            self.assertEqual(item["method"], "native_search")
        traxsource = providers["traxsource"]
        url = urlsplit(traxsource["url"])
        self.assertEqual(url.netloc, "www.google.com")
        self.assertEqual(parse_qs(url.query)["q"], ["site:traxsource.com " + query])
        self.assertEqual(traxsource["method"], "web_search")
        combined = urlsplit(plan["combined_url"])
        self.assertEqual(combined.netloc, "www.google.com")
        combined_query = parse_qs(combined.query)["q"][0]
        self.assertIn(query, combined_query)
        for domain in ("bandcamp.com", "beatport.com", "traxsource.com"):
            self.assertIn("site:" + domain, combined_query)
        self.assertEqual(self.application.store.path.read_bytes(), before)

    def test_provider_search_rejects_invalid_queries(self):
        for query in ("", "   ", "x" * 501, "Artist\x00Title", "Artist\r\nTitle", "Title\x7f"):
            with self.subTest(query=repr(query)):
                self.assertEqual(self.request(path="/api/provider-search?" + urlencode({"q": query}))[0], 400)
        for suffix in ("", "?q=one&q=two", "?q=one&url=https://elsewhere.example", "?a=1&b=2&c=3&d=4&e=5&f=6"):
            self.assertEqual(self.request(path="/api/provider-search" + suffix)[0], 400)

    def test_provider_search_keeps_local_request_guards(self):
        path = "/api/provider-search?q=Example"
        for headers in ({"Host": "other.example:8888"}, {"Origin": "https://other.example"}, {"Sec-Fetch-Site": "cross-site"}):
            self.assertEqual(self.request(path=path, headers=headers)[0], 403)

    def test_request_size_and_static_traversal(self):
        status, _, _ = self.request("POST", "/api/demo", {}, {"X-CSRF-Token": self.application.csrf_token, "Content-Length": str(MAX_BODY + 1)})
        self.assertEqual(status, 413)
        for path in ("/../app.py", "/%2e%2e/app.py", "/%2Fetc/passwd"):
            self.assertEqual(self.request(path=path)[0], 404)


if __name__ == "__main__":
    unittest.main()
