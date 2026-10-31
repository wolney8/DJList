"""XML cue counts are sourced snapshots, with unknown distinct from zero."""
from pathlib import Path
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import Store
from library_inventory import LibraryInventory
from library_audit import LibraryAudit


class LibraryDetailsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.music = self.root / "Music"
        self.music.mkdir()
        self.store = Store(self.root / "data")
        self.store.put_settings({"mode": "real", "music_root": str(self.music)})
        self.library = LibraryInventory(self.store)
        self.files = []
        for name in ("First.flac", "Second.flac"):
            path = self.music / name
            path.write_bytes(b"audio fixture")
            self.files.append(path)

    def tearDown(self):
        self.temporary.cleanup()

    def xml(self):
        root = ET.Element("DJ_PLAYLISTS")
        collection = ET.SubElement(root, "COLLECTION", Entries="2")
        first = ET.SubElement(collection, "TRACK", Name="Track", Artist="Artist", Location=self.files[0].as_uri())
        ET.SubElement(first, "POSITION_MARK", Name="Intro", Type="0", Start="1.25", Num="0")
        ET.SubElement(first, "POSITION_MARK", Name="Loop", Type="4", Start="32", End="48", Num="1")
        ET.SubElement(first, "TEMPO", Inizio="0", Bpm="128", Metro="4/4", Battito="1")
        ET.SubElement(collection, "TRACK", Name="Track", Artist="Artist", Location=self.files[1].as_uri())
        self.library.import_xml(ET.tostring(root, encoding="unicode"), str(self.music), source_name="test export.xml")

    def test_xml_cues_loops_and_zero_are_distinct(self):
        self.xml()
        entries = {row["path"]: row for row in self.library.review([], str(self.music))["entries"]}
        first = entries[str(self.files[0])]["cue_evidence"]
        second = entries[str(self.files[1])]["cue_evidence"]
        self.assertEqual((first["cue_count"], first["loop_count"], first["grid_count"], first["hot_cue_count"]), (1, 1, 1, 2))
        self.assertEqual(first["markers"][0]["start"], 1.25)
        self.assertEqual(first["markers"][1]["end"], 48)
        self.assertEqual(first["source_name"], "test export.xml")
        self.assertTrue(first["refreshed_at"])
        self.assertEqual(first["status"], "snapshot")
        self.assertFalse(first["stale"])
        self.assertTrue(second["known"])
        self.assertEqual(second["cue_count"], 0)

    def test_no_xml_has_unknown_cues_and_file_details(self):
        file = self.files[0]
        with self.store.connect() as db:
            db.execute("INSERT INTO files(path,root,size,mtime) VALUES (?,?,?,?)",
                       (str(file), str(self.music), file.stat().st_size, file.stat().st_mtime))
        entry = self.library.review([], str(self.music))["entries"][0]
        self.assertFalse(entry["cue_evidence"]["known"])
        self.assertIsNone(entry["cue_evidence"]["cue_count"])
        self.assertEqual(entry["mime_type"], "audio/flac")
        self.assertEqual(entry["size"], len(b"audio fixture"))

    def test_changed_or_offline_file_keeps_evidence_but_marks_it_stale(self):
        self.xml()
        self.files[0].write_bytes(b"changed")
        self.library.invalidate()
        first = next(row for row in self.library.review([], str(self.music))["entries"] if row["path"] == str(self.files[0]))
        self.assertEqual(first["cue_evidence"]["status"], "changed")
        self.assertTrue(first["cue_evidence"]["stale"])
        self.files[0].unlink()
        self.library.invalidate()
        first = next(row for row in self.library.review([], str(self.music))["entries"] if row["path"] == str(self.files[0]))
        self.assertEqual(first["cue_evidence"]["status"], "unavailable")
        self.assertEqual(first["cue_evidence"]["cue_count"], 1)

    def test_duplicate_groups_include_each_copys_cues_and_counts(self):
        self.xml()
        audit = LibraryAudit(self.store, self.library).review(kind="duplicates")
        self.assertEqual(audit["summary"]["duplicate_files"], 2)
        self.assertEqual(audit["summary"]["possible_extra_copies"], 1)
        self.assertEqual(sorted(member["cue_evidence"]["cue_count"] for member in audit["issues"][0]["members"]), [0, 1])

    def test_removed_storage_is_never_previewed_as_inventory(self):
        removed = self.music / ".djlist-removed" / "old.flac"
        removed.parent.mkdir()
        removed.write_bytes(b"removed")
        with self.store.connect() as db:
            db.execute("INSERT INTO files(path,root,size,mtime) VALUES (?,?,?,?)",
                       (str(removed), str(self.music), removed.stat().st_size, removed.stat().st_mtime))
        self.assertEqual(self.library.review([], str(self.music))["total"], 0)


if __name__ == "__main__":
    unittest.main()
