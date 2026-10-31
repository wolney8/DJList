"""Filesystem mutations use only disposable audio fixtures and injected processes."""
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import Store
from file_operations import FileOperations, REMOVED_DIRECTORY, PLAN_SECONDS, _rename_exclusive
from library_inventory import LibraryInventory, _identity
from spotify_api import AppError


class FileOperationsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="djlist-file-actions-")
        self.root = Path(self.temporary.name).resolve()
        self.music = self.root / "Music"
        self.music.mkdir()
        self.inbox = self.root / "Incoming"
        self.inbox.mkdir()
        self.store = Store(self.root / "State")
        self.store.put_settings({"music_root": str(self.music), "download_root": str(self.inbox), "mode": "real"})
        self.library = LibraryInventory(self.store)
        self.clock = [100.0]
        self.process = Mock(return_value={"known": True, "running": []})
        self.files = FileOperations(self.store, self.library, self.process, clock=lambda: self.clock[0])
        self.store.upsert([{"spotify_id": "one", "artists": "Synthetic Artist", "title_and_version": "One Track", "duration_ms": 120000}], "test")
        self.track_id = self.store.rows("real")[0]["id"]

    def tearDown(self):
        self.temporary.cleanup()

    def audio(self, name="_05 One Track.flac", root=None, body=b"synthetic audio bytes"):
        path = (root or self.music) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        with self.store.connect() as db:
            info = path.stat()
            db.execute("INSERT INTO files(path,root,size,mtime,title,artist) VALUES (?,?,?,?,?,?)",
                       (str(path), str(root or self.music), info.st_size, info.st_mtime, "One Track", "Synthetic Artist"))
        return path

    def xml(self, path, cue_count=0):
        document = ET.Element("DJ_PLAYLISTS")
        collection = ET.SubElement(document, "COLLECTION", Entries="1")
        track = ET.SubElement(collection, "TRACK", {"Location": path.as_uri(), "Name": "One Track", "Artist": "Synthetic Artist"})
        for index in range(cue_count):
            ET.SubElement(track, "POSITION_MARK", {"Num": str(index), "Start": str(index * 16), "Type": "0"})
        self.library.import_xml(ET.tostring(document, encoding="unicode"), str(self.music))

    def link(self, path):
        self.store.patch(self.track_id, "real", {"local_file": str(path), "owned": False,
                          "prep_status": "reviewed", "tags": "keep", "notes": "keep my notes", "cue_notes": "keep my cue notes"})

    def annotations(self):
        return json.loads(self.store.get(self.track_id, "real")["annotations"])

    def plan(self, path, action="rename", options=None):
        return self.files.preview(action, [_identity(str(path))], options)

    def apply(self, plan, ids=None):
        return self.files.apply(plan["token"], ids or [item["entry_id"] for item in plan["items"]])

    def test_rename_preserves_bytes_annotations_and_source_xml_records(self):
        source = self.audio()
        self.xml(source, cue_count=2)
        self.link(source)
        before = self.annotations()
        plan = self.plan(source)
        self.assertEqual(plan["items"][0]["after"], str(source.with_name("05 One Track.flac")))
        self.assertEqual(plan["items"][0]["cue_count"], 2)
        self.assertTrue(any("2 cue" in value for value in plan["items"][0]["warnings"]))
        result = self.apply(plan)
        target = Path(result["items"][0]["after"])
        self.assertFalse(source.exists())
        self.assertEqual(target.read_bytes(), b"synthetic audio bytes")
        self.assertEqual(self.annotations(), {**before, "local_file": str(target)})
        with self.store.connect() as db:
            self.assertIsNotNone(db.execute("SELECT 1 FROM library_entries WHERE path=?", (str(source),)).fetchone())
        self.assertEqual(self.files.history()["operations"][0]["status"], "completed")

    def test_remove_is_recoverable_and_keeps_missing_link_and_ownership(self):
        source = self.audio()
        self.link(source)
        before = self.annotations()
        result = self.apply(self.plan(source, "remove"))
        removed = Path(result["items"][0]["after"])
        self.assertIn(REMOVED_DIRECTORY, removed.parts)
        self.assertEqual(removed.read_bytes(), b"synthetic audio bytes")
        self.assertFalse(source.exists())
        self.assertEqual(self.annotations(), before)
        undo = self.files.undo_preview(result["operation_id"])
        self.files.undo_apply(undo["token"], [undo["items"][0]["entry_id"]])
        self.assertTrue(source.exists())
        self.assertFalse(removed.exists())
        self.assertEqual(self.annotations(), before)

    def test_rename_undo_updates_only_current_matching_links(self):
        source = self.audio()
        self.link(source)
        result = self.apply(self.plan(source))
        self.store.patch(self.track_id, "real", {"notes": "newer notes"})
        undo = self.files.undo_preview(result["operation_id"])
        self.files.undo_apply(undo["token"], [undo["items"][0]["entry_id"]])
        self.assertEqual(self.annotations()["local_file"], str(source))
        self.assertEqual(self.annotations()["notes"], "newer notes")
        with self.assertRaises(AppError):
            self.files.undo_preview(result["operation_id"])

    def test_artist_title_pattern_and_explicit_name(self):
        source = self.audio()
        plan = self.plan(source, options={"mode": "artist_title"})
        self.assertTrue(plan["items"][0]["after"].endswith("Synthetic Artist - One Track.flac"))
        plan = self.plan(source, options={"names": {_identity(str(source)): "Chosen.flac"}})
        self.assertTrue(plan["items"][0]["after"].endswith("Chosen.flac"))

    def test_leading_track_numbers_require_explicit_option(self):
        source = self.audio("05 Song.flac")
        self.assertFalse(self.plan(source)["items"][0]["selectable"])
        plan = self.plan(source, options={"strip_track_numbers": True})
        self.assertEqual(Path(plan["items"][0]["after"]).name, "Song.flac")
        source = self.audio("1984.flac")
        self.assertFalse(self.plan(source, options={"strip_track_numbers": True})["items"][0]["selectable"])

    def test_invalid_names_and_options_rejected_without_moves(self):
        source = self.audio()
        for name in ("../secret.flac", "other.mp3", ".hidden.flac", "a/b.flac", "x\\y.flac", "bad\nname.flac", "master.db.flac", " ", "x" * 241 + ".flac"):
            with self.subTest(name=name), self.assertRaises(AppError):
                self.plan(source, options={"names": {_identity(str(source)): name}})
        for options in ({"unknown": True}, {"mode": "all"}, {"strip_underscores": 1}, {"names": []}):
            with self.subTest(options=options), self.assertRaises(AppError):
                self.plan(source, options=options)
        self.assertTrue(source.exists())

    def test_collisions_are_unselectable_and_never_overwritten(self):
        source = self.audio("_Track.flac")
        target = self.audio("Track.flac", body=b"keep destination")
        plan = self.plan(source)
        self.assertFalse(plan["items"][0]["selectable"])
        with self.assertRaises(AppError):
            self.apply(plan)
        self.assertEqual(target.read_bytes(), b"keep destination")
        self.assertTrue(source.exists())

    def test_casefold_batch_collisions_are_unselectable(self):
        one, two = self.audio("_One.flac"), self.audio("_Two.flac")
        ids = [_identity(str(one)), _identity(str(two))]
        plan = self.files.preview("rename", ids, {"names": {ids[0]: "SAME.flac", ids[1]: "same.flac"}})
        self.assertTrue(all(not item["selectable"] for item in plan["items"]))

    def test_changed_file_settings_offline_and_expiry_fail_before_move(self):
        for cause in ("file", "settings", "offline", "expired"):
            with self.subTest(cause=cause):
                source = self.audio("_" + cause + ".flac")
                plan = self.plan(source)
                if cause == "file":
                    source.write_bytes(b"changed")
                elif cause == "settings":
                    self.store.put_settings({"download_root": ""})
                elif cause == "offline":
                    self.music.rename(self.root / "Disconnected")
                else:
                    self.clock[0] += PLAN_SECONDS + 1
                with self.assertRaises(AppError):
                    self.apply(plan)
                if cause == "offline":
                    (self.root / "Disconnected").rename(self.music)
                self.store.put_settings({"download_root": str(self.inbox)})
                self.assertTrue(source.exists())

    def test_symlinks_hardlinks_and_unknown_non_audio_ids_rejected(self):
        source = self.audio()
        alias = self.music / "alias.flac"
        os.link(source, alias)
        with self.assertRaises(AppError):
            self.plan(source)
        alias.unlink()
        source.unlink()
        outside = self.root / "outside.flac"
        outside.write_bytes(b"must stay")
        source.symlink_to(outside)
        with self.assertRaises(AppError):
            self.plan(source)
        with self.assertRaises(AppError):
            self.files.preview("remove", [_identity(str(self.root / "master.db"))])
        self.assertEqual(outside.read_bytes(), b"must stay")

    def test_symlink_ancestor_and_unselected_root_blocked(self):
        source = self.audio("Folder/_song.flac")
        folder = source.parent
        actual = self.root / "Actual"
        folder.rename(actual)
        folder.symlink_to(actual, target_is_directory=True)
        with self.assertRaises(AppError):
            self.plan(source)
        outside = self.audio("_outside.flac", root=self.root)
        with self.assertRaises(AppError):
            self.plan(outside)

    def test_demo_never_probes_files(self):
        source = self.audio()
        self.store.put_settings({"mode": "demo"})
        with patch("file_operations._directory", side_effect=AssertionError("must not inspect")):
            with self.assertRaises(AppError):
                self.plan(source)
            with self.assertRaises(AppError):
                self.files.history()

    def test_running_or_unknown_process_blocks_mutation(self):
        source = self.audio()
        for state in ({"known": False, "running": []}, {"known": True, "running": ["Rekordbox"]}, None):
            self.process.return_value = state
            plan = self.plan(source)
            with self.assertRaises(AppError):
                self.apply(plan)
            self.assertTrue(source.exists())
        self.assertFalse(self.files.history()["operations"])

    def test_single_use_token_explicit_selection_and_partial_undo(self):
        one, two = self.audio("_one.flac"), self.audio("_two.flac")
        ids = [_identity(str(one)), _identity(str(two))]
        plan = self.files.preview("rename", ids)
        result = self.apply(plan, [ids[0]])
        self.assertTrue(two.exists())
        with self.assertRaises(AppError):
            self.apply(plan, [ids[1]])
        self.assertEqual(len(result["items"]), 1)
        undo = self.files.undo_preview(result["operation_id"])
        self.files.undo_apply(undo["token"], [ids[0]])
        self.assertTrue(one.exists())

    def test_batch_failure_rolls_back_and_preserves_annotations(self):
        one, two = self.audio("_one.flac"), self.audio("_two.flac")
        self.link(one)
        before = self.annotations()
        ids = [_identity(str(one)), _identity(str(two))]
        plan = self.files.preview("rename", ids)
        calls = [0]
        def failing(*args):
            calls[0] += 1
            if calls[0] == 2:
                raise OSError("test-only injected failure")
            return _rename_exclusive(*args)
        with patch("file_operations._rename_exclusive", side_effect=failing), self.assertRaises(AppError):
            self.apply(plan)
        self.assertTrue(one.exists())
        self.assertTrue(two.exists())
        self.assertEqual(self.annotations(), before)
        self.assertEqual(self.files.history()["operations"][0]["status"], "rolled_back")

    def test_occupied_original_blocks_undo_without_overwrite(self):
        source = self.audio()
        result = self.apply(self.plan(source, "remove"))
        source.write_bytes(b"a new file")
        with self.assertRaises(AppError):
            self.files.undo_preview(result["operation_id"])
        self.assertEqual(source.read_bytes(), b"a new file")
        self.assertTrue(Path(result["items"][0]["after"]).exists())

    def test_modified_removed_file_blocks_undo(self):
        source = self.audio()
        result = self.apply(self.plan(source, "remove"))
        Path(result["items"][0]["after"]).write_bytes(b"changed later")
        with self.assertRaises(AppError):
            self.files.undo_preview(result["operation_id"])

    def test_history_and_undo_survive_restart(self):
        source = self.audio()
        result = self.apply(self.plan(source, "remove"))
        self.files = FileOperations(self.store, self.library, self.process)
        self.assertTrue(self.files.history()["operations"][0]["can_undo"])
        undo = self.files.undo_preview(result["operation_id"])
        self.files.undo_apply(undo["token"], [undo["items"][0]["entry_id"]])
        self.assertTrue(source.exists())

    def test_rapid_rename_undo_remove_history_uses_subsecond_chronology(self):
        source = self.audio()
        ticks = [0]

        class FastClock(datetime):
            @classmethod
            def now(cls, tz=None):
                ticks[0] += 1
                instant = datetime(2026, 10, 31, 12, 0, 0, tzinfo=timezone.utc) + timedelta(microseconds=ticks[0] * 10)
                return instant.astimezone(tz) if tz else instant.replace(tzinfo=None)

        # Reverse ID order makes second-resolution history sort incorrectly.
        with patch("file_operations.datetime", FastClock), patch("file_operations.secrets.token_hex", side_effect=[
                "ffffffffffffffff", "1111111111111111", "0000000000000000"]):
            rename = self.apply(self.plan(source))
            undo = self.files.undo_preview(rename["operation_id"])
            restored = self.files.undo_apply(undo["token"], [undo["items"][0]["entry_id"]])
            removed = self.apply(self.plan(source, "remove"))
        history = self.files.history()["operations"]
        self.assertEqual([record["id"] for record in history], [
            removed["operation_id"], restored["operation_id"], rename["operation_id"]])
        self.assertTrue(history[0]["can_undo"])
        self.assertFalse(history[1]["can_undo"])
        self.assertEqual(history[2]["status"], "undone")
        self.assertFalse(history[2]["can_undo"])

    def test_unrecognised_removed_directory_refused(self):
        source = self.audio()
        folder = self.music / REMOVED_DIRECTORY
        folder.mkdir()
        (folder / "not-ours").write_bytes(b"keep")
        with self.assertRaises(AppError):
            self.apply(self.plan(source, "remove"))
        self.assertTrue(source.exists())
        self.assertEqual((folder / "not-ours").read_bytes(), b"keep")

    def test_master_db_xml_and_sidecars_are_never_file_actions(self):
        for name in ("master.db", "master.db-wal", "rekordbox.xml", "ANLZ0000.DAT"):
            path = self.music / name
            path.write_bytes(b"private database or analysis")
            with self.store.connect() as db:
                db.execute("INSERT INTO files(path,root,size,mtime) VALUES (?,?,?,?)", (str(path), str(self.music), path.stat().st_size, path.stat().st_mtime))
            with self.assertRaises(AppError):
                self.plan(path, "remove")
            self.assertEqual(path.read_bytes(), b"private database or analysis")

    def test_native_exclusive_rename_does_not_replace_destination(self):
        one, two = self.audio("one.flac"), self.audio("two.flac", body=b"keep")
        descriptor = os.open(self.music, os.O_RDONLY | os.O_DIRECTORY)
        try:
            with self.assertRaises(AppError):
                _rename_exclusive(descriptor, one.name, descriptor, two.name)
        finally:
            os.close(descriptor)
        self.assertTrue(one.exists())
        self.assertEqual(two.read_bytes(), b"keep")

    def test_destination_appearing_at_native_rename_is_never_overwritten(self):
        source = self.audio("_race.flac")
        target = source.with_name("race.flac")
        plan = self.plan(source)
        def raced(*args):
            target.write_bytes(b"concurrent destination")
            return _rename_exclusive(*args)
        with patch("file_operations._rename_exclusive", side_effect=raced), self.assertRaises(AppError):
            self.apply(plan)
        self.assertTrue(source.exists())
        self.assertEqual(target.read_bytes(), b"concurrent destination")

    def test_rollback_never_overwrites_a_new_original_and_retains_recovery_record(self):
        one, two = self.audio("_one.flac"), self.audio("_two.flac")
        ids = [_identity(str(one)), _identity(str(two))]
        plan = self.files.preview("rename", ids)
        calls = [0]
        def raced(*args):
            calls[0] += 1
            if calls[0] == 2:
                one.write_bytes(b"new original")
                raise OSError("test-only second move failed")
            return _rename_exclusive(*args)
        with patch("file_operations._rename_exclusive", side_effect=raced), self.assertRaises(AppError):
            self.apply(plan)
        self.assertEqual(one.read_bytes(), b"new original")
        self.assertEqual(one.with_name("one.flac").read_bytes(), b"synthetic audio bytes")
        self.assertTrue(two.exists())
        record = self.files.history()["operations"][0]
        self.assertEqual(record["status"], "needs_recovery")
        self.assertFalse(record["can_undo"])

    def test_journal_failure_after_move_rolls_back_file(self):
        source = self.audio()
        plan = self.plan(source)
        save = self.files._save
        calls = [0]
        def failing(record):
            calls[0] += 1
            if calls[0] == 2:
                raise OSError("test-only history storage failure")
            save(record)
        with patch.object(self.files, "_save", side_effect=failing), self.assertRaises(AppError):
            self.apply(plan)
        self.assertTrue(source.exists())
        self.assertEqual(self.files.history()["operations"][0]["status"], "rolled_back")

    def test_failed_link_transaction_rolls_back_files_and_links(self):
        source = self.audio()
        self.link(source)
        before = self.annotations()
        plan = self.plan(source)
        with patch.object(self.files, "_update_links", side_effect=OSError("test-only transaction failure")), self.assertRaises(AppError):
            self.apply(plan)
        self.assertTrue(source.exists())
        self.assertEqual(self.annotations(), before)
        self.assertEqual(self.files.history()["operations"][0]["status"], "rolled_back")

    def test_cache_failure_after_commit_does_not_reverse_committed_paths(self):
        source = self.audio()
        self.link(source)
        plan = self.plan(source)
        with patch.object(self.library, "invalidate", side_effect=OSError("test-only cache failure")), self.assertRaises(AppError):
            self.apply(plan)
        target = Path(plan["items"][0]["after"])
        self.assertTrue(target.exists())
        self.assertFalse(source.exists())
        self.assertEqual(self.annotations()["local_file"], str(target))
        self.assertEqual(self.files.history()["operations"][0]["status"], "completed")

    def test_original_parent_replacement_blocks_undo(self):
        source = self.audio("Folder/_song.flac")
        result = self.apply(self.plan(source, "remove"))
        undo = self.files.undo_preview(result["operation_id"])
        source.parent.rename(self.music / "Other Folder")
        source.parent.mkdir()
        with self.assertRaises(AppError):
            self.files.undo_apply(undo["token"], [undo["items"][0]["entry_id"]])
        self.assertTrue(Path(result["items"][0]["after"]).exists())
        self.assertFalse(source.exists())

    def test_metadata_changes_expire_a_filename_review(self):
        source = self.audio()
        self.xml(source)
        plan = self.plan(source, options={"mode": "artist_title"})
        with self.store.connect() as db:
            entry = json.loads(db.execute("SELECT metadata FROM library_entries WHERE path=?", (str(source),)).fetchone()[0])
            entry["artist"] = "Different Artist"
            db.execute("UPDATE library_entries SET metadata=? WHERE path=?", (json.dumps(entry), str(source)))
        with self.assertRaises(AppError):
            self.apply(plan)
        self.assertTrue(source.exists())

    def test_fresh_metadata_corrections_can_name_files_but_are_not_rebound(self):
        source = self.audio()
        from audio_metadata import file_fingerprint
        correction = {"artist": "Correct Artist", "title": "Correct Title"}
        with self.store.connect() as db:
            db.execute("INSERT INTO library_metadata_overrides VALUES (?,?,?,?)", (str(source), json.dumps(correction),
                       json.dumps(file_fingerprint(source, self.music)), "2026-10-31T00:00:00Z"))
        plan = self.plan(source, options={"mode": "artist_title"})
        self.assertTrue(plan["items"][0]["after"].endswith("Correct Artist - Correct Title.flac"))
        self.apply(plan)
        with self.store.connect() as db:
            self.assertIsNotNone(db.execute("SELECT 1 FROM library_metadata_overrides WHERE path=?", (str(source),)).fetchone())
            self.assertIsNone(db.execute("SELECT 1 FROM library_metadata_overrides WHERE path=?", (plan["items"][0]["after"],)).fetchone())

    def test_incoming_folder_removal_and_partial_batch_undo(self):
        one = self.audio("_one.flac", root=self.inbox)
        two = self.audio("_two.flac", root=self.inbox)
        ids = [_identity(str(one)), _identity(str(two))]
        result = self.apply(self.files.preview("remove", ids))
        undo = self.files.undo_preview(result["operation_id"])
        self.files.undo_apply(undo["token"], [ids[0]])
        self.assertTrue(one.exists())
        self.assertFalse(two.exists())
        undo = self.files.undo_preview(result["operation_id"])
        self.assertEqual(len(undo["items"]), 1)
        self.files.undo_apply(undo["token"], [ids[1]])
        self.assertTrue(two.exists())


if __name__ == "__main__":
    unittest.main()
