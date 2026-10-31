"""Library setup, watcher recovery and native chooser request boundaries."""
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from app import Application, Handler
from library_watch import LibraryWatcher
from native_picker import NativePicker
from spotify_api import AppError
from test_audio_metadata import flac_bytes
from test_shop_links import StreamSocket


class LibraryWatcherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.music = self.root / "Music"
        self.music.mkdir()
        self.xml = self.root / "library.xml"
        self.xml.write_text("first export")
        self.settings = {"mode": "real", "auto_library_refresh": True,
                         "music_root": str(self.music), "rekordbox_xml": str(self.xml)}
        self.time = 0
        self.calls = []
        self.invalidate = Mock()

        def refresh(config, media, xml):
            self.calls.append((config, media, xml))
            return {"media": {} if media else None, "xml": {} if xml else None, "errors": []}

        self.refresh = refresh
        self.watch = LibraryWatcher(lambda: dict(self.settings), refresh, self.invalidate, clock=lambda: self.time)

    def tearDown(self):
        self.watch.close()
        self.temp.cleanup()

    def advance(self, seconds=5):
        self.time += seconds
        self.watch.tick()

    def test_startup_and_changed_xml_wait_for_stability_without_repeated_tree_scans(self):
        self.watch.tick()
        self.assertEqual([call[1:] for call in self.calls], [(True, False)])
        self.advance()
        self.assertEqual(self.calls[-1][1:], (False, True))
        count = len(self.calls)
        self.advance()
        self.assertEqual(len(self.calls), count)
        self.xml.write_text("second export")
        self.advance()
        self.assertEqual(len(self.calls), count)
        self.xml.write_text("third export")
        self.advance()
        self.assertEqual(len(self.calls), count)
        self.advance()
        self.assertEqual(self.calls[-1][1:], (False, True))
        self.assertEqual(sum(call[1] for call in self.calls), 1)

    def test_disconnect_invalidates_availability_and_reconnect_triggers_scan(self):
        self.watch.tick()
        self.advance()
        before = self.watch.status()["revision"]
        moved = self.root / "Detached"
        self.music.rename(moved)
        self.advance()
        self.assertGreater(self.watch.status()["revision"], before)
        self.assertIn("unavailable", self.watch.status()["message"])
        count = len(self.calls)
        self.advance()
        self.assertEqual(len(self.calls), count)
        moved.rename(self.music)
        self.advance()
        self.assertEqual(self.calls[-1][1:], (True, False))
        self.assertGreaterEqual(self.invalidate.call_count, 3)

    def test_disabled_and_demo_do_not_probe_paths_or_refresh(self):
        with patch("library_watch._signature") as probe:
            self.settings["auto_library_refresh"] = False
            self.watch.tick()
            self.assertFalse(self.watch.status()["enabled"])
            self.settings.update(mode="demo", auto_library_refresh=True)
            self.advance()
            probe.assert_not_called()
        self.assertFalse(self.calls)
        self.settings["mode"] = "real"
        self.advance()
        self.assertTrue(self.calls)

    def test_busy_refresh_is_retried_without_losing_pending_xml(self):
        self.settings["music_root"] = str(self.root / "offline")
        self.watch.refresh = Mock(side_effect=[{"deferred": True}, {"xml": {}, "media": None, "errors": []}])
        self.watch.tick()
        self.advance()
        self.assertIn("try again", self.watch.status()["message"])
        self.assertFalse(self.watch.status()["running"])
        self.advance()
        self.assertEqual(self.watch.refresh.call_count, 2)
        self.assertIsNotNone(self.watch.status()["last_refresh_at"])

    def test_failure_backoff_starts_after_slow_refresh_completes(self):
        self.settings["rekordbox_xml"] = ""
        calls = []
        def slow_failure(*args):
            calls.append(args)
            self.time += 45
            return {"errors": [{"source": "media", "message": "Temporary read failure"}]}
        self.watch.refresh = slow_failure
        self.watch.tick()
        self.advance()
        self.assertEqual(len(calls), 1)
        self.advance(25)
        self.assertEqual(len(calls), 2)

    def test_manual_success_clears_exhausted_errors_without_remount(self):
        self.settings["rekordbox_xml"] = ""
        callback = Mock(return_value={"errors": [{"source": "media", "message": "Cannot read folder"}]})
        self.watch.refresh = callback
        self.watch.tick()
        self.advance(30)
        self.advance(30)
        self.advance(30)
        self.assertEqual(callback.call_count, 3)
        self.assertTrue(self.watch.status()["errors"])
        info = self.music.stat()
        self.watch.acknowledge(media={"root_identity": [info.st_dev, info.st_ino]})
        self.advance()
        self.assertEqual(callback.call_count, 3)
        self.assertFalse(self.watch.status()["errors"])
        self.assertIn("Watching", self.watch.status()["message"])

    def test_closed_watcher_does_not_start_more_work(self):
        self.watch.close()
        self.watch.tick()
        self.assertFalse(self.calls)


