"""Cue review reads saved XML and exports selected markers without library writes."""
from copy import deepcopy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import Store
from cue_planner import CuePlanner, CuePlannerError, PLAN_SECONDS, _read_xml
from library_inventory import LibraryInventory
from spotify_api import AppError


class CuePlannerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name).resolve()
        self.music = self.directory / "DJ Music"
        self.music.mkdir()
        self.audio = self.music / "Artist & Guest — Track.flac"
        self.audio.write_bytes(b"fictional audio; cue planning must not decode or modify this")
        self.xml = self.directory / "collection.xml"
        self.master = self.directory / "master.db"
        self.master.write_bytes(b"fictional database; cue planning must never open this")
        self.store = Store(self.directory / "data")
        self.store.put_settings({"music_root": str(self.music), "rekordbox_xml": str(self.xml),
                                 "rekordbox_db": str(self.master), "mode": "real"})
        self.library = LibraryInventory(self.store)
        self.clock = [100.0]
        self.planner = CuePlanner(self.store, self.library, lambda: self.clock[0])
        self.track = ET.Element("TRACK", {"TrackID": "17", "Name": 'Track & <Remix> "one"',
                                           "Artist": "Artist & Guest", "Album": "Préparation",
                                           "TotalTime": "300", "AverageBpm": "120", "Comments": "keep my notes",
                                           "Location": self.audio.as_uri(), "Rating": "255"})
        ET.SubElement(self.track, "TEMPO", {"Inizio": "0.5", "Bpm": "120", "Metro": "4/4", "Battito": "1"})
        self.refresh()

    def tearDown(self):
        self.temp.cleanup()

    def refresh(self, extra=None):
        document = ET.Element("DJ_PLAYLISTS", {"Version": "1.0.0"})
        items = [self.track, *(extra or [])]
        collection = ET.SubElement(document, "COLLECTION", {"Entries": str(len(items))})
        collection.extend(items)
        self.xml.write_bytes(ET.tostring(document, encoding="utf-8", xml_declaration=True))
        self.library.refresh_xml(str(self.xml), str(self.music))
        self.entry_id = next(row["id"] for row in self.library._entries(str(self.music)) if row["path"] == str(self.audio))

    def cue(self, time, slot=-1, name="Existing cue", **values):
        return ET.SubElement(self.track, "POSITION_MARK", {"Name": name, "Type": "0", "Start": str(time),
                                                            "Num": str(slot), **values})

    def plan(self, **options):
        return self.planner.plan(self.entry_id, options)

    def export(self, plan, selected=None):
        selected = [row["id"] for row in plan["suggestions"]] if selected is None else selected
        return self.planner.export(plan["token"], selected)

    def assert_code(self, code, operation):
        with self.assertRaises(CuePlannerError) as raised:
            operation()
        self.assertEqual(raised.exception.code, code)

    def database_dump(self):
        with self.store.connect() as db:
            return tuple(db.iterdump())

    def test_grid_positions_are_reviewable_and_nothing_is_preselected(self):
        plan = self.plan()
        self.assertEqual([row["time"] for row in plan["suggestions"]], [0.5, 64.5, 128.5, 192.5, 256.5])
        self.assertEqual([row["bar"] for row in plan["suggestions"]], [1, 33, 65, 97, 129])
        self.assertEqual([row["slot"] for row in plan["suggestions"]], list(range(5)))
        self.assertEqual(plan["grid_source"], "rekordbox_xml")
        self.assertEqual(plan["selected_count"], 0)
        self.assertIn("not detected", plan["warnings"][0])
        self.assert_code("invalid_selection", lambda: self.export(plan, []))

    def test_selected_export_preserves_all_xml_fields_cues_and_grid_without_writes(self):
        self.cue(4.5, 0, 'Keep <A> & "B"', Red="1", Green="2", Blue="3")
        self.cue(20.5, -1, "Memory loop", Type="4", End="24.5")
        self.refresh()
        before_xml, before_audio, before_master = self.xml.read_bytes(), self.audio.read_bytes(), self.master.read_bytes()
        before_db = self.database_dump()
        original_children = [ET.tostring(child) for child in self.track]
        plan = self.plan(max_cues=3)
        self.assertEqual([row["slot"] for row in plan["suggestions"]], [1, 2, 3])
        selected = plan["suggestions"][1]
        output = self.export(plan, [selected["id"]])
        self.assertEqual(output["selected_count"], 1)
        self.assertRegex(output["filename"], r"^DJList-cue-review-[0-9a-f]{8}-[0-9a-f]{8}\.xml$")
        document = ET.fromstring(output["xml"])
        exported = document.find("COLLECTION/TRACK")
        self.assertEqual(exported.attrib, self.track.attrib)
        self.assertEqual([ET.tostring(child) for child in list(exported)[:-1]], original_children)
        self.assertEqual(exported[-1].attrib, {"Name": selected["name"], "Type": "0",
                                              "Start": str(selected["time"]), "Num": str(selected["slot"])})
        playlist = document.find("PLAYLISTS/NODE/NODE")
        self.assertEqual(playlist.get("KeyType"), "1")
        self.assertEqual(playlist.find("TRACK").get("Key"), self.audio.as_uri())
        self.assertEqual(document.find("COLLECTION").get("Entries"), "1")
        self.assertEqual(self.xml.read_bytes(), before_xml)
        self.assertEqual(self.audio.read_bytes(), before_audio)
        self.assertEqual(self.master.read_bytes(), before_master)
        self.assertEqual(self.database_dump(), before_db)
        self.assertNotIn("<Remix>", output["xml"].decode())

    def test_variable_tempo_uses_each_anchor_and_preserves_them_in_export(self):
        self.track.find("TEMPO").set("Inizio", "0")
        ET.SubElement(self.track, "TEMPO", {"Inizio": "64", "Bpm": "60", "Metro": "4/4", "Battito": "1"})
        self.refresh()
        plan = self.plan()
        self.assertEqual([row["time"] for row in plan["suggestions"]], [0.0, 64.0, 192.0])
        self.assertEqual([row["bar"] for row in plan["suggestions"]], [1, 33, 65])
        result = ET.fromstring(self.export(plan)["xml"]).find("COLLECTION/TRACK")
        self.assertEqual([node.attrib for node in result.findall("TEMPO")], [node.attrib for node in self.track.findall("TEMPO")])

    def test_beat_number_at_mid_bar_anchor_keeps_downbeats_correct(self):
        first = self.track.find("TEMPO")
        first.set("Inizio", "0")
        first.set("Battito", "3")
        ET.SubElement(self.track, "TEMPO", {"Inizio": "16", "Bpm": "60", "Metro": "4/4", "Battito": "3"})
        self.refresh()
        plan = self.plan(interval_bars=8, max_cues=3)
        self.assertEqual([row["time"] for row in plan["suggestions"]], [1.0, 18.0, 50.0])

    def test_existing_cues_and_loop_ends_have_four_bar_clearance(self):
        self.cue(0.5, 0)
        self.cue(60.5, -1, "Loop", Type="4", End="68.5")
        self.refresh()
        plan = self.plan()
        self.assertEqual(plan["suggestions"][0]["time"], 128.5)
        self.assertEqual(plan["suggestions"][0]["slot"], 1)
        self.assertTrue(any("2 proposed positions were skipped" in text for text in plan["warnings"]))

    def test_all_eight_hotcues_are_preserved_and_no_new_slots_are_invented(self):
        for slot in range(8):
            self.cue(slot * 2, slot)
        self.refresh()
        plan = self.plan()
        self.assertEqual(len(plan["existing_cues"]), 8)
        self.assertEqual(plan["suggestions"], [])
        self.assertIn("All eight", " ".join(plan["warnings"]))
        self.assert_code("invalid_selection", lambda: self.export(plan, ["cue-8"]))

    def test_manual_grid_requires_confirmation_and_is_exported_only_after_review(self):
        self.track.remove(self.track.find("TEMPO"))
        self.cue(8.5, 3)
        self.refresh()
        self.assert_code("manual_grid_required", self.plan)
        for confirmation in (False, "true", 1):
            with self.subTest(confirmation=confirmation):
                self.assert_code("manual_grid_required", lambda: self.plan(bpm=120, first_downbeat_seconds=0.5,
                                                                           confirm_manual_grid=confirmation))
        plan = self.plan(bpm=120, first_downbeat_seconds=0.5, confirm_manual_grid=True)
        self.assertEqual(plan["grid_source"], "manual")
        self.assertIn("No audio analysis", " ".join(plan["warnings"]))
        exported = ET.fromstring(self.export(plan, [plan["suggestions"][0]["id"]])["xml"]).find("COLLECTION/TRACK")
        self.assertEqual(exported.find("TEMPO").attrib, {"Inizio": "0.5", "Bpm": "120", "Metro": "4/4", "Battito": "1"})
        self.assertEqual(exported.findall("POSITION_MARK")[0].attrib, self.track.find("POSITION_MARK").attrib)
        self.assertEqual(self.track.findall("TEMPO"), [])

    def test_manual_values_cannot_replace_an_existing_xml_grid(self):
        self.assert_code("existing_grid", lambda: self.plan(bpm=128, first_downbeat_seconds=0, confirm_manual_grid=True))

    def test_invalid_manual_bpm_timestamps_and_options_are_rejected(self):
        self.track.remove(self.track.find("TEMPO"))
        self.refresh()
        valid = {"bpm": 120, "first_downbeat_seconds": 0, "confirm_manual_grid": True}
        for bpm in (-1, 0, 19, 401, "NaN", "Infinity", True, None):
            with self.subTest(bpm=bpm):
                self.assert_code("invalid_grid", lambda: self.plan(**{**valid, "bpm": bpm}))
        for timestamp in (-0.1, 300, 301, "NaN", "Infinity", True, None):
            with self.subTest(timestamp=timestamp):
                self.assert_code("invalid_grid", lambda: self.plan(**{**valid, "first_downbeat_seconds": timestamp}))
        for options in ({"interval_bars": 12}, {"interval_bars": "32"}, {"interval_bars": True},
                        {"max_cues": 9}, {"max_cues": 0}, {"new_option": 1}):
            with self.subTest(options=options):
                self.assert_code("invalid_options", lambda: self.planner.plan(self.entry_id, options))

    def test_invalid_existing_grid_is_rejected_instead_of_replaced(self):
        original = deepcopy(self.track)
        for attribute, value in (("Inizio", "-1"), ("Inizio", "301"), ("Bpm", "0"),
                                 ("Bpm", "NaN"), ("Bpm", "1000"), ("Bpm", "1e-320"),
                                 ("Battito", "0"), ("Battito", "5")):
            with self.subTest(attribute=attribute, value=value):
                self.track = deepcopy(original)
                self.track.find("TEMPO").set(attribute, value)
                self.refresh()
                self.assert_code("invalid_grid", self.plan)
        self.track = deepcopy(original)
        self.track.find("TEMPO").set("Metro", "3/4")
        self.refresh()
        self.assert_code("unsupported_grid", self.plan)
        self.track = deepcopy(original)
        self.track.append(deepcopy(self.track.find("TEMPO")))
        self.refresh()
        self.assert_code("invalid_grid", self.plan)

    def test_marker_rounding_cannot_put_a_suggestion_at_the_track_end(self):
        self.track.find("TEMPO").set("Inizio", "299.9999999")
        self.refresh()
        plan = self.plan()
        self.assertEqual(plan["suggestions"], [])
        self.assert_code("invalid_selection", lambda: self.export(plan, ["cue-0"]))

    def test_invalid_existing_cues_cannot_be_dropped_by_export(self):
        original = deepcopy(self.track)
        for values, code in (({"Start": "-1"}, "invalid_grid"), ({"Start": "302"}, "invalid_grid"),
                             ({"Start": "NaN"}, "invalid_grid"), ({"Type": "4"}, "invalid_cues"),
                             ({"Type": "4", "End": "4"}, "invalid_cues"), ({"Num": "x"}, "invalid_grid")):
            with self.subTest(values=values):
                self.track = deepcopy(original)
                self.cue(4, **values)
                self.refresh()
                self.assert_code(code, self.plan)
        self.track = deepcopy(original)
        self.cue(4, 0)
        self.cue(20, 0)
        self.refresh()
        self.assert_code("invalid_cues", self.plan)

    def test_tokens_expire_are_single_use_and_clear_invalidates_them(self):
        plan = self.plan()
        self.clock[0] += PLAN_SECONDS
        self.assert_code("stale_plan", lambda: self.export(plan))
        plan = self.plan()
        self.export(plan)
        self.assert_code("stale_plan", lambda: self.export(plan))
        plan = self.plan()
        self.planner.clear()
        self.assert_code("stale_plan", lambda: self.export(plan))

    def test_unknown_duplicate_or_non_ascii_selections_do_not_export(self):
        for selection in (["unknown"], ["cue-0", "cue-0"], [1], "cue-0", ["\ud800"]):
            with self.subTest(selection=repr(selection)):
                plan = self.plan()
                self.assert_code("invalid_selection", lambda: self.export(plan, selection))
        for token in (None, "é" * 43, "\ud800" * 43, "short"):
            with self.subTest(token=repr(token)):
                self.assert_code("invalid_selection", lambda: self.planner.export(token, ["cue-0"]))

    def test_returned_plan_cannot_mutate_the_exported_review(self):
        plan = self.plan()
        expected = deepcopy(plan["suggestions"][0])
        plan["suggestions"][0].update(time=-5, slot=900, name="unreviewed")
        plan["grid"][0]["bpm"] = 999
        exported = ET.fromstring(self.export(plan, [expected["id"]])["xml"]).find("COLLECTION/TRACK")
        cue = exported.find("POSITION_MARK")
        self.assertEqual(float(cue.get("Start")), expected["time"])
        self.assertEqual(int(cue.get("Num")), expected["slot"])
        self.assertEqual(cue.get("Name"), expected["name"])
        self.assertEqual(exported.find("TEMPO").get("Bpm"), "120")

    def test_changed_xml_requires_refresh_and_a_fresh_plan(self):
        plan = self.plan()
        self.track.set("Comments", "changed later")
        self.xml.write_bytes(self.xml.read_bytes().replace(b"keep my notes", b"changed notes"))
        self.assert_code("xml_refresh_required", lambda: self.export(plan))
        self.refresh()
        plan = self.plan()
        self.track.set("Comments", "changed again")
        self.refresh()
        self.assert_code("stale_plan", lambda: self.export(plan))

    def test_xml_replacement_with_same_bytes_size_and_mtime_is_stale(self):
        plan = self.plan()
        data, info = self.xml.read_bytes(), self.xml.stat()
        self.xml.rename(self.xml.with_suffix(".old"))
        self.xml.write_bytes(data)
        os.utime(self.xml, ns=(info.st_atime_ns, info.st_mtime_ns))
        self.assert_code("xml_refresh_required", lambda: self.export(plan))

    def test_source_change_during_calculation_or_export_is_rejected(self):
        import cue_planner
        calculate = cue_planner._bar_positions
        def changed_during_calculation(*args):
            result = calculate(*args)
            self.xml.write_bytes(self.xml.read_bytes().replace(b"keep my notes", b"changed notes"))
            return result
        with patch("cue_planner._bar_positions", side_effect=changed_during_calculation):
            self.assert_code("stale_plan", self.plan)
        self.refresh()
        plan = self.plan()
        serialize = ET.tostring
        def changed_during_export(element, *args, **kwargs):
            result = serialize(element, *args, **kwargs)
            if element.tag == "DJ_PLAYLISTS":
                self.xml.write_bytes(self.xml.read_bytes().replace(b"keep my notes", b"changed notes"))
            return result
        with patch("cue_planner.ET.tostring", side_effect=changed_during_export):
            self.assert_code("stale_plan", lambda: self.export(plan))

    def test_audio_replacement_and_symlink_swap_invalidate_review(self):
        for replacement in ("new_file", "symlink"):
            with self.subTest(replacement=replacement):
                self.refresh()
                plan = self.plan()
                info, payload = self.audio.stat(), self.audio.read_bytes()
                moved = self.directory / "temporary.flac"
                self.audio.rename(moved)
                if replacement == "new_file":
                    self.audio.write_bytes(payload)
                    os.utime(self.audio, ns=(info.st_atime_ns, info.st_mtime_ns))
                    expected = "xml_refresh_required"
                else:
                    self.audio.symlink_to(moved)
                    expected = "file_unavailable"
                self.assert_code(expected, lambda: self.export(plan))
                self.audio.unlink()
                moved.rename(self.audio)

    def test_audio_replacement_before_planning_requires_fresh_xml_review(self):
        original_xml, original_master = self.xml.read_bytes(), self.master.read_bytes()
        self.audio.write_bytes(b"a replacement recording after the last XML refresh")
        replaced = self.audio.read_bytes()
        before_db = self.database_dump()
        self.assert_code("xml_refresh_required", self.plan)
        self.assertEqual(self.audio.read_bytes(), replaced)
        self.assertEqual(self.xml.read_bytes(), original_xml)
        self.assertEqual(self.master.read_bytes(), original_master)
        self.assertEqual(self.database_dump(), before_db)
        self.refresh()
        plan = self.plan()
        self.assertTrue(plan["suggestions"])
        self.assertIn("cannot prove that the saved beatgrid matches the recording", " ".join(plan["warnings"]))

    def test_older_xml_entry_without_audio_fingerprint_requires_refresh(self):
        with self.store.connect() as db:
            row = db.execute("SELECT metadata FROM library_entries WHERE path=?", (str(self.audio),)).fetchone()
            entry = json.loads(row[0])
            entry.pop("xml_audio_fingerprint")
            db.execute("UPDATE library_entries SET metadata=? WHERE path=?", (json.dumps(entry), str(self.audio)))
        self.library.invalidate()
        self.assert_code("xml_refresh_required", self.plan)
        self.refresh()
        self.assertTrue(self.plan()["suggestions"])

    def test_audio_unavailable_at_xml_import_requires_refresh_after_reconnect(self):
        moved = self.directory / "Disconnected music"
        self.music.rename(moved)
        self.refresh()
        moved.rename(self.music)
        self.assert_code("xml_refresh_required", self.plan)
        self.refresh()
        self.assertTrue(self.plan()["suggestions"])

    def test_music_root_changes_demo_and_disconnected_drive_prevent_exports(self):
        plan = self.plan()
        self.store.put_settings({"mode": "demo"})
        self.assert_code("demo_mode", lambda: self.export(plan))
        self.assert_code("demo_mode", self.plan)
        self.store.put_settings({"mode": "real"})
        plan = self.plan()
        self.store.put_settings({"music_root": str(self.directory)})
        self.assert_code("stale_plan", lambda: self.export(plan))
        self.store.put_settings({"music_root": str(self.music)})
        plan = self.plan()
        self.music.rename(self.directory / "Offline")
        with self.assertRaises(AppError):
            self.export(plan)

    def test_uploaded_only_xml_has_no_revalidatable_source(self):
        self.library.import_xml(self.xml.read_text(), str(self.music))
        self.assert_code("xml_required", self.plan)

    def test_ambiguous_duplicate_xml_locations_require_review(self):
        duplicate = deepcopy(self.track)
        duplicate.set("TrackID", "18")
        self.refresh([duplicate])
        self.assert_code("ambiguous_xml_track", self.plan)

    def test_source_missing_or_symlink_is_rejected(self):
        self.xml.rename(self.xml.with_suffix(".saved"))
        self.assert_code("xml_required", self.plan)
        self.xml.symlink_to(self.xml.with_suffix(".saved"))
        self.assert_code("xml_required", self.plan)

    def test_unsafe_declarations_malformed_xml_and_invalid_counts_are_rejected(self):
        fixtures = (b'<!DOCTYPE DJ_PLAYLISTS [<!ENTITY secret SYSTEM "file:///etc/passwd">]><DJ_PLAYLISTS><COLLECTION Entries="0"/></DJ_PLAYLISTS>',
                    b'<!ENTITY x "text"><DJ_PLAYLISTS/>', b'<DJ_PLAYLISTS>', b'<OTHER><COLLECTION/></OTHER>',
                    b'<DJ_PLAYLISTS><COLLECTION Entries="2"/></DJ_PLAYLISTS>',
                    b'<DJ_PLAYLISTS><COLLECTION Entries="' + b'9' * 5000 + b'"/></DJ_PLAYLISTS>')
        for payload in fixtures:
            with self.subTest(payload=payload[:80]):
                self.xml.write_bytes(payload)
                self.assert_code("invalid_xml", lambda: _read_xml(str(self.xml)))

    def test_outside_root_audio_and_intermediate_symlinks_are_never_read(self):
        outside = self.directory / "outside.flac"
        outside.write_bytes(b"not selected")
        self.track.set("Location", outside.as_uri())
        document = ET.Element("DJ_PLAYLISTS")
        ET.SubElement(document, "COLLECTION", {"Entries": "1"}).append(self.track)
        self.xml.write_bytes(ET.tostring(document))
        self.library.refresh_xml(str(self.xml), str(self.music))
        other_id = next(row["id"] for row in self.library._entries(str(self.music)) if row["path"] == str(outside))
        self.assert_code("file_unavailable", lambda: self.planner.plan(other_id))
        link = self.music / "alias"
        link.symlink_to(self.directory, target_is_directory=True)
        self.track.set("Location", (link / "outside.flac").as_uri())
        self.xml.write_bytes(ET.tostring(document))
        self.library.refresh_xml(str(self.xml), str(self.music))
        other_id = next(row["id"] for row in self.library._entries(str(self.music)) if "alias" in row["path"])
        self.assert_code("file_unavailable", lambda: self.planner.plan(other_id))

    def test_planning_and_export_never_open_audio_or_master_database_content(self):
        original = os.open
        def guarded(path, flags, *args, **kwargs):
            candidate = Path(path)
            if candidate.name == self.master.name or candidate.suffix.lower() == ".flac":
                raise AssertionError("Cue planning opened audio or a Rekordbox database")
            self.assertEqual(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC), 0)
            return original(path, flags, *args, **kwargs)
        with patch("cue_planner.os.open", side_effect=guarded) as opened, \
                patch("cue_planner.os.supports_dir_fd", os.supports_dir_fd | {opened}):
            self.export(self.plan())


if __name__ == "__main__":
    unittest.main()
