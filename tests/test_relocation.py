"""Moved-file review changes only explicitly selected DJList links."""
import io
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import Application, Handler
from relocation import PLAN_SECONDS
from spotify_api import AppError


SOURCE = "0123456789ABCDEFGHIJKL"


class StreamSocket:
    def __init__(self, request):
        self.input = io.BytesIO(request)
        self.output = io.BytesIO()

    def makefile(self, mode, *args):
        return self.input if "r" in mode else self.output

    def sendall(self, data):
        self.output.write(data)


class RelocationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.old = self.root / "Old Music"
        self.new = self.root / "New Music"
        self.old.mkdir()
        self.app = Application(self.root / "data")
        self.app.change_settings({"music_root": str(self.old)})
        self.app.store.upsert([
            {"spotify_id": "one", "artists": "Example Artist", "title_and_version": "First (Extended Mix)"},
            {"spotify_id": "two", "artists": "Example Artist", "title_and_version": "Second (Original Mix)"}], SOURCE)
        self.ids = [row["id"] for row in self.app.store.rows("real")]
        self.relative = (Path("Folder/First.flac"), Path("Other/Second.aiff"))
        self.bytes = (b"fictional first recording", b"fictional second recording")
        for relative, data in zip(self.relative, self.bytes):
            path = self.old / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        self.app.scan()
        for identity, relative in zip(self.ids, self.relative):
            self.app.edit_track(identity, {"local_file": str(self.old / relative), "owned": False,
                                          "prep_status": "reviewed", "tags": "my tags", "musical_key": "8A",
                                          "bpm": "172", "notes": "keep notes", "cue_notes": "keep cues",
                                          "purchase_url": "https://example.com/my-confirmed-version"})
        self.old.rename(self.new)
        self.app.change_settings({"music_root": str(self.new)})

    def tearDown(self):
        self.app.close()
        self.temp.cleanup()

    def annotations(self):
        with self.app.store.connect() as db:
            return {row["id"]: json.loads(row["annotations"]) for row in db.execute(
                "SELECT id,annotations FROM tracks WHERE namespace='real'")}

    def preview(self):
        return self.app.relocation_preview(str(self.old))

    def test_non_ascii_plan_token_is_rejected_without_changing_links(self):
        plan = self.preview()
        before = self.annotations()
        for token in ("\ud800", "not-a-token-\u00e9"):
            with self.subTest(token=repr(token)), self.assertRaises(AppError) as raised:
                self.app.relocation_apply(token, [self.ids[0]])
            self.assertEqual(raised.exception.status, 409)
        self.assertEqual(self.annotations(), before)
        self.assertEqual(self.app.relocation_apply(plan["token"], [self.ids[0]])["updated"], 1)

    def request(self, path, body, headers=None, method="POST"):
        payload = json.dumps(body).encode()
        values = {"Host": "127.0.0.1:8888", "Content-Type": "application/json", "Content-Length": str(len(payload)), **(headers or {})}
        raw = (method + " " + path + " HTTP/1.1\r\n" + "".join(key + ": " + value + "\r\n" for key, value in values.items()) + "\r\n").encode() + payload
        socket = StreamSocket(raw)
        Handler(socket, ("127.0.0.1", 12001), type("Server", (), {"app": self.app})())
        head, payload = socket.output.getvalue().split(b"\r\n\r\n", 1)
        return int(head.split()[1]), json.loads(payload)

    def auth(self):
        return {"Origin": self.app.origin, "X-CSRF-Token": self.app.csrf_token}

    def test_preview_is_read_only_and_remembers_previous_root(self):
        before = self.annotations()
        result = self.preview()
        self.assertEqual(result["summary"]["eligible"], 2)
        self.assertEqual(result["summary"]["linked"], 2)
        self.assertEqual(result["old_root"], str(self.old))
        self.assertEqual(result["music_root"], str(self.new))
        self.assertTrue(all(row["selectable"] and row["status"] == "available" for row in result["candidates"]))
        self.assertTrue(all("audio identity was not checked" in row["reason"] for row in result["candidates"]))
        self.assertFalse(any("_fingerprint" in row for row in result["candidates"]))
        self.assertEqual(self.annotations(), before)
        self.assertEqual(self.app.state()["relocation"]["previous_music_root"], str(self.old))
        self.assertTrue(self.app.state()["capabilities"]["file_relink"])
        for relative, original in zip(self.relative, self.bytes):
            self.assertEqual((self.new / relative).read_bytes(), original)

    def test_explicit_selection_changes_only_path_and_keeps_later_annotations(self):
        result = self.preview()
        self.app.edit_track(self.ids[0], {"notes": "edited after preview", "tags": "fresh manual tags"})
        before = self.annotations()
        applied = self.app.relocation_apply(result["token"], [self.ids[0]])
        self.assertEqual(applied["updated"], 1)
        expected = {**before[self.ids[0]], "local_file": str(self.new / self.relative[0])}
        self.assertEqual(self.annotations(), {self.ids[0]: expected, self.ids[1]: before[self.ids[1]]})
        self.assertFalse(expected["owned"])
        self.assertEqual(expected["prep_status"], "reviewed")
        again = self.preview()
        self.assertEqual(again["summary"]["already_available"], 1)
        self.assertEqual([row["track_id"] for row in again["candidates"]], [self.ids[1]])

    def test_size_mismatch_and_unavailable_files_are_not_selectable(self):
        (self.new / self.relative[0]).write_bytes(b"different length")
        (self.new / self.relative[1]).unlink()
        result = self.preview()
        self.assertEqual({row["status"] for row in result["candidates"]}, {"size_mismatch", "unavailable"})
        self.assertFalse(any(row["selectable"] for row in result["candidates"]))
        before = self.annotations()
        with self.assertRaises(AppError):
            self.app.relocation_apply(result["token"], self.ids)
        self.assertEqual(self.annotations(), before)

    def test_unindexed_previous_size_is_explicitly_unknown(self):
        with self.app.store.connect() as db:
            db.execute("DELETE FROM files")
        result = self.preview()
        self.assertTrue(all(row["previous_size"] is None and "unknown" in row["reason"] for row in result["candidates"]))
        self.assertEqual(self.app.relocation_apply(result["token"], self.ids)["updated"], 2)

    def test_preview_does_not_probe_old_paths(self):
        original_stat, original_lstat, original_open = Path.stat, Path.lstat, os.open

        def guarded(method):
            def call(path, *args, **kwargs):
                if path == self.old or self.old in path.parents:
                    raise AssertionError("Probed an old location outside the current music root")
                return method(path, *args, **kwargs)
            return call

        def guarded_open(path, *args, **kwargs):
            parsed = Path(path)
            if parsed.is_absolute() and (parsed == self.old or self.old in parsed.parents):
                raise AssertionError("Opened an old location")
            return original_open(path, *args, **kwargs)

        with patch.object(Path, "stat", guarded(original_stat)), patch.object(Path, "lstat", guarded(original_lstat)), \
                patch("relocation.os.open", side_effect=guarded_open), patch("relocation.os.supports_dir_fd", {os.stat, os.open}):
            # The guarded os.open mock must appear in the capability set too.
            import relocation
            relocation.os.supports_dir_fd.add(relocation.os.open)
            result = self.preview()
        self.assertEqual(result["summary"]["eligible"], 2)

    def test_old_root_rejects_traversal_controls_relative_and_filesystem_root(self):
        for old in (None, "relative/music", "/", str(self.old / ".."), str(self.old) + "\n", str(self.old) + "\x00", str(self.new)):
            with self.subTest(old=old), self.assertRaises(AppError):
                self.app.relocation_preview(old)

    def test_symlink_files_and_directories_are_never_selectable(self):
        outside = self.root / "Outside"
        outside.mkdir()
        (outside / "First.flac").write_bytes(self.bytes[0])
        first = self.new / self.relative[0]
        first.unlink()
        first.symlink_to(outside / "First.flac")
        second = self.new / self.relative[1]
        second.unlink()
        second.parent.rmdir()
        (outside / "Second.aiff").write_bytes(self.bytes[1])
        second.parent.symlink_to(outside, target_is_directory=True)
        result = self.preview()
        self.assertTrue(all(row["status"] == "unsafe" and not row["selectable"] for row in result["candidates"]))

    def test_offline_root_or_root_symlink_prevents_preview(self):
        self.new.rename(self.root / "Offline")
        with self.assertRaises(AppError):
            self.preview()
        self.new.symlink_to(self.root / "Offline", target_is_directory=True)
        with self.assertRaises(AppError):
            self.preview()

    def test_changed_original_link_aborts_entire_batch(self):
        result = self.preview()
        self.app.store.patch(self.ids[1], "real", {"local_file": "/changed/elsewhere.aiff"})
        before = self.annotations()
        with self.assertRaises(AppError):
            self.app.relocation_apply(result["token"], self.ids)
        self.assertEqual(self.annotations(), before)

    def test_file_replacement_same_size_and_mtime_is_detected_by_identity(self):
        result = self.preview()
        file = self.new / self.relative[1]
        old_info = file.stat()
        file.rename(file.with_suffix(".backup"))
        file.write_bytes(self.bytes[1])
        os.utime(file, ns=(old_info.st_atime_ns, old_info.st_mtime_ns))
        before = self.annotations()
        with self.assertRaises(AppError):
            self.app.relocation_apply(result["token"], self.ids)
        self.assertEqual(self.annotations(), before)

    def test_symlink_swap_after_preview_aborts_batch(self):
        result = self.preview()
        directory = self.new / self.relative[1].parent
        directory.rename(self.root / "Moved Elsewhere")
        directory.symlink_to(self.root / "Moved Elsewhere", target_is_directory=True)
        before = self.annotations()
        with self.assertRaises(AppError):
            self.app.relocation_apply(result["token"], self.ids)
        self.assertEqual(self.annotations(), before)

    def test_root_change_expiry_new_preview_and_restart_invalidate_plans(self):
        clock = [100.0]
        self.app.relocation.clock = lambda: clock[0]
        expired = self.preview()
        clock[0] += PLAN_SECONDS
        with self.assertRaises(AppError):
            self.app.relocation_apply(expired["token"], self.ids)
        previous = self.preview()
        newest = self.preview()
        with self.assertRaises(AppError):
            self.app.relocation_apply(previous["token"], self.ids)
        other = self.root / "Third Music"
        other.mkdir()
        self.app.change_settings({"music_root": str(other)})
        with self.assertRaises(AppError):
            self.app.relocation_apply(newest["token"], self.ids)
        self.app.change_settings({"music_root": str(self.new)})
        result = self.preview()
        self.app.close()
        self.app = Application(self.root / "data")
        with self.assertRaises(AppError):
            self.app.relocation_apply(result["token"], self.ids)

    def test_success_and_failed_attempts_are_single_use(self):
        result = self.preview()
        self.app.relocation_apply(result["token"], [self.ids[0]])
        with self.assertRaises(AppError):
            self.app.relocation_apply(result["token"], [self.ids[1]])
        result = self.preview()
        with self.assertRaises(AppError):
            self.app.relocation_apply(result["token"], [self.ids[0]])
        with self.assertRaises(AppError):
            self.app.relocation_apply(result["token"], [self.ids[1]])

    def test_invalid_selections_and_tokens_do_not_change_records(self):
        before = self.annotations()
        for selected in ([], [True], [str(self.ids[0])], [self.ids[0], self.ids[0]], [99999], "all", [1] * 501):
            result = self.preview()
            with self.subTest(selected=selected), self.assertRaises(AppError):
                self.app.relocation_apply(result["token"], selected)
        for token in (None, {}, "é", "x" * 129):
            with self.subTest(token=token), self.assertRaises(AppError):
                self.app.relocation_apply(token, self.ids)
        self.assertEqual(self.annotations(), before)

    def test_database_error_rolls_back_all_selected_changes(self):
        result = self.preview()
        before = self.annotations()
        with self.app.store.connect() as db:
            db.execute("CREATE TRIGGER fail_second BEFORE UPDATE OF annotations ON tracks WHEN NEW.id=%d BEGIN SELECT RAISE(ABORT, 'test rollback'); END" % self.ids[1])
        with self.assertRaises(sqlite3.IntegrityError):
            self.app.relocation_apply(result["token"], self.ids)
        self.assertEqual(self.annotations(), before)

    def test_preview_caps_large_batch_and_later_preview_excludes_updated_links(self):
        self.app.store.upsert([{"spotify_id": "extra-%d" % i, "artists": "Example Artist", "title_and_version": "Example %d" % i}
                               for i in range(499)], SOURCE)
        with self.app.store.connect() as db:
            db.execute("UPDATE tracks SET annotations=? WHERE id NOT IN (?,?)",
                       (json.dumps({"local_file": str(self.old / self.relative[0])}), *self.ids))
        result = self.preview()
        self.assertEqual((len(result["candidates"]), result["limit"], result["summary"]["eligible"], result["truncated"]), (500, 500, 501, True))
        self.app.relocation_apply(result["token"], [self.ids[0]])
        result = self.preview()
        self.assertEqual((result["summary"]["already_available"], len(result["candidates"]), result["truncated"]), (1, 500, False))

    def test_source_track_limit_is_rejected_before_file_checks(self):
        with patch("relocation.MAX_TRACKS", 1), self.assertRaises(AppError) as failure:
            self.preview()
        self.assertEqual(failure.exception.status, 413)

    def test_routes_enforce_csrf_arguments_and_demo_boundary(self):
        body = {"old_root": str(self.old)}
        for path in ("/api/relocation-preview", "/api/relocation-apply"):
            self.assertEqual(self.request(path, {})[0], 403)
            self.assertEqual(self.request(path, {}, {**self.auth(), "Origin": "https://outside.example"})[0], 403)
            self.assertEqual(self.request(path, {}, {**self.auth(), "Host": "outside.example"})[0], 403)
        self.assertEqual(self.request("/api/relocation-preview", {**body, "unexpected": True}, self.auth())[0], 400)
        status, result = self.request("/api/relocation-preview", body, self.auth())
        self.assertEqual(status, 200)
        status, result = self.request("/api/relocation-apply", {"token": result["token"], "track_ids": [self.ids[0]]}, self.auth())
        self.assertEqual((status, result["updated"]), (200, 1))
        result = self.preview()
        self.app.demo(True)
        self.assertEqual(self.request("/api/relocation-preview", body, self.auth())[0], 409)
        self.assertEqual(self.request("/api/relocation-apply", {"token": result["token"], "track_ids": [self.ids[1]]}, self.auth())[0], 409)


if __name__ == "__main__":
    unittest.main()
