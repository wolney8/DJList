"""Download pairing, atomic confirmation and read boundaries on disposable files."""
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from download_inbox import DownloadInbox
from library_inventory import LibraryInventory
from spotify_api import AppError


class DownloadInboxTests(unittest.TestCase):
    def setUp(self):
        from app import Store
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.downloads = self.root / "Purchased downloads"
        self.music = self.root / "Music"
        self.downloads.mkdir()
        self.music.mkdir()
        self.store = Store(self.root / "data")
        self.store.put_settings({"music_root": str(self.music), "download_root": str(self.downloads)})
        self.library = LibraryInventory(self.store)
        self.store.upsert([
            {"spotify_id": "first", "artists": "Example Artist", "title_and_version": "Night Window (Extended Mix)", "duration_ms": 193000},
            {"spotify_id": "second", "artists": "Example Artist", "title_and_version": "Morning Light (Original Mix)", "duration_ms": 240000},
        ], "test-source")
        self.ids = [row["id"] for row in self.store.rows("real")]
        self.files = [self.downloads / "nested" / "first.flac", self.downloads / "second.mp3"]
        for file in self.files:
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(b"Disposable synthetic fixture, not recorded music.")
        self.metadata = {
            "first.flac": {"title": "Night Window (Extended Mix)", "artist": "Example Artist", "album": "Test release", "genre": "House", "duration_ms": 193000, "bpm": "126", "musical_key": "D minor", "status": "read", "message": "Read embedded tags."},
            "second.mp3": {"title": "Morning Light (Original Mix)", "artist": "Example Artist", "duration_ms": 240000, "bpm": "120", "musical_key": "C major", "status": "read"},
        }
        self.reads = []
        self.clock = [100.0]
        self.inbox = DownloadInbox(self.store, self.library, self.reader, clock=lambda: self.clock[0])

    def tearDown(self):
        self.temporary.cleanup()

    def reader(self, path, root):
        self.assertEqual(Path(root), self.downloads)
        self.assertTrue(Path(path).is_relative_to(self.downloads))
        self.reads.append(str(path))
        return dict(self.metadata.get(Path(path).name, {"status": "empty"}))

    def annotations(self):
        with self.store.connect() as db:
            return {row["id"]: json.loads(row["annotations"]) for row in db.execute("SELECT id,annotations FROM tracks WHERE namespace='real'")}

    def pairs(self, plan):
        return [{"entry_id": entry["id"], "track_id": entry["candidates"][0]["track_id"]}
                for entry in plan["entries"] if entry["candidates"]]

    def first_entry(self, plan):
        return next(entry for entry in plan["entries"] if entry["name"] == "first.flac")

    def test_preview_reads_explicit_download_root_without_changing_tracks_or_files(self):
        before = self.annotations()
        content = {str(file): file.read_bytes() for file in self.files}
        plan = self.inbox.preview()
        self.assertEqual(plan["root"], str(self.downloads))
        self.assertEqual(plan["total"], 2)
        self.assertFalse(plan["truncated"])
        self.assertEqual(len(plan["entries"]), 2)
        self.assertTrue(all(entry["available"] and entry["metadata_source"] == "file_tags" for entry in plan["entries"]))
        self.assertTrue(all(entry["candidates"] and not entry["linked_track_ids"] for entry in plan["entries"]))
        self.assertEqual(self.first_entry(plan)["candidates"][0]["track_id"], self.ids[0])
        self.assertEqual(self.annotations(), before)
        self.assertEqual({str(file): file.read_bytes() for file in self.files}, content)
        self.assertEqual(len(self.reads), 2)

    def test_real_metadata_reader_and_fingerprints_link_a_synthetic_flac(self):
        from test_audio_metadata import flac_bytes
        self.files[0].write_bytes(flac_bytes())
        before = self.files[0].read_bytes()
        self.inbox = DownloadInbox(self.store, self.library)
        plan = self.inbox.preview()
        entry = self.first_entry(plan)
        self.assertTrue(entry["available"])
        self.assertEqual((entry["title"], entry["artist"], entry["bpm"], entry["key"]),
                         ("Night Window (Extended Mix)", "Example Artist", "172", "C minor"))
        self.assertEqual(entry["candidates"][0]["match_status"], "likely_match")
        self.inbox.apply(plan["token"], [{"entry_id": entry["id"], "track_id": self.ids[0]}])
        self.assertEqual(self.annotations()[self.ids[0]]["local_file"], str(self.files[0]))
        self.assertEqual(self.files[0].read_bytes(), before)

    def test_confirmed_pairs_preserve_concurrent_annotations_and_fill_only_empty_fields(self):
        self.store.patch(self.ids[0], "real", {"notes": "before", "tags": "my tag", "bpm": "128", "cue_notes": "keep cues", "purchase_url": "https://example.test/my-link"})
        self.store.patch(self.ids[1], "real", {"prep_status": "reviewed", "musical_key": "manual key"})
        plan = self.inbox.preview()
        self.store.patch(self.ids[0], "real", {"notes": "edited while preview open", "tags": "new manual tag"})
        result = self.inbox.apply(plan["token"], self.pairs(plan))
        self.assertEqual(result["updated"], 2)
        saved = self.annotations()
        first, second = saved[self.ids[0]], saved[self.ids[1]]
        self.assertEqual((first["local_file"], first["owned"], first["prep_status"]), (str(self.files[0]), True, "downloaded"))
        self.assertEqual((first["notes"], first["tags"], first["bpm"], first["musical_key"]), ("edited while preview open", "new manual tag", "128", "D minor"))
        self.assertEqual((first["cue_notes"], first["purchase_url"]), ("keep cues", "https://example.test/my-link"))
        self.assertEqual((second["prep_status"], second["musical_key"], second["bpm"]), ("reviewed", "manual key", "120"))
        self.assertTrue(all(file.is_file() for file in self.files))
        with self.assertRaises(AppError) as error:
            self.inbox.apply(plan["token"], self.pairs(plan))
        self.assertEqual(error.exception.status, 409)

    def test_prep_advancement_is_monotonic(self):
        for state in ("wishlist", "bought", "downloaded", "imported", "reviewed"):
            with self.subTest(state=state):
                self.store.patch(self.ids[0], "real", {"local_file": "", "prep_status": state, "owned": False})
                plan = self.inbox.preview()
                entry = self.first_entry(plan)
                self.inbox.apply(plan["token"], [{"entry_id": entry["id"], "track_id": self.ids[0]}])
                self.assertEqual(self.annotations()[self.ids[0]]["prep_status"], "downloaded" if state in {"wishlist", "bought"} else state)

    def test_wrong_artist_and_mix_are_excluded_and_duration_needs_review(self):
        self.metadata["first.flac"].update(artist="Tribute Artist")
        self.assertEqual(self.first_entry(self.inbox.preview())["candidates"], [])
        self.metadata["first.flac"].update(artist="Example Artist", title="Night Window (Other Remix)")
        self.assertEqual(self.first_entry(self.inbox.preview())["candidates"], [])
        self.metadata["first.flac"].update(title="Night Window (Extended Mix)", duration_ms=250000)
        self.assertEqual(self.first_entry(self.inbox.preview())["candidates"][0]["match_status"], "check_duration")

    def test_separate_version_notes_are_preserved_and_require_review(self):
        self.metadata["first.flac"]["version_notes"] = "Radio edit"
        entry = self.first_entry(self.inbox.preview())
        self.assertEqual(entry["version_notes"], "Radio edit")
        self.assertEqual(entry["candidates"][0]["match_status"], "check_version")
        self.assertIn("Radio edit", " ".join(entry["candidates"][0]["reasons"]))

    def test_filename_fallback_is_explicitly_unverified(self):
        renamed = self.downloads / "Example Artist - Night Window (Extended Mix).flac"
        self.files[0].rename(renamed)
        plan = self.inbox.preview()
        entry = next(entry for entry in plan["entries"] if entry["name"] == renamed.name)
        self.assertEqual(entry["metadata_source"], "filename")
        self.assertEqual(entry["candidates"][0]["match_status"], "filename_match")
        self.assertIn("filename", " ".join(entry["candidates"][0]["reasons"]).lower())

    def test_single_character_title_can_still_match(self):
        with self.store.connect() as db:
            metadata = json.loads(db.execute("SELECT metadata FROM tracks WHERE id=?", (self.ids[0],)).fetchone()[0])
            metadata["title_and_version"] = "A"
            db.execute("UPDATE tracks SET metadata=? WHERE id=?", (json.dumps(metadata), self.ids[0]))
        self.metadata["first.flac"]["title"] = "A"
        self.assertEqual(self.first_entry(self.inbox.preview())["candidates"][0]["track_id"], self.ids[0])

    def test_already_linked_files_and_tracks_cannot_be_selected(self):
        self.store.patch(self.ids[0], "real", {"local_file": str(self.files[0])})
        plan = self.inbox.preview()
        entry = self.first_entry(plan)
        self.assertEqual(entry["linked_track_ids"], [self.ids[0]])
        before = self.annotations()
        with self.assertRaises(AppError):
            self.inbox.apply(plan["token"], [{"entry_id": entry["id"], "track_id": self.ids[0]}])
        self.assertEqual(self.annotations(), before)
        self.store.patch(self.ids[0], "real", {"local_file": str(self.music / "existing.flac")})
        plan = self.inbox.preview()
        entry = self.first_entry(plan)
        self.assertEqual(entry["candidates"][0]["existing_file"], str(self.music / "existing.flac"))
        with self.assertRaises(AppError):
            self.inbox.apply(plan["token"], [{"entry_id": entry["id"], "track_id": self.ids[0]}])

    def test_a_file_linked_to_another_track_after_preview_aborts(self):
        plan = self.inbox.preview()
        entry = self.first_entry(plan)
        self.store.patch(self.ids[1], "real", {"local_file": entry["path"]})
        before = self.annotations()
        with self.assertRaises(AppError) as error:
            self.inbox.apply(plan["token"], [{"entry_id": entry["id"], "track_id": self.ids[0]}])
        self.assertEqual(error.exception.status, 409)
        self.assertEqual(self.annotations(), before)

    def test_one_stale_track_aborts_entire_batch(self):
        plan = self.inbox.preview()
        with self.store.connect() as db:
            metadata = json.loads(db.execute("SELECT metadata FROM tracks WHERE id=?", (self.ids[1],)).fetchone()[0])
            metadata["title_and_version"] = "Another Recording"
            db.execute("UPDATE tracks SET metadata=? WHERE id=?", (json.dumps(metadata), self.ids[1]))
        before = self.annotations()
        with self.assertRaises(AppError):
            self.inbox.apply(plan["token"], self.pairs(plan))
        self.assertEqual(self.annotations(), before)

    def test_changed_or_deleted_original_link_aborts_batch(self):
        for change in ("link", "delete"):
            with self.subTest(change=change):
                plan = self.inbox.preview()
                if change == "link":
                    self.store.patch(self.ids[1], "real", {"local_file": str(self.music / "other.mp3")})
                else:
                    with self.store.connect() as db:
                        db.execute("DELETE FROM tracks WHERE id=?", (self.ids[1],))
                before = self.annotations()
                with self.assertRaises(AppError):
                    self.inbox.apply(plan["token"], self.pairs(plan))
                self.assertEqual(self.annotations(), before)
                if change == "link":
                    self.store.patch(self.ids[1], "real", {"local_file": ""})

    def test_file_replacement_same_size_and_mtime_aborts_batch(self):
        plan = self.inbox.preview()
        file = self.files[1]
        before_info = file.stat()
        data = file.read_bytes()
        file.rename(file.with_suffix(".old"))
        file.write_bytes(data)
        os.utime(file, ns=(before_info.st_atime_ns, before_info.st_mtime_ns))
        before = self.annotations()
        with self.assertRaises(AppError):
            self.inbox.apply(plan["token"], self.pairs(plan))
        self.assertEqual(self.annotations(), before)

    def test_missing_file_aborts_batch(self):
        plan = self.inbox.preview()
        self.files[1].unlink()
        before = self.annotations()
        with self.assertRaises(AppError):
            self.inbox.apply(plan["token"], self.pairs(plan))
        self.assertEqual(self.annotations(), before)

    def test_symlink_directories_files_and_special_files_are_not_read(self):
        outside = self.root / "outside"
        outside.mkdir()
        target = outside / "outside.flac"
        target.write_bytes(b"Do not read")
        (self.downloads / "link.flac").symlink_to(target)
        (self.downloads / "linked-folder").symlink_to(outside, target_is_directory=True)
        os.mkfifo(self.downloads / "pipe.flac")
        plan = self.inbox.preview()
        self.assertEqual(plan["total"], 2)
        self.assertEqual(plan["skipped"], 3)
        self.assertNotIn(str(target), self.reads)

    def test_symlink_swap_after_preview_is_rejected(self):
        plan = self.inbox.preview()
        nested = self.downloads / "nested"
        outside = self.root / "renamed-folder"
        nested.rename(outside)
        nested.symlink_to(outside, target_is_directory=True)
        before = self.annotations()
        with self.assertRaises(AppError):
            self.inbox.apply(plan["token"], self.pairs(plan))
        self.assertEqual(self.annotations(), before)

    def test_root_changes_offline_or_demo_reject_confirmation(self):
        for change in ("setting", "offline", "replacement", "demo"):
            with self.subTest(change=change):
                plan = self.inbox.preview()
                before = self.annotations()
                if change == "setting":
                    self.store.put_settings({"download_root": str(self.music)})
                elif change in {"offline", "replacement"}:
                    self.downloads.rename(self.root / "offline")
                    if change == "replacement":
                        self.downloads.mkdir()
                else:
                    self.store.put_settings({"mode": "demo"})
                with self.assertRaises(AppError):
                    self.inbox.apply(plan["token"], self.pairs(plan))
                self.assertEqual(self.annotations(), before)
                if change == "setting":
                    self.store.put_settings({"download_root": str(self.downloads)})
                elif change in {"offline", "replacement"}:
                    if change == "replacement":
                        self.downloads.rmdir()
                    (self.root / "offline").rename(self.downloads)
                else:
                    self.store.put_settings({"mode": "real"})

    def test_invalid_roots_are_explicit_and_do_not_create_a_plan(self):
        alias = self.root / "alias"
        alias.symlink_to(self.downloads, target_is_directory=True)
        for value in ("", "relative", "/", str(self.root / "missing"), str(alias), str(self.downloads / ".."), str(self.downloads) + "\n", str(self.files[0])):
            with self.subTest(value=value):
                self.store.put_settings({"download_root": value})
                with self.assertRaises(AppError):
                    self.inbox.preview()
                self.assertIsNone(self.inbox.plan)

    def test_expiry_unknown_non_ascii_and_single_use_tokens(self):
        plan = self.inbox.preview()
        for token in (None, 4, "unknown", "\ud800", "token-é"):
            with self.subTest(token=repr(token)), self.assertRaises(AppError):
                self.inbox.apply(token, self.pairs(plan))
        self.assertEqual(self.inbox.apply(plan["token"], self.pairs(plan))["updated"], 2)
        self.store.patch(self.ids[0], "real", {"local_file": ""})
        plan = self.inbox.preview()
        self.clock[0] += 600
        with self.assertRaises(AppError) as error:
            self.inbox.apply(plan["token"], self.pairs(plan))
        self.assertEqual(error.exception.status, 409)
        self.assertEqual(self.annotations()[self.ids[0]]["local_file"], "")

    def test_clear_or_new_preview_invalidates_old_plan(self):
        first = self.inbox.preview()
        self.inbox.clear()
        with self.assertRaises(AppError):
            self.inbox.apply(first["token"], self.pairs(first))
        first = self.inbox.preview()
        second = self.inbox.preview()
        with self.assertRaises(AppError):
            self.inbox.apply(first["token"], self.pairs(first))
        self.assertEqual(self.inbox.apply(second["token"], self.pairs(second))["updated"], 2)

    def test_pair_validation_and_no_arbitrary_paths_or_track_ids(self):
        for case in ("empty", "duplicate_entry", "duplicate_track", "bool_id", "unknown_entry", "unknown_track", "extra_path", "not_list"):
            with self.subTest(case=case):
                plan = self.inbox.preview()
                pairs = self.pairs(plan)
                first = pairs[0]
                if case == "empty":
                    selection = []
                elif case == "duplicate_entry":
                    selection = [first, dict(first)]
                elif case == "duplicate_track":
                    selection = [first, {**pairs[1], "track_id": first["track_id"]}]
                elif case == "bool_id":
                    selection = [{**first, "track_id": True}]
                elif case == "unknown_entry":
                    selection = [{**first, "entry_id": "unknown"}]
                elif case == "unknown_track":
                    selection = [{**first, "track_id": 99999}]
                elif case == "extra_path":
                    selection = [{**first, "path": "/tmp/anything.mp3"}]
                else:
                    selection = first
                before = self.annotations()
                with self.assertRaises(AppError):
                    self.inbox.apply(plan["token"], selection)
                self.assertEqual(self.annotations(), before)

    def test_database_failure_rolls_back_all_selected_pairs(self):
        plan = self.inbox.preview()
        with self.store.connect() as db:
            db.execute("CREATE TRIGGER reject_second BEFORE UPDATE ON tracks WHEN NEW.id=%d BEGIN SELECT RAISE(ABORT, 'fixture rejection'); END" % self.ids[1])
        before = self.annotations()
        with self.assertRaises(sqlite3.IntegrityError):
            self.inbox.apply(plan["token"], self.pairs(plan))
        self.assertEqual(self.annotations(), before)

    def test_changed_during_metadata_read_is_shown_but_not_selectable(self):
        def changing_reader(path, root):
            value = self.reader(path, root)
            if Path(path).name == "first.flac":
                Path(path).write_bytes(b"Changed while tags were being read")
            return value
        self.inbox.metadata_reader = changing_reader
        plan = self.inbox.preview()
        entry = self.first_entry(plan)
        self.assertFalse(entry["available"])
        self.assertEqual(entry["metadata_status"], "changed")
        with self.assertRaises(AppError):
            self.inbox.apply(plan["token"], [{"entry_id": entry["id"], "track_id": self.ids[0]}])

    def test_bounded_preview_and_scan_prioritize_unlinked_files(self):
        for index in range(6):
            (self.downloads / ("more-%d.flac" % index)).write_bytes(b"Synthetic")
        self.store.patch(self.ids[0], "real", {"local_file": str(self.files[0])})
        with patch("download_inbox.BATCH_LIMIT", 3), patch("download_inbox.SCAN_LIMIT", 5):
            plan = self.inbox.preview()
        self.assertEqual(len(plan["entries"]), 3)
        self.assertEqual(plan["total"], 5)
        self.assertTrue(plan["truncated"])
        self.assertFalse(any(entry["linked_track_ids"] for entry in plan["entries"]))
        self.assertEqual(len(self.reads), 3)

    def test_slow_metadata_reads_stop_with_partial_preview(self):
        def slow(path, root):
            value = self.reader(path, root)
            self.clock[0] += 21
            return value
        self.inbox.metadata_reader = slow
        plan = self.inbox.preview()
        self.assertTrue(plan["truncated"])
        self.assertEqual(plan["total"], 2)
        self.assertEqual(len(plan["entries"]), 1)

    def test_demo_preview_does_not_read_files(self):
        self.store.put_settings({"mode": "demo"})
        with self.assertRaises(AppError):
            self.inbox.preview()
        self.assertEqual(self.reads, [])


if __name__ == "__main__":
    unittest.main()
