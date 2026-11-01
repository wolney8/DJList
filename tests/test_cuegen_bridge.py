"""CueGen adapter tests use synthetic files and an injected process runner."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import subprocess
import time
from unittest.mock import patch
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import Store
from cue_planner import CuePlanner, CuePlannerError
from cuegen_bridge import CueGenBridge, validate_options, _arguments, parse_proposals, sandbox_profile, run_bounded
from library_inventory import LibraryInventory
from rekordbox_backups import RekordboxBackups


class FixtureRuntime:
    """The bridge guard tests never download or execute a third-party binary."""
    def __init__(self, store):
        self.store = store

    def prepare(self, progress=None):
        return self.verified_path()

    def verified_path(self, force=False):
        return Path(self.store.settings()["cuegen_path"])

    def status(self):
        return {"installed": self.verified_path().exists(), "message": "Synthetic installed engine"}


class CueGenBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.music = self.root / "Music"
        self.music.mkdir()
        self.audio = self.music / "Artist - Tune.wav"
        self.audio.write_bytes(b"fictional audio")
        self.db_dir = self.root / "Rekordbox"
        self.db_dir.mkdir()
        self.master = self.db_dir / "master.db"
        self.master.write_bytes(b"fictional encrypted database bytes")
        self.analysis = self.db_dir / "share"
        self.analysis.mkdir()
        (self.analysis / "sample.DAT").write_bytes(b"fictional analysis")
        self.executable = self.root / "CueGen.Console"
        self.executable.write_bytes(b"\xcf\xfa\xed\xfe" + b"fictional executable")
        self.executable.chmod(0o700)
        self.sandbox = self.root / "sandbox-exec"
        self.sandbox.write_bytes(b"test-only executable; never run")
        self.sandbox.chmod(0o700)
        self.xml = self.root / "collection.xml"
        self.store = Store(self.root / "DJList")
        self.store.put_settings({"mode": "real", "music_root": str(self.music), "rekordbox_xml": str(self.xml),
                                 "rekordbox_db": str(self.master), "cuegen_path": str(self.executable),
                                 "rekordbox_analysis_root": str(self.analysis)})
        self.library = LibraryInventory(self.store)
        self.planner = CuePlanner(self.store, self.library)
        self.closed = lambda: {"known": True, "running": []}
        self.backups = RekordboxBackups(self.store, threading.RLock(), process_checker=self.closed, platform="darwin")
        self.track = ET.Element("TRACK", {"TrackID": "17", "Name": "Tune", "Artist": "Artist", "TotalTime": "300", "Location": self.audio.as_uri()})
        ET.SubElement(self.track, "TEMPO", {"Inizio": "0", "Bpm": "120", "Metro": "4/4", "Battito": "1"})
        self.refresh()
        self.calls = []
        self.probe_calls = []
        self.probe_result = {"returncode": 0, "output": "Usage: CueGen.Console [OPTION]...\nVersion 1.0.37.0\n"}
        self.rows = [{"ContentID": "17", "InMsec": 64000, "OutMsec": -1, "Kind": 1, "Comment": "Verse", "ColorTableIndex": 1}]
        self.version = "1.0.37.0"
        self.exit_code = 1
        self.extra = ""
        self.bridge = CueGenBridge(self.store, self.backups, self.planner, runner=self.runner,
                                   process_checker=self.closed, platform="darwin", sandbox_path=str(self.sandbox),
                                   runtime=FixtureRuntime(self.store))

    def tearDown(self):
        self.backups.close()
        self.temp.cleanup()

    def refresh(self):
        document = ET.Element("DJ_PLAYLISTS")
        collection = ET.SubElement(document, "COLLECTION", {"Entries": "1"})
        collection.append(self.track)
        self.xml.write_bytes(ET.tostring(document))
        self.library.refresh_xml(str(self.xml), str(self.music))
        self.entry_id = next(row["id"] for row in self.library._entries(str(self.music)) if row["path"] == str(self.audio))

    def output(self, source="phrases"):
        header = f"Info: Getting cue points from phrase analysis for 17 with file at {self.audio}" if source == "phrases" else f"Info: Reading cue points for 17 from tag of {self.audio}"
        return "\n".join(["Info: Starting CueGen version " + self.version, header,
                          *["Info: Created cue point " + json.dumps(row) for row in self.rows], self.extra,
                          "Info: Finished cue points creation without errors"])

    def runner(self, args, **kwargs):
        if args[-1] == "--help":
            self.probe_calls.append((args, kwargs))
            self.assertFalse(any(arg.startswith("--db=") for arg in args))
            self.assertFalse((kwargs["cwd"] / "share").exists())
            return self.probe_result
        self.calls.append((args, kwargs))
        path = Path(next(arg.split("=", 1)[1] for arg in args if arg.startswith("--db=")))
        self.assertNotEqual(path, self.master)
        self.assertEqual(path.read_bytes(), self.master.read_bytes())
        self.assertEqual(path.parent, kwargs["cwd"])
        self.assertEqual((path.parent / "share").resolve(), self.analysis)
        self.assertIn("--dryrun", args)
        self.assertIn("--merge", args)
        self.assertIn("--mytag-", args)
        self.assertIn("--energy-", args)
        self.assertEqual(kwargs["env"]["HOME"], str(path.parent / "home"))
        # A writer can only receive this disposable copy, not the source/backup.
        path.write_bytes(b"a subprocess changed its disposable working database")
        return {"returncode": self.exit_code, "output": self.output("phrases" if "--phrase" in args else "mixed_in_key")}

    def plan(self, **options):
        return self.bridge.plan(self.entry_id, {"engine": "cuegen", **options})

    def assert_code(self, code, callback):
        with self.assertRaises(CuePlannerError) as result:
            callback()
        self.assertEqual(result.exception.code, code)

    def test_plan_uses_checked_copy_sandbox_and_selected_xml_export_only(self):
        originals = {path: path.read_bytes() for path in (self.master, self.audio, self.xml, self.analysis / "sample.DAT")}
        plan = self.plan()
        self.assertEqual(plan["engine"], "cuegen")
        self.assertEqual(plan["selected_count"], 0)
        self.assertEqual(plan["suggestions"][0]["time"], 64)
        self.assertEqual(plan["suggestions"][0]["rgb"], [48, 90, 255])
        args, kwargs = self.calls[0]
        self.assertEqual(args[:2], [str(self.sandbox), "-p"])
        self.assertIn("(deny network*)", args[2])
        self.assertIn("(deny file-read-data (literal " + json.dumps(str(self.master)) + "))", args[2])
        self.assertIn("--glob=" + str(self.audio), args)
        self.assertFalse(kwargs["cwd"].exists())
        output = self.planner.export(plan["token"], [plan["suggestions"][0]["id"]])
        cue = ET.fromstring(output["xml"]).find("COLLECTION/TRACK/POSITION_MARK")
        self.assertEqual(cue.get("Start"), "64")
        self.assertEqual(cue.get("Red"), "48")
        for path, original in originals.items():
            self.assertEqual(path.read_bytes(), original)
        snapshot = self.backups.status()["backups"][0]
        self.assertEqual((Path(snapshot["path"]) / "master.db").read_bytes(), originals[self.master])

    def test_internal_setup_checks_version_without_database_or_audio_access(self):
        self.master.unlink()
        self.audio.unlink()
        result = self.bridge.prepare_runtime()
        self.assertEqual(result["runtime_check"], "passed")
        self.assertEqual(result["version"], "1.0.37")
        self.assertEqual(self.calls, [])
        self.assertEqual(len(self.probe_calls), 1)
        profile = self.probe_calls[0][0][2]
        self.assertNotIn(str(self.master), profile)
        self.assertNotIn(str(self.audio), profile)
        self.assertIn("(allow file-map-executable", profile)
        self.assertIn("(deny network*)", profile)
        self.assertEqual(self.backups.status()["backups"], [])

    def test_internal_setup_fails_closed_and_status_reports_failed_probe(self):
        self.probe_result = {"returncode": 1, "output": "runtime unavailable"}
        self.assert_code("cuegen_runtime", self.bridge.prepare_runtime)
        self.assertEqual(self.bridge.status()["runtime_check"], "failed")
        self.assertEqual(self.calls, [])
        self.assertEqual(self.backups.status()["backups"], [])

    def test_runtime_success_is_not_reported_after_executable_changes(self):
        self.bridge.prepare_runtime()
        self.executable.write_bytes(b"\xcf\xfa\xed\xfe changed test executable")
        self.assertEqual(self.bridge.status()["runtime_check"], "not_checked")
        self.bridge.prepare_runtime()
        self.assertEqual(len(self.probe_calls), 2)

    def test_runtime_probe_is_reused_only_for_same_installed_file(self):
        self.bridge.prepare_runtime()
        self.plan()
        self.assertEqual(len(self.probe_calls), 1)

    def test_phrase_order_and_names_trim_human_entered_spaces(self):
        options = validate_options({"phrase_order": "Intro, Outro, Verse, Chorus, Bridge, Up / Down",
                                    "phrase_names": " Intro, Verse, Bridge, Chorus, Outro, Up, Down "})
        args = _arguments(self.executable, self.master, str(self.audio), options)
        self.assertIn("--order=Intro,Outro,Verse,Chorus,Bridge,Up/Down", args)
        self.assertIn("--names=Intro,Verse,Bridge,Chorus,Outro,Up,Down", args)

    def test_memory_loops_preserve_existing_cues_and_use_num_minus_one(self):
        ET.SubElement(self.track, "POSITION_MARK", {"Name": "Keep hot A", "Type": "0", "Num": "0", "Start": "4"})
        self.refresh()
        self.rows = [{"ContentID": "17", "InMsec": 64000, "OutMsec": 72000, "Kind": 4, "Comment": "Intro loop", "Color": 2}]
        plan = self.plan(cue_type="memory", loop_intro=16)
        self.assertEqual(plan["suggestions"][0]["end"], 72)
        exported = ET.fromstring(self.planner.export(plan["token"], [plan["suggestions"][0]["id"]])["xml"])
        marks = exported.findall("COLLECTION/TRACK/POSITION_MARK")
        self.assertEqual(marks[0].attrib, self.track.find("POSITION_MARK").attrib)
        self.assertEqual(marks[1].get("Num"), "-1")
        self.assertEqual(marks[1].get("Type"), "4")
        self.assertEqual(marks[1].get("End"), "72")

    def test_existing_slot_or_nearby_marker_is_skipped_without_overwrite(self):
        ET.SubElement(self.track, "POSITION_MARK", {"Name": "Keep", "Type": "0", "Num": "0", "Start": "0"})
        self.refresh()
        plan = self.plan()
        self.assertEqual(plan["suggestions"], [])
        self.assertTrue(any("skipped" in warning for warning in plan["warnings"]))

    def test_demo_or_unsupported_platform_never_runs_or_creates_backup(self):
        self.store.put_settings({"mode": "demo"})
        self.assert_code("demo_mode", self.plan)
        self.store.put_settings({"mode": "real"})
        self.bridge.platform = "linux"
        self.assert_code("cuegen_unavailable", self.plan)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.backups.status()["backups"], [])

    def test_unknown_or_running_process_state_is_blocked(self):
        for value in ({"known": False, "running": []}, {"known": True, "running": ["Rekordbox"]},
                      {"known": True}, {"known": True, "running": None}, {"known": True, "running": ""}):
            self.bridge.process_checker = lambda: value
            self.assert_code("cuegen_library_busy", self.plan)
        self.assertEqual(self.calls, [])

    def test_source_changes_after_plan_block_export(self):
        plan = self.plan()
        self.master.write_bytes(b"database changed elsewhere")
        self.assert_code("stale_plan", lambda: self.planner.export(plan["token"], [plan["suggestions"][0]["id"]]))

    def test_settings_changes_after_plan_block_export(self):
        plan = self.plan()
        self.store.put_settings({"cuegen_path": str(self.root / "different")})
        self.assert_code("stale_plan", lambda: self.planner.export(plan["token"], [plan["suggestions"][0]["id"]]))

    def test_no_unsandboxed_fallback_if_sandbox_missing(self):
        self.sandbox.unlink()
        self.assert_code("cuegen_unavailable", self.plan)
        self.assertEqual(self.calls, [])

    def test_executable_symlink_hardlink_or_script_is_rejected(self):
        original = self.executable.read_bytes()
        self.executable.write_bytes(b"#!/bin/sh\nexit 0\n")
        self.assert_code("cuegen_unavailable", self.plan)
        self.executable.write_bytes(original)
        link = self.root / "linked"
        os.link(self.executable, link)
        self.assert_code("cuegen_unavailable", self.plan)
        link.unlink()
        link.symlink_to(self.executable)
        self.store.put_settings({"cuegen_path": str(link)})
        self.assert_code("cuegen_unavailable", self.plan)
        self.assertEqual(self.calls, [])

    def test_glob_metacharacters_never_allow_a_broad_batch(self):
        replacement = self.music / "Artist [live] - Tune.wav"
        self.audio.rename(replacement)
        self.audio = replacement
        self.track.set("Location", replacement.as_uri())
        self.refresh()
        self.assert_code("cuegen_track_mismatch", self.plan)
        self.assertEqual(self.calls, [])

    def test_wrong_track_or_version_and_engine_errors_are_rejected(self):
        self.version = "1.0.38.0"
        self.assert_code("cuegen_version", self.plan)
        self.version = "1.0.37.0"
        self.rows[0]["ContentID"] = "18"
        self.assert_code("cuegen_output_invalid", self.plan)
        self.rows[0]["ContentID"] = "17"
        self.extra = "Error: failed analysis"
        self.assert_code("cuegen_failed", self.plan)
        self.extra = "Info: BPM is unknown, assuming 120 BPM"
        self.assert_code("cuegen_analysis_required", self.plan)

    def test_all_requested_options_are_separate_fixed_arguments(self):
        options = validate_options({"engine": "cuegen", "cue_type": "memory", "max_cues": 10, "source": "mixed_in_key", "offset_beats": -4, "snap": False, "loop_outro": 32, "color_mode": "sequence", "comment": "--energy", "colors": "1, 4,16"})
        args = _arguments(self.executable, self.root / "copy.db", str(self.audio), options)
        self.assertIn("--comment=--energy", args)
        self.assertNotIn("--energy", args)
        self.assertIn("--snap-", args)
        self.assertIn("--offset=-4", args)
        self.assertIn("--colors=1,4,16", args)
        for values in ({"overwrite": True}, {"offset_beats": .25}, {"loop_intro": 3}, {"colors": "17"}, {"max_cues": 9}, {"phrase_order": "Intro,Intro"}, {"comment": "bad\ntext"}):
            self.assert_code("invalid_options", lambda: validate_options(values))

    def test_modified_snapshot_is_rejected_before_process(self):
        old_copy = self.bridge._copy_snapshot
        def changed(record, work):
            (Path(record["path"]) / "master.db").write_bytes(b"tampered backup")
            return old_copy(record, work)
        self.bridge._copy_snapshot = changed
        self.assert_code("cuegen_unavailable", self.plan)
        self.assertEqual(self.calls, [])

    def test_unsafe_analysis_root_containing_database_is_rejected(self):
        self.store.put_settings({"rekordbox_analysis_root": str(self.db_dir)})
        self.assert_code("cuegen_unavailable", self.plan)
        self.assertEqual(self.calls, [])

    def test_missing_success_marker_and_multiple_track_headers_are_rejected(self):
        source = self.planner._source(self.entry_id)
        options = validate_options({})
        output = self.output().replace("Finished cue points creation without errors", "Finished cue points creation with errors")
        self.assert_code("cuegen_failed", lambda: parse_proposals({"returncode": 1, "output": output}, source, options))
        self.extra = f"Info: Getting cue points from phrase analysis for 18 with file at {self.audio}"
        self.assert_code("cuegen_track_mismatch", lambda: parse_proposals({"returncode": 1, "output": self.output()}, source, options))

    def test_bounded_runner_rejects_excess_output_without_external_executable(self):
        self.assert_code("cuegen_output_limit", lambda: run_bounded([sys.executable, "-c", "print('x' * 10000)"], cwd=self.root, env={"PATH": os.environ.get("PATH", "")}, timeout=3, output_limit=100))

    def test_ten_external_memory_cues_can_be_reviewed_and_exported(self):
        source = self.planner._source(self.entry_id)
        proposals = [{"name": f"Memory {i}", "slot": -1, "time": i * 24.0} for i in range(10)]
        plan = self.planner.accept_external(source, proposals, {"cue_type": "memory", "max_cues": 10, "min_distance_bars": 4})
        self.assertEqual(len(plan["suggestions"]), 10)
        exported = self.planner.export(plan["token"], [row["id"] for row in plan["suggestions"]])
        self.assertEqual(len(ET.fromstring(exported["xml"]).findall("COLLECTION/TRACK/POSITION_MARK")), 10)

    def test_external_invalid_positions_colours_and_duplicate_rows_are_rejected(self):
        source = self.planner._source(self.entry_id)
        for proposals in ([{"name": "Bad", "slot": 0, "time": 300}], [{"name": "Bad", "slot": 0, "time": 30, "rgb": [999, 0, 0]}], [{"name": "A", "slot": 0, "time": 30}]*2):
            with self.assertRaises(CuePlannerError):
                self.planner.accept_external(source, proposals, {"cue_type": "hot", "max_cues": 8})

    def test_only_read_only_descriptors_open_the_live_database(self):
        original_open = os.open
        parent_identity = (self.db_dir.stat().st_dev, self.db_dir.stat().st_ino)
        source_flags = []
        def guarded(path, flags, *args, **kwargs):
            parent = kwargs.get("dir_fd")
            if str(path) == str(self.master) or (str(path) == "master.db" and parent is not None
                    and (os.fstat(parent).st_dev, os.fstat(parent).st_ino) == parent_identity):
                source_flags.append(flags)
                self.assertEqual(flags & os.O_ACCMODE, os.O_RDONLY)
                self.assertFalse(flags & (os.O_CREAT | os.O_TRUNC))
            return original_open(path, flags, *args, **kwargs)
        with patch("os.open", guarded), patch("os.supports_dir_fd", os.supports_dir_fd | {guarded}):
            self.plan()
        self.assertTrue(source_flags)

    def test_written_working_copy_is_rehashed_before_execution(self):
        original_copy, original_write = self.bridge._copy_snapshot, os.write
        def corrupted(record, work):
            def wrong_write(descriptor, data):
                return original_write(descriptor, b"z" * len(data))
            with patch("cuegen_bridge.os.write", wrong_write):
                return original_copy(record, work)
        self.bridge._copy_snapshot = corrupted
        self.assert_code("cuegen_unavailable", self.plan)
        self.assertEqual(self.calls, [])

    def test_timeout_kills_child_when_leader_already_exited(self):
        pid_file = self.root / "child-pid"
        child_script = "import os,time;from pathlib import Path;Path(" + repr(str(pid_file)) + ").write_text(str(os.getpid()));time.sleep(30)"
        leader_script = "import subprocess,sys;subprocess.Popen([sys.executable,'-c'," + repr(child_script) + "]);print('leader exit',flush=True)"
        self.assert_code("cuegen_timeout", lambda: run_bounded([sys.executable, "-c", leader_script], cwd=self.root,
                         env={"PATH": os.environ.get("PATH", "")}, timeout=.5, output_limit=1000))
        self.assertTrue(pid_file.exists())
        pid = int(pid_file.read_text())
        try:
            state = ""
            for _ in range(10):
                state = subprocess.run(["/bin/ps", "-p", str(pid), "-o", "stat="], capture_output=True, text=True).stdout.strip()
                if not state or state.startswith("Z"):
                    break
                time.sleep(.02)
            self.assertTrue(not state or state.startswith("Z"), "The child must not remain running after the leader exits.")
        finally:
            try:
                os.kill(pid, 9)
            except ProcessLookupError:
                pass