class NativePickerTests(unittest.TestCase):
    def test_only_fixed_native_scripts_run_and_paths_are_drafts(self):
        runner = Mock(return_value=subprocess.CompletedProcess([], 0, "/Volumes/My SSD/DJ Music/\n", ""))
        picker = NativePicker(runner, platform="darwin")
        self.assertEqual(picker.choose("music"), {"cancelled": False, "path": "/Volumes/My SSD/DJ Music"})
        command = runner.call_args.args[0]
        self.assertEqual(command[:2], ["/usr/bin/osascript", "-e"])
        self.assertNotIn("shell", runner.call_args.kwargs)
        with self.assertRaises(AppError):
            picker.choose("music; rm anything")
        self.assertEqual(runner.call_count, 1)

    def test_cancel_timeout_and_failures_release_the_picker(self):
        runner = Mock(side_effect=[subprocess.CompletedProcess([], 1, "", "User canceled. (-128)"),
                                  subprocess.TimeoutExpired("osascript", 120),
                                  subprocess.CompletedProcess([], 1, "", "Permission denied"),
                                  subprocess.CompletedProcess([], 0, "/tmp/example.xml\n", "")])
        picker = NativePicker(runner, platform="darwin")
        self.assertTrue(picker.choose("xml")["cancelled"])
        for _ in range(2):
            with self.assertRaises(AppError):
                picker.choose("xml")
        self.assertEqual(picker.choose("xml")["path"], "/tmp/example.xml")

    def test_unsupported_and_already_open_picker_do_not_launch(self):
        runner = Mock()
        with self.assertRaises(AppError):
            NativePicker(runner, platform="linux").choose("music")
        picker = NativePicker(runner, platform="darwin")
        picker._lock.acquire()
        try:
            with self.assertRaises(AppError):
                picker.choose("downloads")
        finally:
            picker._lock.release()
        runner.assert_not_called()


class LibrarySetupIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.music = self.root / "Music"
        self.music.mkdir()
        self.downloads = self.root / "Downloads"
        self.downloads.mkdir()
        self.audio = self.music / "generic.flac"
        self.audio.write_bytes(flac_bytes())
        self.xml = self.root / "library.xml"
        self.export()
        self.app = Application(self.root / "data")
        self.app.change_settings({"music_root": str(self.music), "rekordbox_xml": str(self.xml),
                                  "download_root": str(self.downloads), "auto_store_lookup": False})
        self.app.store.upsert([{"spotify_id": "setup-fixture", "artists": "Example Artist",
                               "title_and_version": "Night Window (Extended Mix)", "duration_ms": 193000}], "fixture-source")
        self.identity = self.app.store.rows("real")[0]["id"]
        self.app.store.patch(self.identity, "real", {"notes": "Keep my notes", "tags": "my tag", "bpm": "171"})

    def tearDown(self):
        self.app.close()
        self.temp.cleanup()

    def export(self, bpm="174"):
        self.xml.write_text('<DJ_PLAYLISTS><COLLECTION Entries="1"><TRACK Name="Night Window (Extended Mix)" Artist="Example Artist" '
                            'AverageBpm="%s" Tonality="C minor" TotalTime="193" Location="%s"/></COLLECTION></DJ_PLAYLISTS>' % (bpm, self.audio.as_uri()))

    def request(self, path, method="GET", body=None, authorised=True):
        payload = json.dumps(body or {}).encode() if method != "GET" else b""
        headers = {"Host": "127.0.0.1:8888", "Content-Type": "application/json", "Content-Length": str(len(payload))}
        if authorised:
            headers.update({"Origin": self.app.origin, "X-CSRF-Token": self.app.csrf_token})
        raw = (method + " " + path + " HTTP/1.1\r\n" + "".join(k + ": " + v + "\r\n" for k, v in headers.items()) + "\r\n").encode() + payload
        socket = StreamSocket(raw)
        Handler(socket, ("127.0.0.1", 12001), type("Server", (), {"app": self.app})())
        head, data = socket.output.getvalue().split(b"\r\n\r\n", 1)
        return int(head.split()[1]), json.loads(data)

    def test_setup_distinguishes_readable_current_changed_and_failed_xml(self):
        status, initial = self.request("/api/setup-check")
        self.assertEqual(status, 200)
        self.assertEqual({row["id"]: row["status"] for row in initial["checks"]},
                         {"music": "ok", "xml": "warning", "downloads": "ok", "metadata": "ok"})
        before_audio = self.audio.read_bytes()
        self.app.refresh_libraries()
        checked = self.app.setup_check()
        self.assertEqual(checked["checks"][1]["status"], "ok")
        self.assertEqual(checked["summary"]["xml_entries"], 1)
        self.assertEqual(self.audio.read_bytes(), before_audio)
        self.xml.write_text("not an XML export")
        self.assertEqual(self.app.setup_check()["checks"][1]["status"], "warning")
        result = self.app.refresh_libraries(media=False, xml=True)
        self.assertTrue(result["errors"])
        self.assertEqual(self.app.setup_check()["checks"][1]["status"], "warning")
        self.assertEqual(self.app.library.status(str(self.music))["entry_count"], 1)
        self.xml.unlink()
        self.assertEqual(self.app.setup_check()["checks"][1]["status"], "error")

    def test_browse_request_guards_and_demo_preserve_saved_settings(self):
        self.app.picker = Mock()
        self.app.picker.choose.return_value = {"cancelled": False, "path": str(self.downloads)}
        before = self.app.store.settings()
        self.assertEqual(self.request("/api/browse", "POST", {"kind": "music"}, False)[0], 403)
        self.app.picker.choose.assert_not_called()
        self.assertEqual(self.request("/api/browse", "POST", {"kind": "music", "command": "anything"})[0], 400)
        self.assertEqual(self.request("/api/browse", "POST", {"kind": "music"})[0], 200)
        self.assertEqual(self.app.store.settings(), before)
        self.app.demo(True)
        self.assertEqual(self.request("/api/browse", "POST", {"kind": "music"})[0], 409)
        self.assertEqual(self.request("/api/setup-check")[0], 409)
        self.assertEqual(self.request("/api/inbox-preview", "POST")[0], 409)

    def test_native_inbox_routes_confirm_only_explicit_pairs(self):
        purchased = self.downloads / "generic.flac"
        purchased.write_bytes(flac_bytes())
        self.assertEqual(self.request("/api/inbox-preview", "POST", {}, False)[0], 403)
        self.assertEqual(self.request("/api/inbox-preview", "POST", {"root": "/"})[0], 400)
        status, preview = self.request("/api/inbox-preview", "POST")
        self.assertEqual(status, 200)
        self.assertFalse(self.app.store.rows("real")[0]["owned"])
        entry = preview["entries"][0]
        self.assertEqual(entry["candidates"][0]["track_id"], self.identity)
        status, result = self.request("/api/inbox-apply", "POST", {"token": preview["token"],
                                     "selections": [{"entry_id": entry["id"], "track_id": self.identity}]})
        self.assertEqual((status, result["updated"]), (200, 1))
        saved = self.app.store.rows("real")[0]
        self.assertEqual((saved["local_file"], saved["owned"], saved["prep_status"]), (str(purchased), True, "downloaded"))
        self.assertEqual((saved["notes"], saved["tags"], saved["bpm"], saved["musical_key"]), ("Keep my notes", "my tag", "171", "C minor"))
        self.assertEqual(purchased.read_bytes(), flac_bytes())

    def test_automatic_refresh_reads_new_exports_without_editing_track_annotations(self):
        clock = [0]
        self.app.library_watcher.clock = lambda: clock[0]
        before = self.app.store.get(self.identity, "real")["annotations"]
        self.app.library_watcher.tick()
        clock[0] += 5
        self.app.library_watcher.tick()
        self.assertEqual(self.app.library.review([], str(self.music))["entries"][0]["bpm"], "174")
        self.export("176")
        for _ in range(2):
            clock[0] += 5
            self.app.library_watcher.tick()
        self.assertEqual(self.app.library.review([], str(self.music))["entries"][0]["bpm"], "176")
        self.assertEqual(self.app.store.get(self.identity, "real")["annotations"], before)
        self.app.change_settings({"auto_library_refresh": False})
        self.export("180")
        clock[0] += 30
        self.app.library_watcher.tick()
        self.assertFalse(self.request("/api/library-watch")[1]["enabled"])
        self.assertEqual(self.app.library.review([], str(self.music))["entries"][0]["bpm"], "176")

    def test_filesystem_roots_and_traversal_are_not_saved_or_scanned(self):
        for key in ("music_root", "download_root"):
            for value in ("/", str(self.music / "..")):
                with self.subTest(key=key, value=value), self.assertRaises(AppError):
                    self.app.change_settings({key: value})
        self.app.store.put_settings({"music_root": "/"})
        with patch("app.os.walk") as walk, self.assertRaises(AppError):
            self.app.scan()
        walk.assert_not_called()


if __name__ == "__main__":
    unittest.main()
