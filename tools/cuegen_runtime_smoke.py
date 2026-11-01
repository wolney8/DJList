#!/usr/bin/env python3
"""Run the real pinned CueGen on synthetic data inside DJList's macOS sandbox.

This developer/CI check never accepts a user's database or music folder. It
creates its own encrypted database, silent audio, phrase/grid files and XML in
one temporary directory. Homebrew SQLCipher is a CI fixture-building dependency,
not a DJList runtime requirement. CueGen itself always runs through the same
mandatory sandbox and working-copy bridge used by the application.

The known-library process check alone is injected: the CI script's own filename
contains "cuegen", which the normal conservative command-line scan recognises.
The executable, native SQLCipher load, phrase parsing, cue generation, backup,
sandbox, output parser and XML export are real, not mocked.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import stat
import struct
import subprocess
import sys
import tempfile
import threading
import time
import wave
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import Store
from cue_planner import CuePlanner, CuePlannerError
from cuegen_bridge import CueGenBridge, run_bounded
from library_inventory import LibraryInventory
from rekordbox_backups import RekordboxBackups


# Public interoperability value in upstream CueGen's Generator.cs, not a user
# credential. The fixture deliberately uses the same SQLCipher 4 file format.
# https://github.com/mganss/CueGen/blob/eac34c8c1f007599261f45127095717e0f4abd01/CueGen/Generator.cs
FIXTURE_PASSPHRASE = "402fd482c38817c35ffa8ffb8c7d93143b749e7d315df7a81732a1ff43608497"


def _quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def _section(magic, header_length, body):
    return struct.pack(">4sII", magic, header_length, 12 + len(body)) + body


def _analysis(section):
    return struct.pack(">4sII", b"PMAI", 28, 28 + len(section)) + bytes(16) + section


def phrase_fixture(directory):
    """A 96-second / 120 BPM track with three 16-bar phrase groups.

    Independently constructed from the big-endian PQTZ/PSSI layout consumed by
    CueGen/Analysis, pinned at eac34c8c1f007599261f45127095717e0f4abd01.
    There are no downloaded recordings or user analysis files in this check.
    """
    directory.mkdir(parents=True)
    beats = b"".join(struct.pack(">HHI", number % 4 + 1, 12000, number * 500)
                     for number in range(193))
    grid = struct.pack(">III", 0, 0, 193) + beats
    (directory / "ANLZ0000.DAT").write_bytes(_analysis(_section(b"PQTZ", 24, grid)))

    # Low-mood phrase IDs 1,9,10 mean Intro,Chorus,Outro. Beat numbering is 1-based.
    phrases = b"".join(struct.pack(">HHH", index, beat, kind) + bytes(18)
                       for index, (beat, kind) in enumerate(((1, 1), (65, 9), (129, 10)), 1))
    phrase_header = struct.pack(">IHH", 24, 3, 3) + bytes(6) + struct.pack(">H", 193) + bytes(4)
    (directory / "ANLZ0000.EXT").write_bytes(_analysis(_section(b"PSSI", 32, phrase_header + phrases)))


def database_fixture(sqlcipher, target, audio, other_audio, hot_cues=()):
    """Create a closed encrypted fixture containing two tracks and one old cue."""
    sql = "\n".join([
        "PRAGMA key=" + _quote(FIXTURE_PASSPHRASE) + ";",
        "PRAGMA cipher_compatibility=4;",
        "PRAGMA journal_mode=DELETE;",
        "CREATE TABLE djmdContent (ID TEXT PRIMARY KEY, FolderPath TEXT, FileNameL TEXT,"
        " Title TEXT, BPM INTEGER, Length INTEGER, AnalysisDataPath TEXT, UUID TEXT,"
        " created_at TEXT, updated_at TEXT);",
        "CREATE TABLE djmdCue (ID TEXT PRIMARY KEY, ContentID TEXT, InMsec INTEGER,"
        " OutMsec INTEGER, Kind INTEGER, Comment TEXT, UUID TEXT, created_at TEXT);",
        "CREATE TABLE contentCue (ID TEXT PRIMARY KEY, ContentID TEXT, Cues TEXT);",
        "CREATE TABLE djmdSongMyTag (ID TEXT PRIMARY KEY, ContentID TEXT, MyTagID TEXT, created_at TEXT);",
        "INSERT INTO djmdContent VALUES ('17'," + _quote(audio) + "," + _quote(audio.name)
        + ",'Synthetic phrase check',12000,96,'ANLZ/fixture/ANLZ0000.DAT',"
        "'00000000-0000-0000-0000-000000000017','2026-01-01T00:00:00.000+00:00','2026-01-01T00:00:00.000+00:00');",
        "INSERT INTO djmdContent VALUES ('18'," + _quote(other_audio) + "," + _quote(other_audio.name)
        + ",'Must remain outside the selected track',12000,96,'ANLZ/fixture/ANLZ0000.DAT',"
        "'00000000-0000-0000-0000-000000000018','2026-01-01T00:00:00.000+00:00','2026-01-01T00:00:00.000+00:00');",
        "INSERT INTO djmdCue VALUES ('1','17',12000,-1,0,'Keep memory cue',"
        "'00000000-0000-0000-0000-000000000001','2026-01-01T00:00:00.000+00:00');",
        "INSERT INTO contentCue VALUES ('1','17','[]');",
    ])
    for index, (slot, seconds) in enumerate(hot_cues, 2):
        kind = slot + 1 if slot < 3 else slot + 2
        sql += ("\nINSERT INTO djmdCue VALUES (" + _quote(index) + ",'17',"
                + str(seconds * 1000) + ",-1," + str(kind) + ","
                + _quote("Keep " + chr(65 + slot)) + ","
                + _quote("00000000-0000-0000-0000-" + str(index).zfill(12))
                + ",'2026-01-01T00:00:00.000+00:00');")
    sql += "\nPRAGMA integrity_check;"
    result = subprocess.run([str(sqlcipher), str(target)], input=sql, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            timeout=30, check=False)
    if result.returncode or not target.is_file() or "ok" not in result.stdout.splitlines():
        raise AssertionError("SQLCipher could not create the disposable encrypted test fixture: "
                             + result.stderr[-2000:])
    if target.read_bytes().startswith(b"SQLite format 3"):
        raise AssertionError("The fixture must actually be encrypted.")


def _hashes(paths):
    return {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}


def native_host_diagnostics():
    """Identify the real test host and its existing Intel execution support.

    These are fixed Apple OS utilities, not a CueGen fallback. Do not install
    Rosetta or change the machine's configuration to make a check pass.
    """
    if sys.platform != "darwin":
        raise RuntimeError("This check requires a real macOS host.")
    machine = platform.machine()
    print("Native test host architecture:", machine)
    expected = os.environ.get("DJLIST_EXPECTED_HOST_ARCH")
    if expected and machine != expected:
        raise RuntimeError("Native test host architecture mismatch: " + machine + "; expected " + expected)
    for label, argv in (
        ("macOS version", ["/usr/bin/sw_vers"]),
        ("Existing OS x86_64 execution support", ["/usr/bin/arch", "-x86_64", "/usr/bin/uname", "-m"]),
    ):
        result = run_bounded(argv, cwd=Path.cwd(), env=dict(os.environ), timeout=15, output_limit=8192)
        print(label + ":", result)
        if result["returncode"] != 0:
            raise RuntimeError(label + " could not be verified; no CueGen process was started.")


def _ci_native_diagnostics(started_at):
    """Bounded CI-only evidence for native launch failures; no policy changes.

    Read only newly created CueGen reports from this disposable CI machine.
    Never inspect the user's existing crash reports when they run this script.
    """
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return
    # Compare the loader's shipped rules with the narrow production allowlist.
    # Fixed OS-owned paths only; never import an unchecked profile into policy.
    for name in ("/System/Library/Sandbox/Profiles/dyld-support.sb",
                 "/usr/share/sandbox/dyld-support.sb",
                 "/System/Library/Sandbox/Profiles/rosetta-support.sb",
                 "/usr/share/sandbox/rosetta-support.sb"):
        path = Path(name)
        try:
            if path.is_file() and path.stat().st_size <= 32 * 1024:
                print("CI OS dyld profile:", name)
                print(path.read_text(errors="replace"))
        except OSError as error:
            print("CI OS dyld profile unavailable:", str(error))
    for name in ("/System/Cryptexes/App", "/System/Cryptexes/OS",
                 "/System/Volumes/Preboot/Cryptexes/App/System",
                 "/System/Volumes/Preboot/Cryptexes/OS", "/private/var/db/dyld"):
        path = Path(name)
        print("CI OS cache path:", name, "=>", path.resolve(), "exists:", path.exists())
    try:
        result = run_bounded([
            "/usr/bin/log", "show", "--last", "2m", "--style", "compact", "--info", "--debug",
            "--predicate", 'eventMessage CONTAINS[c] "CueGen.Console" OR '
            '((process == "sandboxd" OR process == "kernel") AND '
            'eventMessage CONTAINS[c] "deny")',
        ], cwd=Path.cwd(), env=dict(os.environ), timeout=15, output_limit=128 * 1024)
        print("CI sandbox/loader diagnostics:", result["returncode"])
        print(result["output"][-16000:])
    except Exception as error:
        print("CI unified-log diagnostics unavailable:", str(error))
    # ReportCrash can write its file shortly after the child has been reaped.
    time.sleep(2)
    reports = []
    for directory in (Path.home() / "Library/Logs/DiagnosticReports",
                      Path("/Library/Logs/DiagnosticReports")):
        try:
            for path in directory.glob("CueGen*"):
                info = path.lstat()
                if (stat.S_ISREG(info.st_mode) and info.st_mtime >= started_at
                        and info.st_size <= 512 * 1024):
                    reports.append((info.st_mtime, path))
        except OSError:
            continue
    for _, path in sorted(reports, reverse=True)[:2]:
        try:
            content = path.read_text(errors="replace")
            print("CI new CueGen diagnostic report:", path.name)
            if path.suffix == ".ips":
                decoder = json.JSONDecoder()
                header, end = decoder.raw_decode(content)
                report = json.loads(content[end:].strip())
                selected = {key: report.get(key) for key in
                            ("procName", "procPath", "osVersion", "exception",
                             "termination", "asi", "faultingThread")}
                thread = report.get("faultingThread")
                threads = report.get("threads", [])
                if isinstance(thread, int) and 0 <= thread < len(threads):
                    selected["faultingFrames"] = threads[thread].get("frames", [])[:20]
                    image_ids = {frame.get("imageIndex") for frame in selected["faultingFrames"]}
                    selected["usedImages"] = {str(index): value for index, value in
                                              enumerate(report.get("usedImages", [])) if index in image_ids}
                print(json.dumps({"header": header, "report": selected}, ensure_ascii=False)[:20000])
            else:
                print(content[:16000])
        except (OSError, ValueError, TypeError) as error:
            print("CI crash report could not be read:", str(error))


def run(sqlcipher, hot_cues=()):
    if sys.platform != "darwin":
        raise RuntimeError("This check requires a real macOS runner and sandbox-exec; there is no unsandboxed fallback.")
    sqlcipher = Path(sqlcipher).resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix="djlist-native-check-") as temporary:
        root = Path(temporary).resolve()
        music = root / "DJ Music"
        database_dir = root / "Synthetic collection"
        music.mkdir()
        database_dir.mkdir()
        audio = music / "Fictional artist - Phrase check.wav"
        other_audio = music / "Fictional artist - Unselected.wav"
        for path in (audio, other_audio):
            with wave.open(str(path), "wb") as out:
                out.setnchannels(1)
                out.setsampwidth(2)
                out.setframerate(8000)
                out.writeframes(bytes(96 * 8000 * 2))
        analysis = database_dir / "share"
        phrase_fixture(analysis / "ANLZ" / "fixture")
        master = database_dir / "master.db"
        database_fixture(sqlcipher, master, audio, other_audio, hot_cues)

        source_xml = root / "synthetic.xml"
        document = ET.Element("DJ_PLAYLISTS", {"Version": "1.0.0"})
        collection = ET.SubElement(document, "COLLECTION", {"Entries": "1"})
        track = ET.SubElement(collection, "TRACK", {
            "TrackID": "17", "Name": "Synthetic phrase check", "Artist": "Fictional artist",
            "TotalTime": "96", "AverageBpm": "120", "Location": audio.as_uri(),
            "Comments": "Keep original metadata", "Rating": "255"})
        ET.SubElement(track, "TEMPO", {"Inizio": "0", "Bpm": "120", "Metro": "4/4", "Battito": "1"})
        ET.SubElement(track, "POSITION_MARK", {"Name": "Keep memory cue", "Type": "0", "Start": "12", "Num": "-1"})
        for slot, seconds in hot_cues:
            ET.SubElement(track, "POSITION_MARK", {"Name": "Keep " + chr(65 + slot), "Type": "0",
                                                   "Start": str(seconds), "Num": str(slot)})
        source_xml.write_bytes(ET.tostring(document, encoding="utf-8", xml_declaration=True))
        source_paths = [master, audio, other_audio, source_xml, *sorted(analysis.rglob("*.DAT")), *sorted(analysis.rglob("*.EXT"))]
        before = _hashes(source_paths)

        store = Store(root / "DJList state")
        store.put_settings({"mode": "real", "music_root": str(music), "rekordbox_xml": str(source_xml),
                            "rekordbox_db": str(master), "rekordbox_analysis_root": str(analysis)})
        library = LibraryInventory(store)
        library.refresh_xml(str(source_xml), str(music))
        entry_id = next(row["id"] for row in library._entries(str(music)) if row["path"] == str(audio))
        planner = CuePlanner(store, library)
        # Synthetic-only injection, for the command-line filename reason above.
        closed_fixture_tools = lambda: {"known": True, "running": [], "message": "Dedicated synthetic CI fixture"}
        backups = RekordboxBackups(store, threading.RLock(), process_checker=closed_fixture_tools)
        engine_results = []

        def recorded_real_runner(argv, **options):
            # This delegates to the production subprocess implementation. Logs
            # are printed only on failure and refer solely to this synthetic
            # fixture, making native loader/SQLCipher CI failures diagnosable.
            if os.environ.get("GITHUB_ACTIONS") == "true" and argv[:2] == ["/usr/bin/sandbox-exec", "-p"]:
                # Denial logging changes no sandbox permissions and helps
                # identify the actual operation rejected by a CI OS update.
                argv = [*argv[:2], argv[2] + "\n(debug deny)", *argv[3:]]
            result = run_bounded(argv, **options)
            engine_results.append(result)
            return result

        bridge = CueGenBridge(store, backups, planner, process_checker=closed_fixture_tools,
                              runner=recorded_real_runner)
        started_at = time.time()
        try:
            prepared = bridge.prepare_runtime()
            print("Actual sandboxed CueGen runtime check:", prepared)
            options = {"engine": "cuegen", "source": "phrases", "cue_type": "hot",
                       "max_cues": 8, "min_distance_bars": 4}
            if any(slot == 7 for slot, _ in hot_cues):
                try:
                    bridge.plan(entry_id, options)
                except CuePlannerError as error:
                    if error.code != "cuegen_hotcue_slots":
                        raise
                    output = engine_results[-1]["output"]
                    if '"Kind":10,' not in output or "Finished cue points creation without errors" not in output:
                        raise AssertionError("The native high-slot case must reproduce actual upstream overflow.")
                    print("PASS: actual CueGen appends beyond H; invalid slots are rejected with memory-cue guidance.")
                else:
                    raise AssertionError("The pinned engine's beyond-H proposals must never become exportable.")
                # The advised fallback must also work with the original B/H
                # slots still present in both encrypted fixture and source XML.
                options["cue_type"] = "memory"
            plan = bridge.plan(entry_id, options)
            actual = [(row["name"], row["time"], row["slot"]) for row in plan["suggestions"]]
            first_slot = max((slot for slot, _ in hot_cues), default=-1) + 1
            slots = [-1] * 3 if options["cue_type"] == "memory" else list(range(first_slot, first_slot + 3))
            expected = [(name, seconds, slot) for (name, seconds), slot in
                        zip((("Intro", 0.0), ("Chorus", 32.0), ("Outro", 64.0)), slots)]
            if actual != expected:
                raise AssertionError("Real engine marker mismatch: " + repr(actual) + "; warnings=" + repr(plan["warnings"]))
            exported = planner.export(plan["token"], [row["id"] for row in plan["suggestions"]])
            saved_track = ET.fromstring(exported["xml"]).find("COLLECTION/TRACK")
            if saved_track is None or saved_track.attrib != track.attrib:
                raise AssertionError("Export changed original track metadata.")
            original_children = [ET.tostring(child) for child in track]
            if [ET.tostring(child) for child in saved_track][:len(original_children)] != original_children:
                raise AssertionError("Export changed the existing grid, memory cue or hot cues.")
            if len(saved_track.findall("POSITION_MARK")) != 4 + len(hot_cues):
                raise AssertionError("Expected every original cue and exactly three reviewed additions.")
            if _hashes(source_paths) != before:
                raise AssertionError("A source database, audio, XML or analysis file changed.")
            records = backups.status()["backups"]
            if not records or not all(record["verification"] == "verified" for record in records):
                raise AssertionError("The real bridge must retain a verified source snapshot.")
            print("PASS: actual pinned CueGen, mandatory macOS sandbox, encrypted synthetic database, "
                  "native SQLCipher, phrase/grid parsing, exact selected track, Intro/Chorus/Outro at 0/32/64s, "
                  "reviewed XML preserves original markers and metadata, all source hashes unchanged.")
            print("Verified cue layout:", {"existing_hot_slots": [chr(65 + slot) for slot, _ in hot_cues],
                                           "generated_type": options["cue_type"], "generated_slots": slots})
        except Exception:
            for result in engine_results[-2:]:
                print("Real fixture engine return code:", result["returncode"])
                print(result["output"][-8000:])
            _ci_native_diagnostics(started_at)
            raise
        finally:
            backups.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sqlcipher", required=True, help="Official SQLCipher CLI used only to build the synthetic CI database")
    arguments = parser.parse_args()
    native_host_diagnostics()
    run(arguments.sqlcipher)
    run(arguments.sqlcipher, hot_cues=((1, 24),))
    run(arguments.sqlcipher, hot_cues=((1, 24), (7, 48)))
