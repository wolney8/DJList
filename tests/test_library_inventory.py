"""Persistent XML and duplicate-review checks using only disposable files."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import Store
from library_inventory import LibraryInventory
from spotify_api import AppError


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.music = self.root / "Music"
        self.music.mkdir()
        self.store = Store(self.root / "data")
        self.library = LibraryInventory(self.store)
        self.track = {"spotify_id": "example-track", "artists": "Example Artist", "title_and_version": "Night Window (Original Mix)",
                      "duration_ms": 312000, "album": "Example album", "duration": "5:12", "spotify_url": "", "isrc": ""}
        self.store.upsert([self.track], "example-source")
        self.track = self.store.rows("real")[0]

    def tearDown(self):
        self.temporary.cleanup()

    def audio(self, name="Example Artist - Night Window (Original Mix).flac"):
        file = self.music / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(b"Disposable test fixture, not real audio")
        return file

    def xml(self, *rows):
        document = ET.Element("DJ_PLAYLISTS")
        collection = ET.SubElement(document, "COLLECTION", Entries=str(len(rows)))
        for row in rows:
            values = {"Name": "Night Window (Original Mix)", "Artist": "Example Artist", "Album": "Example album",
                      "Tonality": "C minor", "AverageBpm": "130.00", "Genre": "Garage", "TotalTime": "312"}
            values.update({key: str(value) for key, value in row.items()})
            item = ET.SubElement(collection, "TRACK", values)
            ET.SubElement(item, "POSITION_MARK", Name="Intro", Start="0")
        return ET.tostring(document, encoding="unicode")

    def tracks(self):
        return self.store.rows("real")

    def test_xml_snapshot_retains_offline_and_outside_files_without_owning_tracks(self):
        present = self.audio()
        missing = self.music / "missing.flac"
        outside = self.root / "outside.flac"
        outside.write_bytes(b"Outside fixture")
        self.store.patch(self.track["id"], "real", {"tags": "my tag", "notes": "keep", "musical_key": "manual key"})
        annotations = self.tracks()[0]
        before = present.read_bytes()
        summary = self.library.import_xml(self.xml({"Location": present.as_uri()}, {"Location": missing.as_uri()},
                                                   {"Location": outside.as_uri()}), str(self.music))
        self.assertEqual((summary["entry_count"], summary["available"], summary["missing"], summary["outside_root"]), (3, 1, 1, 1))
        self.assertEqual(summary["scan_count"], 1)
        self.assertEqual(self.tracks()[0], annotations)
        self.assertEqual(present.read_bytes(), before)
        with self.store.connect() as db:
            rows = [dict(row) for row in db.execute("SELECT * FROM files")]
        self.assertEqual([row["path"] for row in rows], [str(present)])
        self.assertEqual((rows[0]["musical_key"], rows[0]["bpm"], rows[0]["cue_count"]), ("C minor", "130.00", 1))
        self.music.rename(self.root / "Unplugged")
        restarted = LibraryInventory(Store(self.root / "data"))
        review = restarted.review(self.tracks(), str(self.music))
        self.assertEqual(review["total"], 3)
        self.assertFalse(any(row["available"] for row in review["entries"]))
        self.assertTrue(review["matches"])
        self.assertFalse(self.tracks()[0]["owned"])

    def test_successful_snapshot_replaces_xml_but_preserves_scan_rows_and_annotations(self):
        first = self.audio()
        second = self.audio("Other Artist - Daylight.flac")
        self.library.import_xml(self.xml({"Location": first.as_uri()}), str(self.music))
        self.library.import_xml(self.xml({"Location": second.as_uri(), "Name": "Daylight", "Artist": "Other Artist"}), str(self.music))
        with self.store.connect() as db:
            paths = [row[0] for row in db.execute("SELECT path FROM library_entries")]
            previous = dict(db.execute("SELECT * FROM files WHERE path=?", (str(first),)).fetchone())
        self.assertEqual(paths, [str(second)])
        self.assertEqual((previous["origin"], previous["musical_key"], previous["bpm"]), ("scan", "", ""))
        self.assertTrue(first.is_file())
        self.assertEqual(self.library.status(str(self.music))["entry_count"], 1)
        self.assertEqual(self.library.status(str(self.music))["scan_count"], 2)

    def test_invalid_xml_refresh_preserves_previous_snapshot(self):
        audio = self.audio()
        self.library.import_xml(self.xml({"Location": audio.as_uri()}), str(self.music))
        before = self.library.review(self.tracks(), str(self.music))
        invalid = ["<broken", '<!DOCTYPE x [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><DJ_PLAYLISTS><COLLECTION/></DJ_PLAYLISTS>',
                   '<DJ_PLAYLISTS><COLLECTION Entries="2"/></DJ_PLAYLISTS>',
                   self.xml({"Location": "https://example.invalid/audio.mp3"}),
                   self.xml({"Location": "file:///tmp/../outside.mp3"})]
        for payload in invalid:
            with self.subTest(payload=payload), self.assertRaises(AppError):
                self.library.import_xml(payload, str(self.music))
            self.assertEqual(self.library.review(self.tracks(), str(self.music)), before)
        with patch("library_inventory.MAX_XML_BYTES", 5), self.assertRaises(AppError):
            self.library.import_xml(self.xml({"Location": audio.as_uri()}), str(self.music))
        self.assertEqual(self.library.review(self.tracks(), str(self.music)), before)

    def test_saved_xml_path_refreshes_new_export_and_preserves_snapshot_on_read_failure(self):
        audio = self.audio()
        export = self.root / "collection.xml"
        export.write_text(self.xml({"Location": audio.as_uri()}), encoding="utf-8")
        first = self.library.refresh_xml(str(export), str(self.music))
        self.assertEqual((first["source_name"], first["source_path"]), ("collection.xml", str(export)))
        export.write_text(self.xml({"Location": audio.as_uri(), "AverageBpm": "172", "Tonality": "D minor"}), encoding="utf-8")
        self.library.refresh_xml(str(export), str(self.music))
        entry = self.library.review(self.tracks(), str(self.music))["entries"][0]
        self.assertEqual((entry["bpm"], entry["key"]), ("172", "D minor"))
        export.unlink()
        with self.assertRaises(AppError):
            self.library.refresh_xml(str(export), str(self.music))
        self.assertEqual(self.library.review(self.tracks(), str(self.music))["entries"][0], entry)
        for path in ("relative.xml", str(self.root / "master.db"), ""):
            with self.subTest(path=path), self.assertRaises(AppError):
                self.library.refresh_xml(path, str(self.music))

    def test_prepare_link_uses_latest_xml_after_offline_refresh(self):
        audio = self.audio()
        self.library.import_xml(self.xml({"Location": audio.as_uri()}), str(self.music))
        self.music.rename(self.root / "Unplugged")
        self.library.import_xml(self.xml({"Location": audio.as_uri(), "AverageBpm": "174", "Tonality": "F minor"}), str(self.music))
        with self.assertRaises(AppError):
            self.library.prepare_link(str(audio), str(self.music))
        (self.root / "Unplugged").rename(self.music)
        self.library.prepare_link(str(audio), str(self.music))
        saved = self.store.patch(self.track["id"], "real", {"local_file": str(audio)}, confirmed_file=True)
        self.assertEqual((saved["bpm"], saved["musical_key"], saved["owned"]), ("174", "F minor", True))

    def test_matching_excludes_wrong_artists_and_mixes_and_flags_duration(self):
        rows = [
            {"Location": self.audio("original.flac").as_uri()},
            {"Location": self.audio("tribute.flac").as_uri(), "Artist": "The Example Artist Tribute"},
            {"Location": self.audio("remix.flac").as_uri(), "Name": "Night Window (Other Remix)"},
            {"Location": self.audio("long.flac").as_uri(), "TotalTime": "360"},
        ]
        self.library.import_xml(self.xml(*rows), str(self.music))
        candidates = self.library.matches(self.tracks(), str(self.music))[0]["candidates"]
        self.assertEqual({Path(row["path"]).name for row in candidates}, {"original.flac", "long.flac"})
        self.assertEqual([row["match_status"] for row in candidates], ["likely_match", "check_duration"])
        self.assertFalse(self.tracks()[0]["owned"])

    def test_scanned_filenames_match_without_merging_same_xml_path_twice(self):
        audio = self.audio()
        with self.store.connect() as db:
            db.execute("INSERT INTO files(path,root,size,mtime,title) VALUES (?,?,?,?,?)",
                       (str(audio), str(self.music), audio.stat().st_size, audio.stat().st_mtime, audio.stem))
        matches = self.library.matches(self.tracks(), str(self.music))
        self.assertEqual(matches[0]["candidates"][0]["path"], str(audio))
        self.assertEqual(matches[0]["candidates"][0]["match_status"], "filename_match")
        self.assertIn("Confirm", " ".join(matches[0]["candidates"][0]["reasons"]))
        self.library.import_xml(self.xml({"Location": audio.as_uri()}, {"Location": audio.as_uri()}), str(self.music))
        review = self.library.review(self.tracks(), str(self.music))
        self.assertEqual(review["total"], 1)
        self.assertEqual(review["summary"]["duplicate_locations"], 1)
        self.assertEqual(review["duplicate_groups"], [])

    def test_multiple_spotify_credits_keep_the_primary_artist_for_library_matching(self):
        audio = self.audio()
        self.library.import_xml(self.xml({"Location": audio.as_uri()}), str(self.music))
        multi_credit = {**self.track, "artists": "Example Artist; Featured Artist; Remix Artist"}
        matches = self.library.matches([multi_credit], str(self.music))
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["track_id"], self.track["id"])
        self.assertEqual(matches[0]["candidates"][0]["path"], str(audio))
        self.assertEqual(matches[0]["candidates"][0]["match_status"], "likely_match")
        wrong_primary = {**multi_credit, "artists": "Cover Artist; Example Artist; Remix Artist"}
        self.assertEqual(self.library.matches([wrong_primary], str(self.music)), [])

    def test_library_review_paginates_filters_and_reports_possible_duplicate_files(self):
        rows = [{"Location": self.audio("copy-%d.flac" % number).as_uri()} for number in range(3)]
        rows.extend({"Location": (self.music / ("missing-%d.flac" % number)).as_uri(), "Name": "Different %d" % number} for number in range(6))
        rows.append({"Location": self.audio("other-version.flac").as_uri(), "Name": "Night Window (Extended Mix)"})
        rows.append({"Location": self.audio("other-duration.flac").as_uri(), "TotalTime": "600"})
        self.library.import_xml(self.xml(*rows), str(self.music))
        review = self.library.review(self.tracks(), str(self.music), page=2, page_size=4)
        self.assertEqual((review["total"], review["page"], review["pages"], len(review["entries"])), (11, 2, 3, 4))
        duplicates = self.library.review(self.tracks(), str(self.music), filter_name="duplicates")
        self.assertEqual(duplicates["total"], 3)
        self.assertEqual(duplicates["duplicate_groups"][0]["count"], 3)
        self.assertTrue(all(row["duplicate"] for row in duplicates["entries"]))
        missing = self.library.review(self.tracks(), str(self.music), filter_name="missing", query="Different 2")
        self.assertEqual(missing["total"], 1)
        self.assertFalse(missing["entries"][0]["available"])
        self.assertEqual(self.library.review(self.tracks(), str(self.music), page=100, page_size=4)["page"], 3)
        for kwargs in ({"page": 0}, {"page_size": 101}, {"filter_name": "unsafe"}, {"query": "bad\x00"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(AppError):
                self.library.review(self.tracks(), str(self.music), **kwargs)

    def test_symlink_and_external_locations_never_hydrate_audio_metadata(self):
        outside = self.root / "outside.flac"
        outside.write_bytes(b"Outside fixture")
        symlink = self.music / "alias.flac"
        symlink.symlink_to(outside)
        directory_link = self.music / "elsewhere"
        directory_link.symlink_to(self.root, target_is_directory=True)
        self.library.import_xml(self.xml({"Location": symlink.as_uri()}, {"Location": (directory_link / "outside.flac").as_uri()},
                                        {"Location": outside.as_uri()}), str(self.music))
        review = self.library.review(self.tracks(), str(self.music))
        self.assertFalse(any(row["available"] for row in review["entries"]))
        self.assertFalse(any(row["within_root"] for row in review["entries"]))
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM files").fetchone()[0], 0)
        for path in (symlink, directory_link / "outside.flac", outside):
            with self.subTest(path=path), self.assertRaises(AppError):
                self.library.prepare_link(str(path), str(self.music))

    def test_upgrade_preserves_previous_xml_metadata_without_reimport(self):
        audio = self.audio()
        with self.store.connect() as db:
            db.execute("INSERT INTO files(path,root,size,mtime,title,artist,musical_key,bpm,origin) VALUES (?,?,?,?,?,?,?,?,'rekordbox_xml')",
                       (str(audio), str(self.music), 1, 0, self.track["title_and_version"], self.track["artists"], "8A", "132"))
        upgraded = LibraryInventory(self.store)
        self.assertEqual(upgraded.status(str(self.music))["entry_count"], 1)
        self.music.rename(self.root / "Unplugged")
        upgraded.invalidate()
        candidates = upgraded.matches(self.tracks(), str(self.music))[0]["candidates"]
        self.assertEqual((candidates[0]["key"], candidates[0]["bpm"], candidates[0]["available"]), ("8A", "132", False))
        self.assertEqual(candidates[0]["match_status"], "likely_match")
        self.assertEqual(candidates[0]["identity_source"], "rekordbox_xml")

    def test_upgrade_keeps_missing_xml_identity_as_filename_evidence(self):
        audio = self.audio()
        title_from_filename = self.audio("Night Window (Original Mix).flac")
        with self.store.connect() as db:
            for path, title, artist in ((audio, self.track["title_and_version"], ""),
                                        (title_from_filename, "", self.track["artists"])):
                db.execute("INSERT INTO files(path,root,size,mtime,title,artist,musical_key,bpm,origin) VALUES (?,?,?,?,?,?,?,?,'rekordbox_xml')",
                           (str(path), str(self.music), 1, 0, title, artist, "8A", "132"))
        upgraded = LibraryInventory(self.store)
        entries = {row["path"]: row for row in upgraded.review(self.tracks(), str(self.music))["entries"]}
        self.assertEqual(entries[str(audio)]["artist"], "")
        candidates = upgraded.matches(self.tracks(), str(self.music))[0]["candidates"]
        self.assertEqual({row["path"] for row in candidates}, {str(audio), str(title_from_filename)})
        for candidate in candidates:
            self.assertEqual(candidate["match_status"], "filename_match")
            self.assertEqual(candidate["identity_source"], "filename")
            self.assertLessEqual(candidate["score"], 80)
            self.assertIn("filename", " ".join(candidate["reasons"]))
            self.assertEqual((candidate["key"], candidate["bpm"]), ("8A", "132"))
        self.assertFalse(self.tracks()[0]["owned"])


if __name__ == "__main__":
    unittest.main()
