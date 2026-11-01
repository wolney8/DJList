"""Relocation evidence uses disposable files/XML; never a real Rekordbox DB."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import Store
from file_operations import FileOperations
from library_inventory import LibraryInventory, _identity
from rekordbox_relocation import RekordboxRelocation
from spotify_api import AppError


class RekordboxRelocationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="djlist-rb-relocation-")
        self.root = Path(self.temporary.name).resolve()
        self.music = self.root / "Music"
        self.music.mkdir()
        self.original = self.music / "_05 Track.wav"
        self.original.write_bytes(b"Disposable audio fixture")
        self.xml = self.root / "collection.xml"
        self.store = Store(self.root / "State")
        self.store.put_settings({"mode": "real", "music_root": str(self.music), "rekordbox_xml": str(self.xml)})
        self.library = LibraryInventory(self.store)
        self.files = FileOperations(self.store, self.library, Mock(return_value={"known": True, "running": []}))
        self.queue = RekordboxRelocation(self.store, self.library)
        self.export(self.original)
        self.library.refresh_xml(str(self.xml), str(self.music))

    def tearDown(self):
        self.temporary.cleanup()

    def export(self, path, track_id="123", cues=True, grid=True, playlist=True,
               complete=True, extra_id=None, cue_name="Intro"):
        document = ET.Element("DJ_PLAYLISTS")
        collection = ET.SubElement(document, "COLLECTION", Entries="2" if extra_id else "1")
        track = ET.SubElement(collection, "TRACK", TrackID=track_id, Location=path.as_uri(),
                              Name="Track", Artist="Artist", TotalTime="240")
        if cues:
            ET.SubElement(track, "POSITION_MARK", Num="0", Start="0.00", Type="0", Name=cue_name,
                          Red="255", Green="0", Blue="0")
        if grid:
            ET.SubElement(track, "TEMPO", Inizio="0", Bpm="172", Metro="4/4", Battito="1")
        if extra_id:
            ET.SubElement(collection, "TRACK", TrackID=extra_id, Location=(self.music / "different.wav").as_uri())
        if complete:
            root = ET.SubElement(ET.SubElement(document, "PLAYLISTS"), "NODE", Name="ROOT", Type="0")
            if playlist:
                node = ET.SubElement(root, "NODE", Name="My set", Type="1", KeyType="0", Entries="1")
                ET.SubElement(node, "TRACK", Key=track_id)
        self.xml.write_text(ET.tostring(document, encoding="unicode"), encoding="utf-8")

    def rename(self, path=None, name=None):
        path = path or self.original
        options = {"names": {_identity(str(path)): name}} if name else None
        plan = self.files.preview("rename", [_identity(str(path))], options)
        result = self.files.apply(plan["token"], [plan["items"][0]["entry_id"]])
        return result, Path(result["items"][0]["after"])

    def only(self):
        result = self.queue.status()
        self.assertEqual(result["counts"]["total"], 1)
        return result["entries"][0]

    def test_success_is_atomic_durable_and_only_verified_by_fresh_same_id_xml(self):
        before_xml = self.xml.read_bytes()
        result, target = self.rename()
        self.assertEqual(self.xml.read_bytes(), before_xml)
        first = self.only()
        self.assertEqual(first["status"], "pending")
        self.assertEqual(first["track_id"], "123")
        self.assertEqual(self.queue.verify()["entries"][0]["status"], "pending")
        self.export(target)
        verified = self.queue.verify()["entries"][0]
        self.assertEqual(verified["status"], "verified")
        self.assertTrue(all(verified["evidence"].values()))
        restarted = RekordboxRelocation(Store(self.root / "State"), self.library)
        self.assertEqual(restarted.status()["entries"][0]["status"], "verified")
        self.assertEqual(target.read_bytes(), b"Disposable audio fixture")
        self.assertFalse(self.queue.status()["direct_database_write"])

    def test_new_record_at_new_path_is_not_accepted_as_relocation(self):
        _, target = self.rename()
        self.export(target, track_id="999")
        result = self.queue.verify()["entries"][0]
        self.assertEqual(result["status"], "needs_review")
        self.assertFalse(result["evidence"]["same_track_id"])

    def test_old_id_at_wrong_path_remains_pending(self):
        self.rename()
        self.export(self.original)
        self.assertEqual(self.queue.verify()["entries"][0]["status"], "pending")

    def test_lost_cue_or_changed_name_is_not_hidden_by_equal_count(self):
        _, target = self.rename()
        self.export(target, cue_name="Changed")
        result = self.queue.verify()["entries"][0]
        self.assertEqual(result["status"], "needs_review")
        self.assertTrue(result["evidence"]["same_track_id"])
        self.assertFalse(result["evidence"]["cues_preserved"])

    def test_lost_grid_or_playlist_blocks_verified_label(self):
        _, target = self.rename()
        self.export(target, grid=False)
        self.assertFalse(self.queue.verify()["entries"][0]["evidence"]["grid_preserved"])
        self.export(target, playlist=False)
        result = self.queue.verify()["entries"][0]
        self.assertFalse(result["evidence"]["playlists_preserved"])
        self.assertEqual(result["status"], "needs_review")

    def test_missing_playlists_is_unknown_not_preserved(self):
        _, target = self.rename()
        self.export(target, complete=False)
        self.assertEqual(self.queue.verify()["entries"][0]["status"], "needs_review")

    def test_duplicate_track_ids_are_not_a_unique_identity(self):
        _, target = self.rename()
        self.export(target, extra_id="123")
        self.assertEqual(self.queue.verify()["entries"][0]["status"], "needs_review")

    def test_two_records_for_one_audio_path_require_manual_review(self):
        self.export(self.original, extra_id="999")
        document = ET.fromstring(self.xml.read_text())
        document.find("COLLECTION")[1].set("Location", self.original.as_uri())
        self.xml.write_text(ET.tostring(document, encoding="unicode"))
        self.library.refresh_xml(str(self.xml), str(self.music))
        self.rename()
        self.assertEqual(self.only()["status"], "needs_review")

    def test_chained_rename_preserves_original_id_and_final_target(self):
        first, intermediate = self.rename()
        second, final = self.rename(intermediate, "Final.wav")
        row = self.only()
        self.assertEqual(row["original_path"], str(self.original))
        self.assertEqual(row["current_path"], str(final))
        self.assertEqual(row["operation_ids"], [first["operation_id"], second["operation_id"]])
        self.export(final)
        self.assertEqual(self.queue.verify()["entries"][0]["status"], "verified")

    def test_undo_after_rekordbox_relocated_requires_reverse_relocation(self):
        operation, target = self.rename()
        self.export(target)
        self.assertEqual(self.queue.verify()["entries"][0]["status"], "verified")
        plan = self.files.undo_preview(operation["operation_id"])
        self.files.undo_apply(plan["token"], [plan["items"][0]["entry_id"]])
        self.assertEqual(self.only()["current_path"], str(self.original))
        self.assertEqual(self.only()["status"], "pending")
        self.assertEqual(self.queue.verify()["entries"][0]["status"], "pending")
        self.export(self.original)
        self.assertEqual(self.queue.verify()["entries"][0]["status"], "verified")

    def test_remove_hides_relocate_target_until_restored(self):
        _, target = self.rename()
        plan = self.files.preview("remove", [_identity(str(target))])
        operation = self.files.apply(plan["token"], [plan["items"][0]["entry_id"]])
        row = self.only()
        self.assertEqual(row["status"], "removed")
        self.assertFalse(row["can_relocate"])
        plan = self.files.undo_preview(operation["operation_id"])
        self.files.undo_apply(plan["token"], [plan["items"][0]["entry_id"]])
        self.assertEqual(self.only()["current_path"], str(target))
        self.assertEqual(self.only()["status"], "pending")

    def test_offline_drive_and_changed_file_are_not_verified(self):
        _, target = self.rename()
        self.export(target)
        self.music.rename(self.root / "Disconnected")
        self.assertEqual(self.queue.verify()["entries"][0]["status"], "blocked")
        (self.root / "Disconnected").rename(self.music)
        target.write_bytes(b"Different audio")
        self.assertEqual(self.queue.verify()["entries"][0]["status"], "blocked")

    def test_changed_settings_do_not_rebind_existing_queue(self):
        _, target = self.rename()
        self.store.put_settings({"rekordbox_db": str(self.root / "Other" / "master.db")})
        self.assertEqual(self.only()["status"], "blocked")

    def test_other_xml_source_does_not_verify_the_original_collection(self):
        _, target = self.rename()
        other = self.root / "other.xml"
        self.export(target)
        other.write_bytes(self.xml.read_bytes())
        self.store.put_settings({"rekordbox_xml": str(other)})
        self.assertEqual(self.queue.verify()["entries"][0]["status"], "blocked")

    def test_new_xml_snapshot_invalidates_previous_verified_display(self):
        _, target = self.rename()
        self.export(target)
        self.queue.verify()
        self.export(target, cues=False)
        self.library.refresh_xml(str(self.xml), str(self.music))
        self.assertEqual(self.only()["status"], "pending")

    def test_xml_changed_during_verification_does_not_commit_success(self):
        _, target = self.rename()
        self.export(target)
        original = self.queue._file_state
        changed = [False]
        def inspect(row, settings):
            if not changed[0]:
                changed[0] = True
                self.export(target, cues=False)
            return original(row, settings)
        with patch.object(self.queue, "_file_state", side_effect=inspect):
            with self.assertRaises(AppError):
                self.queue.verify()
        with self.store.connect() as db:
            record = json.loads(db.execute("SELECT metadata FROM rekordbox_relocations").fetchone()[0])
        self.assertIsNone(record["verification"])

    def test_demo_cannot_review_or_verify_real_paths(self):
        self.rename()
        self.store.put_settings({"mode": "demo"})
        with self.assertRaises(AppError):
            self.queue.status()
        with self.assertRaises(AppError):
            self.queue.verify()

    def test_legacy_journal_recovered_without_inventing_track_id(self):
        operation, target = self.rename()
        with self.store.connect() as db:
            db.execute("DELETE FROM rekordbox_relocations")
            db.execute("DELETE FROM rekordbox_relocation_events")
            row = json.loads(db.execute("SELECT metadata FROM file_operations WHERE id=?", (operation["operation_id"],)).fetchone()[0])
            row["items"][0].pop("_rekordbox_relocation", None)
            db.execute("UPDATE file_operations SET metadata=? WHERE id=?", (json.dumps(row), operation["operation_id"]))
        result = self.only()
        self.assertEqual(result["status"], "needs_review")
        self.assertEqual(result["track_id"], "")
        self.assertEqual(self.only()["id"], result["id"])

    def test_failure_before_commit_does_not_leave_false_pending_move(self):
        plan = self.files.preview("rename", [_identity(str(self.original))])
        with patch("file_operations._rename_exclusive", side_effect=OSError("injected failure")):
            with self.assertRaises(AppError):
                self.files.apply(plan["token"], [plan["items"][0]["entry_id"]])
        self.assertEqual(self.queue.status()["counts"]["total"], 0)
        self.assertTrue(self.original.exists())


if __name__ == "__main__":
    unittest.main()
