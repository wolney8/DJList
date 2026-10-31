"""Single-file links retain authorization and annotations through slow tag reads.

Only temporary fixtures and DJList's own database are used. No user audio,
Spotify session or Rekordbox database is opened.
"""
from contextlib import contextmanager
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import Application
from spotify_api import AppError
from test_audio_metadata import flac_bytes


class LinkIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        # Use the canonical root, including on macOS where /tmp is an alias.
        self.root = Path(self.temp.name).resolve()
        self.music = self.root / "Music"
        self.downloads = self.root / "Downloads"
        self.other = self.root / "Other downloads"
        for folder in (self.music, self.downloads, self.other):
            folder.mkdir()
        self.audio = self.downloads / "Purchased track.flac"
        self.payload = flac_bytes()
        self.audio.write_bytes(self.payload)
        self.second = self.music / "Another purchased track.flac"
        self.second.write_bytes(self.payload)
        self.app = Application(self.root / "state")
        self.app.change_settings({"music_root": str(self.music), "download_root": str(self.downloads)})
        self.app.store.upsert([{"spotify_id": "fixture", "title_and_version": "Night Window (Extended Mix)",
                               "artists": "Example Artist", "duration_ms": 193000}], "fixture")
        self.identity = self.current()["id"]

    def tearDown(self):
        self.app.close()
        self.temp.cleanup()

    def current(self):
        return self.app.store.rows("real")[0]

    def assert_conflict(self, action):
        with self.assertRaises(AppError) as raised:
            action()
        self.assertEqual(raised.exception.status, 409)

    @contextmanager
    def preparing(self, action=None):
        """Pause a real link inside prepare_link until the test releases it."""
        entered, release = threading.Event(), threading.Event()
        original = self.app.library.prepare_link
        outcome = {}

        def blocked(path, root):
            entered.set()
            if not release.wait(5):
                raise AssertionError("The test did not release the metadata read.")
            return original(path, root)

        def run():
            try:
                outcome["result"] = action() if action else self.app.link(self.identity, str(self.audio))
            except Exception as error:
                outcome["error"] = error

        with patch.object(self.app.library, "prepare_link", side_effect=blocked):
            worker = threading.Thread(target=run, daemon=True)
            worker.start()
            try:
                self.assertTrue(entered.wait(3), "The link did not reach metadata preparation.")
                yield outcome
            finally:
                release.set()
                worker.join(5)
                self.assertFalse(worker.is_alive(), "The link did not release its work lock.")

    def test_link_blocks_concurrent_settings_demo_and_other_file_links(self):
        with self.preparing() as outcome:
            for label, action in (
                ("settings", lambda: self.app.change_settings({"download_root": str(self.other)})),
                ("demo", lambda: self.app.demo(True)),
                ("link", lambda: self.app.link(self.identity, str(self.second))),
                ("edit path", lambda: self.app.edit_track(self.identity, {"local_file": str(self.second)})),
            ):
                with self.subTest(action=label):
                    self.assert_conflict(action)
        self.assertNotIn("error", outcome)
        self.assertEqual(self.current()["local_file"], str(self.audio))
        self.assertEqual(self.app.namespace(), "real")
        self.assertEqual(self.app.store.settings()["download_root"], str(self.downloads))
        self.assertEqual(self.audio.read_bytes(), self.payload)

    def test_changed_path_edit_holds_the_same_work_lock(self):
        with self.preparing(lambda: self.app.edit_track(self.identity, {"local_file": str(self.audio)})) as outcome:
            self.assert_conflict(lambda: self.app.change_settings({"music_root": str(self.other)}))
            self.assert_conflict(lambda: self.app.demo(True))
        self.assertNotIn("error", outcome)
        self.assertTrue(self.current()["owned"])

    def test_clearing_a_saved_path_is_serialized_with_a_new_link(self):
        self.app.link(self.identity, str(self.second))
        with self.preparing() as outcome:
            self.assert_conflict(lambda: self.app.edit_track(self.identity, {"local_file": ""}))
        self.assertNotIn("error", outcome)
        self.assertEqual(self.current()["local_file"], str(self.audio))

    def test_folder_settings_reject_filesystem_root_and_parent_traversal(self):
        before = self.app.store.settings()
        for setting in ("music_root", "download_root"):
            for value in (self.root.anchor, str(self.music / ".." / "Music")):
                with self.subTest(setting=setting, value=value):
                    with self.assertRaises(AppError):
                        self.app.change_settings({setting: value})
                    self.assertEqual(self.app.store.settings()[setting], before[setting])

    def test_same_thread_root_change_after_prepare_rejects_link(self):
        original = self.app.library.prepare_link

        def changing(path, root):
            original(path, root)
            self.app.change_settings({"download_root": str(self.other)})

        with patch.object(self.app.library, "prepare_link", side_effect=changing):
            self.assert_conflict(lambda: self.app.link(self.identity, str(self.audio)))
        self.assertEqual(self.current()["local_file"], "")
        self.assertFalse(self.current()["owned"])

    def test_same_thread_demo_change_after_prepare_rejects_link(self):
        original = self.app.library.prepare_link

        def changing(path, root):
            original(path, root)
            self.app.demo(True)

        with patch.object(self.app.library, "prepare_link", side_effect=changing):
            self.assert_conflict(lambda: self.app.link(self.identity, str(self.audio)))
        self.assertEqual(self.current()["local_file"], "")
        self.assertFalse(self.current()["owned"])

    def test_same_thread_root_change_after_prepare_rejects_changed_path_edit(self):
        original = self.app.library.prepare_link

        def changing(path, root):
            original(path, root)
            self.app.change_settings({"download_root": str(self.other)})

        with patch.object(self.app.library, "prepare_link", side_effect=changing):
            self.assert_conflict(lambda: self.app.edit_track(self.identity, {"local_file": str(self.audio), "notes": "do not partially save"}))
        self.assertEqual(self.current()["local_file"], "")
        self.assertEqual(self.current()["notes"], "")

    def test_same_size_same_mtime_replacement_after_prepare_rejects_link(self):
        original = self.app.library.prepare_link

        def replacing(path, root):
            original(path, root)
            before = self.audio.stat()
            replacement = self.downloads / "replacement.tmp"
            replacement.write_bytes(self.payload)
            os.utime(replacement, ns=(before.st_atime_ns, before.st_mtime_ns))
            os.replace(replacement, self.audio)

        with patch.object(self.app.library, "prepare_link", side_effect=replacing):
            self.assert_conflict(lambda: self.app.link(self.identity, str(self.audio)))
        self.assertEqual(self.current()["local_file"], "")
        self.assertFalse(self.current()["owned"])
        self.assertEqual(self.audio.read_bytes(), self.payload)

    def test_unchanged_offline_file_allows_annotation_edits_without_metadata_reads(self):
        self.app.link(self.identity, str(self.audio))
        saved_path = self.current()["local_file"]
        self.downloads.rename(self.root / "Disconnected downloads")
        with patch.object(self.app.library, "prepare_link") as prepare:
            self.app.edit_track(self.identity, {"local_file": saved_path, "notes": "Available at the next session", "tags": "warm-up"})
        prepare.assert_not_called()
        current = self.current()
        self.assertEqual((current["local_file"], current["notes"], current["tags"]),
                         (saved_path, "Available at the next session", "warm-up"))
        self.assertTrue(current["owned"])
        self.assertFalse(current["file_available"])

    def test_annotations_remain_editable_during_tag_read_and_are_preserved(self):
        updates = {"local_file": "", "notes": "Concurrent note", "tags": "my tag", "musical_key": "8A", "bpm": "145", "prep_status": "reviewed"}
        with self.preparing() as outcome:
            self.app.edit_track(self.identity, updates)
        self.assertNotIn("error", outcome)
        current = self.current()
        for field in ("notes", "tags", "musical_key", "bpm", "prep_status"):
            self.assertEqual(current[field], updates[field])
        self.assertEqual(current["local_file"], str(self.audio))
        self.assertTrue(current["owned"])

    def test_inferred_prep_status_only_advances_existing_progress(self):
        for status in ("wishlist", "bought", "downloaded", "imported", "reviewed"):
            with self.subTest(status=status):
                self.app.store.patch(self.identity, "real", {"local_file": "", "prep_status": status, "bpm": "121", "musical_key": "manual key"})
                self.app.link(self.identity, str(self.audio))
                row = self.current()
                self.assertEqual(row["prep_status"], "downloaded" if status in {"wishlist", "bought"} else status)
                self.assertEqual((row["bpm"], row["musical_key"]), ("121", "manual key"))

    def test_explicit_prep_status_edit_remains_authoritative(self):
        self.app.store.patch(self.identity, "real", {"prep_status": "reviewed"})
        self.app.edit_track(self.identity, {"local_file": str(self.audio), "prep_status": "bought"})
        self.assertEqual(self.current()["prep_status"], "bought")
        self.assertTrue(self.current()["owned"])


if __name__ == "__main__":
    unittest.main()
