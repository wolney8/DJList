"""Library health never deletes audio or rewrites Rekordbox records."""
import csv
import io
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import Store
from library_audit import LibraryAudit, PLAN_SECONDS
from library_inventory import LibraryInventory
from spotify_api import AppError


class LibraryAuditTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.music = self.root / "Music"
        self.music.mkdir()
        self.downloads = self.root / "Inbox"
        self.downloads.mkdir()
        self.store = Store(self.root / "data")
        self.store.put_settings({"music_root": str(self.music), "download_root": str(self.downloads), "mode": "real"})
        self.library = LibraryInventory(self.store)
        self.clock = [100.0]
        self.audit = LibraryAudit(self.store, self.library, clock=lambda: self.clock[0])
        self.store.upsert([{"spotify_id": "one", "artists": "Example Artist", "title_and_version": "Night Window (Extended Mix)",
                           "duration_ms": 193000},
                          {"spotify_id": "two", "artists": "Example Artist", "title_and_version": "Morning Window (Original Mix)",
                           "duration_ms": 193000}], "test-source")
        self.ids = [row[0] for row in self.rows()]

    def tearDown(self):
        self.temporary.cleanup()

    def rows(self):
        with self.store.connect() as db:
            return list(db.execute("SELECT id,metadata,annotations FROM tracks WHERE namespace='real' ORDER BY id"))

    def annotations(self, identity=None):
        identity = identity or self.ids[0]
        return json.loads(self.store.get(identity, "real")["annotations"])

    def audio(self, name, content=b"synthetic complete audio file", title="Night Window (Extended Mix)", artist="Example Artist", duration=193):
        file = self.music / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(content)
        return {"Location": file.as_uri(), "Name": title, "Artist": artist, "TotalTime": str(duration)}

    def xml(self, *rows):
        document = ET.Element("DJ_PLAYLISTS")
        collection = ET.SubElement(document, "COLLECTION", Entries=str(len(rows)))
        for row in rows:
            ET.SubElement(collection, "TRACK", {"Name": "Night Window (Extended Mix)", "Artist": "Example Artist", "TotalTime": "193", **row})
        text = ET.tostring(document, encoding="unicode")
        self.library.import_xml(text, str(self.music))
        return text

    def missing_link(self, name="old/name.flac", identity=None):
        path = self.music / name
        identity = identity or self.ids[0]
        self.store.patch(identity, "real", {"local_file": str(path), "owned": False, "prep_status": "reviewed",
                         "tags": "keep my tag", "notes": "keep my note", "musical_key": "C minor", "bpm": "172",
                         "cue_notes": "keep cues", "purchase_url": "https://example.test/purchase"})
        return path

    def duplicate_group(self, *contents):
        rows = [self.audio("copy%d.flac" % index, content) for index, content in enumerate(contents or (b"same", b"same"))]
        self.xml(*rows)
        group = self.audit.review(kind="duplicates")["issues"][0]
        return group, [self.music / ("copy%d.flac" % index) for index in range(len(rows))]

    def test_missing_xml_and_saved_links_are_visible_without_claiming_ownership(self):
        present = self.audio("new/location.flac")
        missing = self.music / "lost.flac"
        self.xml(present, {"Location": missing.as_uri()})
        old = self.missing_link()
        before = self.annotations()
        review = self.audit.review(kind="missing")
        self.assertEqual((review["total"], review["summary"]["missing_files"], review["summary"]["linked_missing_tracks"]), (2, 2, 1))
        xml_issue = next(row for row in review["issues"] if row["path"] == str(missing))
        link_issue = next(row for row in review["issues"] if row["path"] == str(old))
        self.assertEqual(xml_issue["source"], "rekordbox_xml")
        self.assertFalse(xml_issue["can_relink"])
        self.assertTrue(link_issue["can_relink"])
        self.assertEqual(link_issue["candidates"][0]["path"], str(self.music / "new/location.flac"))
        self.assertIn("Rekordbox locations must be updated", xml_issue["reason"])
        self.assertEqual(self.annotations(), before)

    def test_disconnected_root_is_offline_not_individually_missing(self):
        self.xml(self.audio("file.flac"), {"Location": (self.music / "lost.flac").as_uri()})
        self.missing_link()
        self.music.rename(self.root / "Disconnected")
        review = self.audit.review(kind="offline")
        self.assertFalse(review["root_available"])
        self.assertEqual(review["summary"]["missing_files"], 0)
        self.assertEqual(review["total"], 3)
        self.assertTrue(review["warnings"])
        self.assertTrue(all(not row["candidates"] for row in review["issues"]))

    def test_outside_paths_are_not_probed_and_remain_report_only(self):
        outside = self.root / "Unselected" / "secret.flac"
        self.xml({"Location": outside.as_uri()})
        self.store.patch(self.ids[0], "real", {"local_file": str(outside)})
        original = Path.stat
        def guarded(path, *args, **kwargs):
            if str(path) == str(outside):
                raise AssertionError("Outside file was probed")
            return original(path, *args, **kwargs)
        with patch.object(Path, "stat", guarded):
            result = self.audit.review(kind="outside_root")
        self.assertEqual(result["total"], 1)
        self.assertFalse(result["issues"][0]["can_relink"])
        with self.assertRaises(AppError):
            self.audit.preview_relink(self.ids[0])

    def test_download_folder_links_are_checked_only_inside_explicit_folder(self):
        file = self.downloads / "purchased.flac"
        file.write_bytes(b"purchased")
        self.store.patch(self.ids[0], "real", {"local_file": str(file)})
        self.assertEqual(self.audit.review()["total"], 0)
        file.unlink()
        self.assertEqual(self.audit.review(kind="missing")["total"], 1)
        self.downloads.rename(self.root / "Offline Inbox")
        self.assertEqual(self.audit.review(kind="offline")["total"], 1)

    def test_filters_pagination_and_csv_formula_protection(self):
        rows = [{"Location": (self.music / ("missing%d.flac" % i)).as_uri(), "Name": "=untrusted" if i == 0 else "Track %d" % i,
                 "Artist": "+artist"} for i in range(7)]
        self.xml(*rows)
        result = self.audit.review(kind="missing", page=2, page_size=3)
        self.assertEqual((result["page"], result["pages"], result["total"], len(result["issues"])), (2, 3, 7, 3))
        self.assertEqual(self.audit.review(query="Track 3")["total"], 1)
        data = list(csv.reader(io.StringIO(self.audit.export_report(kind="missing"))))
        row = next(row for row in data[1:] if "untrusted" in row[1])
        self.assertEqual(row[1:3], ["'=untrusted", "'+artist"])
        for kwargs in ({"kind": "x"}, {"page": True}, {"page": 0}, {"page_size": 101}, {"query": "bad\x00"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(AppError):
                self.audit.review(**kwargs)
        with patch("library_audit.MAX_REPORT_ROWS", 1), self.assertRaises(AppError):
            self.audit.export_report()

    def test_malformed_saved_inventory_has_explicit_error_without_modification(self):
        with self.store.connect() as db:
            db.execute("INSERT INTO library_entries VALUES (?,?)", (str(self.music / "bad.flac"), "not json"))
        before = self.annotations()
        with self.assertRaises(AppError) as error:
            self.audit.review()
        self.assertEqual(error.exception.status, 409)
        self.assertEqual(self.annotations(), before)

    def test_audit_bounds_initial_inventory_and_only_loads_relevant_hash_cache(self):
        rows = [self.audio("%d.flac" % index) for index in range(4)]
        self.xml(*rows)
        paths = [str(self.music / ("%d.flac" % index)) for index in range(4)]
        with self.store.connect() as db:
            db.execute("UPDATE library_entries SET metadata='unread bad JSON' WHERE path=?", (paths[3],))
            db.executemany("INSERT INTO library_audit_hashes VALUES (?,?,?,?,?)", [
                (path, str(self.music), "{}", "a" * 64, "2026-01-01T00:00:00Z")
                for path in paths + [str(self.music / ("old-cache-%d.flac" % index)) for index in range(100)]])
        original = __import__("library_inventory")._path_info
        seen = set()

        def bounded(path, root):
            self.assertIn(path, set(paths[:3]))  # Cap plus one sentinel only.
            seen.add(path)
            return original(path, root)

        with patch("library_audit.MAX_ENTRIES", 2), patch("library_inventory._path_info", side_effect=bounded):
            result = self.audit.review()
            loaded = self.audit._load()
        self.assertTrue(result["truncated"])
        self.assertEqual(result["summary"]["inventory_files"], 2)
        self.assertTrue(any("limited" in warning for warning in result["warnings"]))
        self.assertEqual(seen, set(paths[:3]))
        self.assertEqual(set(loaded[4]), set(paths[:2]))

    def test_malformed_track_is_skipped_and_report_labels_incomplete_review(self):
        with self.store.connect() as db:
            db.execute("UPDATE tracks SET metadata=? WHERE id=?", (json.dumps({"artists": ["invalid"], "title_and_version": "test"}), self.ids[0]))
        result = self.audit.review()
        self.assertTrue(result["truncated"])
        self.assertIn("malformed", result["warnings"][0])
        report = list(csv.reader(io.StringIO(self.audit.export_report())))
        self.assertEqual(report[1][0], "review_warning")
        with self.assertRaises(AppError) as error:
            self.audit.preview_relink(self.ids[0])
        self.assertEqual(error.exception.status, 409)

    def test_relink_rejects_malformed_annotations_after_preview(self):
        self.xml(self.audio("current.flac"))
        self.missing_link()
        plan = self.audit.preview_relink(self.ids[0])
        with self.store.connect() as db:
            db.execute("UPDATE tracks SET annotations='[]' WHERE id=?", (self.ids[0],))
        with self.assertRaises(AppError) as error:
            self.audit.apply_relink(plan["token"], plan["candidates"][0]["entry_id"])
        self.assertEqual(error.exception.status, 409)
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT annotations FROM tracks WHERE id=?", (self.ids[0],)).fetchone()[0], "[]")

    def test_hash_cache_with_invalid_digest_is_not_accepted(self):
        group, _ = self.duplicate_group(b"same", b"same")
        self.audit.verify_duplicates(group["group_id"])
        with self.store.connect() as db:
            db.execute("UPDATE library_audit_hashes SET sha256=?", ("x" * 64,))
        result = self.audit.verify_duplicates(group["group_id"])
        self.assertEqual(result["cached_files"], 0)
        self.assertEqual(result["bytes_read"], 8)

    def test_separate_versions_and_durations_do_not_become_duplicate_groups(self):
        self.xml(self.audio("one.flac"), self.audio("two.flac", title="Night Window (Radio Edit)"),
                 self.audio("three.flac", duration=400))
        self.assertEqual(self.audit.review(kind="duplicates")["total"], 0)
        self.xml(self.audio("one.flac"), self.audio("two.flac"))
        with self.store.connect() as db:
            row = db.execute("SELECT metadata FROM library_entries WHERE path=?", (str(self.music / "two.flac"),)).fetchone()
            value = json.loads(row[0]); value["version_notes"] = "Radio Edit"
            db.execute("UPDATE library_entries SET metadata=? WHERE path=?", (json.dumps(value), value["path"]))
        self.assertEqual(self.audit.review(kind="duplicates")["total"], 0)

    def test_same_physical_file_is_not_counted_as_two_duplicates(self):
        row = self.audio("one.flac")
        two = self.music / "two.flac"
        os.link(self.music / "one.flac", two)
        self.xml(row, {"Location": two.as_uri()})
        self.assertEqual(self.audit.review(kind="duplicates")["total"], 0)

    def test_exact_file_hashes_are_explicit_cached_and_leave_audio_unchanged(self):
        group, paths = self.duplicate_group(b"same complete bytes", b"same complete bytes")
        self.assertEqual(group["verification"]["status"], "unchecked")
        before = [path.read_bytes() for path in paths]
        result = self.audit.verify_duplicates(group["group_id"])
        self.assertEqual(result["status"], "byte_identical")
        self.assertEqual(result["bytes_read"], sum(map(len, before)))
        self.assertEqual(result["groups"][0]["count"], 2)
        self.assertTrue(all(member["entry_id"] for member in result["members"]))
        with patch.object(self.audit, "_hash", side_effect=AssertionError("cached file reread")):
            again = self.audit.verify_duplicates(group["group_id"])
        self.assertEqual((again["cached_files"], again["bytes_read"]), (2, 0))
        self.assertEqual(self.audit.review()["summary"]["byte_identical_groups"], 1)
        self.assertEqual([path.read_bytes() for path in paths], before)

    def test_changed_tags_or_audio_are_different_not_a_duplicate_deletion_instruction(self):
        group, paths = self.duplicate_group(b"same", b"same", b"else")
        result = self.audit.verify_duplicates(group["group_id"])
        self.assertEqual(result["status"], "different")
        self.assertEqual(result["groups"][0]["count"], 2)
        self.assertIn("Different tags, formats or audio", result["message"])
        self.assertTrue(all(path.exists() for path in paths))

    def test_replaced_file_invalidates_hash_cache_even_same_size_and_mtime(self):
        group, paths = self.duplicate_group(b"same", b"same")
        self.audit.verify_duplicates(group["group_id"])
        before = paths[1].stat()
        replacement = self.music / "replace.flac"
        replacement.write_bytes(b"else")
        os.utime(replacement, ns=(before.st_atime_ns, before.st_mtime_ns))
        replacement.replace(paths[1])
        result = self.audit.verify_duplicates(group["group_id"])
        self.assertEqual(result["status"], "different")
        self.assertEqual(result["cached_files"], 1)
        self.assertEqual(result["bytes_read"], 4)

    def test_hash_budget_and_missing_members_are_explicitly_partial(self):
        group, paths = self.duplicate_group(b"first", b"other")
        with patch("library_audit.MAX_HASH_BYTES", 5):
            result = self.audit.verify_duplicates(group["group_id"])
        self.assertEqual(result["status"], "partial")
        self.assertTrue(result["partial"])
        self.assertEqual(result["bytes_read"], 5)
        paths[1].unlink()
        result = self.audit.verify_duplicates(group["group_id"])
        self.assertTrue(result["partial"])
        self.assertEqual(result["members"][1]["status"], "unavailable")

    def test_hash_file_count_and_time_limits_are_visible(self):
        group, paths = self.duplicate_group(*([b"same"] * 21))
        result = self.audit.verify_duplicates(group["group_id"])
        self.assertEqual(len(result["members"]), 20)
        self.assertTrue(result["truncated"])
        self.assertEqual(result["status"], "partial")
        with self.store.connect() as db:
            db.execute("DELETE FROM library_audit_hashes")
        current = [0]
        def fast_clock():
            current[0] += 21
            return current[0]
        self.audit.clock = fast_clock
        limited = self.audit.verify_duplicates(group["group_id"])
        self.assertEqual(limited["bytes_read"], 0)
        self.assertEqual(limited["status"], "unavailable")
        self.assertTrue(all(row["status"] == "limited" for row in limited["members"]))

    def test_audio_descriptors_are_read_only(self):
        group, _ = self.duplicate_group(b"same", b"same")
        original = os.open
        observed = []
        def read_only(path, flags, *args, **kwargs):
            observed.append(flags & os.O_ACCMODE)
            return original(path, flags, *args, **kwargs)
        # Preserve the capability check while instrumenting the original call.
        with patch("os.open", side_effect=read_only) as opened, patch("os.supports_dir_fd", os.supports_dir_fd | {opened}):
            self.audit.verify_duplicates(group["group_id"])
        self.assertTrue(observed)
        self.assertEqual(set(observed), {os.O_RDONLY})

    def test_symlink_is_not_opened_for_hashing(self):
        group, paths = self.duplicate_group(b"same", b"same")
        outside = self.root / "outside.flac"; outside.write_bytes(b"private")
        paths[1].unlink(); paths[1].symlink_to(outside)
        result = self.audit.verify_duplicates(group["group_id"])
        self.assertTrue(result["partial"])
        self.assertEqual(result["members"][1]["status"], "unavailable")
        self.assertEqual(outside.read_bytes(), b"private")

    def test_changed_file_during_hash_is_discarded(self):
        group, paths = self.duplicate_group(b"same", b"same")
        original = self.audit._hash
        def changed(*args):
            digest = original(*args)
            Path(args[0]).write_bytes(b"evil")
            return digest
        with patch.object(self.audit, "_hash", side_effect=changed):
            result = self.audit.verify_duplicates(group["group_id"])
        self.assertEqual(result["status"], "unavailable")
        self.assertFalse(result["groups"])
        self.assertTrue(all("sha256" not in row for row in result["members"]))
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM library_audit_hashes").fetchone()[0], 0)

    def test_hash_rejects_changed_settings_and_leaves_annotations_unchanged(self):
        group, _ = self.duplicate_group(b"same", b"same")
        before = self.annotations()
        original = self.audit._hash
        def changed(*args):
            digest = original(*args)
            self.store.put_settings({"mode": "demo"})
            return digest
        with patch.object(self.audit, "_hash", side_effect=changed), self.assertRaises(AppError):
            self.audit.verify_duplicates(group["group_id"])
        self.assertEqual(self.annotations(), before)

    def test_relink_across_renamed_folders_preserves_every_annotation(self):
        self.xml(self.audio("new organisation/renamed.flac"))
        old = self.missing_link()
        plan = self.audit.preview_relink(self.ids[0])
        self.assertEqual(plan["old_path"], str(old))
        self.assertEqual(len(plan["candidates"]), 1)
        self.store.patch(self.ids[0], "real", {"notes": "new note after preview", "tags": "fresh tags"})
        before = self.annotations()
        result = self.audit.apply_relink(plan["token"], plan["candidates"][0]["entry_id"])
        after = self.annotations()
        self.assertEqual(after, {**before, "local_file": str(self.music / "new organisation/renamed.flac")})
        self.assertFalse(after["owned"])
        self.assertEqual(result["updated"], 1)
        with self.assertRaises(AppError):
            self.audit.apply_relink(plan["token"], plan["candidates"][0]["entry_id"])

    def test_relink_candidate_keeps_mix_duration_and_filename_evidence(self):
        self.xml(self.audio("extended.flac"), self.audio("radio.flac", title="Night Window (Radio Edit)"),
                 self.audio("long.flac", duration=500))
        self.missing_link()
        plan = self.audit.preview_relink(self.ids[0])
        self.assertEqual(len(plan["candidates"]), 2)
        self.assertNotIn(str(self.music / "radio.flac"), [row["path"] for row in plan["candidates"]])
        long = next(row for row in plan["candidates"] if row["path"].endswith("long.flac"))
        self.assertEqual(long["match_status"], "check_duration")

    def test_available_link_and_unsafe_original_cannot_be_replaced(self):
        self.xml(self.audio("available.flac"), self.audio("alternative.flac"))
        self.store.patch(self.ids[0], "real", {"local_file": str(self.music / "available.flac")})
        with self.assertRaises(AppError):
            self.audit.preview_relink(self.ids[0])
        link = self.music / "link.flac"; link.symlink_to(self.music / "available.flac")
        self.store.patch(self.ids[0], "real", {"local_file": str(link)})
        with self.assertRaises(AppError):
            self.audit.preview_relink(self.ids[0])

    def test_relink_rejects_parent_symlink_swap(self):
        self.xml(self.audio("sub/current.flac"))
        self.missing_link()
        plan = self.audit.preview_relink(self.ids[0])
        (self.music / "sub").rename(self.root / "Moved")
        (self.music / "sub").symlink_to(self.root / "Moved", target_is_directory=True)
        before = self.annotations()
        with self.assertRaises(AppError):
            self.audit.apply_relink(plan["token"], plan["candidates"][0]["entry_id"])
        self.assertEqual(self.annotations(), before)

    def test_database_failure_does_not_partially_update_link(self):
        self.xml(self.audio("current.flac"))
        self.missing_link()
        plan = self.audit.preview_relink(self.ids[0])
        before = self.annotations()
        with self.store.connect() as db:
            db.execute("CREATE TRIGGER reject_link BEFORE UPDATE ON tracks BEGIN SELECT RAISE(ABORT,'test failure'); END")
        with self.assertRaises(sqlite3.DatabaseError):
            self.audit.apply_relink(plan["token"], plan["candidates"][0]["entry_id"])
        self.assertEqual(self.annotations(), before)

    def test_stale_relink_failures_keep_original_annotations(self):
        for change in ("file", "track", "link", "root", "mode", "metadata", "old_returned", "expired"):
            with self.subTest(change=change):
                self.store.put_settings({"mode": "real", "music_root": str(self.music)})
                row = self.audio("current.flac", b"same")
                self.xml(row)
                old = self.missing_link()
                if old.exists():
                    old.unlink()
                plan = self.audit.preview_relink(self.ids[0])
                if change == "file":
                    replacement = self.music / "temp.flac"; replacement.write_bytes(b"else"); replacement.replace(self.music / "current.flac")
                elif change == "track":
                    with self.store.connect() as db:
                        db.execute("UPDATE tracks SET metadata=? WHERE id=?", (json.dumps({"title_and_version": "other"}), self.ids[0]))
                elif change == "link":
                    self.store.patch(self.ids[0], "real", {"local_file": str(self.music / "changed.flac")})
                elif change == "root":
                    self.store.put_settings({"music_root": str(self.downloads)})
                elif change == "mode":
                    self.store.put_settings({"mode": "demo"})
                elif change == "metadata":
                    self.xml({**row, "Artist": "New artist"})
                elif change == "old_returned":
                    old.parent.mkdir(exist_ok=True); old.write_bytes(b"returned")
                else:
                    self.clock[0] += PLAN_SECONDS
                before = self.annotations()
                with self.assertRaises(AppError):
                    self.audit.apply_relink(plan["token"], plan["candidates"][0]["entry_id"])
                self.assertEqual(self.annotations(), before)
                with self.store.connect() as db:
                    db.execute("UPDATE tracks SET metadata=? WHERE id=?", (json.dumps({"artists": "Example Artist", "title_and_version": "Night Window (Extended Mix)", "duration_ms": 193000}), self.ids[0]))

    def test_demo_invalid_tokens_and_reset_never_change_files(self):
        self.xml(self.audio("current.flac"))
        self.missing_link()
        plan = self.audit.preview_relink(self.ids[0])
        for invalid in ("\ud800", "non-ascii-é", None):
            with self.subTest(invalid=repr(invalid)), self.assertRaises(AppError):
                self.audit.apply_relink(invalid, plan["candidates"][0]["entry_id"])
        self.audit.clear()
        with self.assertRaises(AppError):
            self.audit.apply_relink(plan["token"], plan["candidates"][0]["entry_id"])
        self.store.put_settings({"mode": "demo"})
        for method in (self.audit.review, self.audit.export_report, lambda: self.audit.preview_relink(self.ids[0])):
            with self.assertRaises(AppError):
                method()


if __name__ == "__main__":
    unittest.main()
