"""Run an explicitly selected CueGen executable on an isolated database copy.

The live database is never passed to CueGen. The mandatory macOS sandbox denies
network and source writes, and allows writes only in the one disposable run
folder. CueGen's dry-run flag is additional protection, not the write boundary.
Only validated log proposals become a reviewed, separate XML export.
"""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import selectors
import shutil
import signal
import stat
import subprocess
import sys
import threading
import time

from cue_planner import CuePlannerError, _integer
from rekordbox_backups import (MAX_DATABASE_BYTES, _absolute, _directory,
                               _directory_current, _fingerprint, _identity,
                               check_rekordbox_closed)
from spotify_api import AppError

SUPPORTED_VERSION = "1.0.37"
RUN_SECONDS = 120
MAX_OUTPUT_BYTES = 2 * 1024 * 1024
MAX_EXECUTABLE_BYTES = 256 * 1024 * 1024
COPY_SECONDS = 180
OPTIONS = {"engine", "source", "cue_type", "max_cues", "min_distance_bars",
           "min_phrase_bars", "offset_beats", "snap", "color_mode", "colors",
           "comment", "phrase_names", "phrase_order", "loop_intro", "loop_outro"}
MACHO_MAGIC = {b"\xfe\xed\xfa\xce", b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xcf",
               b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca",
               b"\xca\xfe\xba\xbf", b"\xbf\xba\xfe\xca"}

# CueGen colour data, MIT, Copyright (c) 2021 Michael Ganss.
# Source commit eac34c8c1f007599261f45127095717e0f4abd01, CueGen/ColorTable.cs.
# The complete upstream licence is retained at vendor/cuegen/LICENSE.
HOT_COLOURS = ((40, 226, 20), (48, 90, 255), (80, 115, 255), (80, 140, 255), (80, 160, 255), (80, 180, 255), (80, 176, 242), (80, 174, 232), (69, 172, 219), (0, 224, 255), (25, 218, 240), (50, 210, 230), (33, 180, 185), (32, 170, 160), (31, 163, 146), (25, 160, 140), (20, 165, 132), (20, 170, 125), (16, 177, 118), (48, 210, 110), (55, 222, 90), (60, 235, 80), (40, 226, 20), (125, 193, 61), (140, 200, 50), (155, 215, 35), (165, 225, 22), (165, 220, 10), (170, 210, 8), (180, 200, 5), (180, 190, 4), (186, 180, 4), (195, 175, 4), (225, 170, 0), (255, 160, 0), (255, 150, 0), (255, 140, 0), (255, 117, 0), (224, 100, 27), (224, 70, 30), (224, 48, 30), (224, 40, 35), (230, 40, 40), (255, 55, 111), (255, 45, 111), (255, 18, 123), (245, 30, 140), (235, 45, 160), (230, 55, 180), (222, 68, 207), (222, 68, 141), (230, 48, 180), (230, 25, 220), (230, 0, 255), (220, 0, 255), (204, 0, 255), (180, 50, 255), (185, 60, 255), (197, 66, 255), (170, 90, 255), (170, 114, 255), (130, 114, 255), (100, 115, 255))
MEMORY_COLOURS = ((248, 112, 248), (248, 0, 0), (248, 163, 48), (248, 227, 48), (0, 224, 0), (0, 192, 248), (0, 80, 248), (152, 8, 248))


def _error(message, code="cuegen_unavailable", status=409):
    return CuePlannerError(message, code, status)


def validate_options(value):
    if not isinstance(value, dict) or set(value) - OPTIONS or value.get("engine", "cuegen") != "cuegen":
        raise _error("Choose only the supported CueGen review options.", "invalid_options", 400)
    result = {"engine": "cuegen", "source": "phrases", "cue_type": "hot", "max_cues": 8,
              "min_distance_bars": 4, "min_phrase_bars": 4, "offset_beats": 0, "snap": True,
              "color_mode": "phrase", "colors": "", "comment": "Energy #",
              "phrase_names": "Intro,Verse,Bridge,Chorus,Outro,Up,Down",
              "phrase_order": "Intro,Outro,Verse,Chorus,Bridge,Up/Down", "loop_intro": 0, "loop_outro": 0}
    result.update(value)
    for key, allowed in (("source", {"phrases", "mixed_in_key"}), ("cue_type", {"hot", "memory"}),
                         ("color_mode", {"phrase", "energy", "sequence"})):
        if not isinstance(result[key], str) or result[key] not in allowed:
            raise _error("Choose a valid CueGen " + key.replace("_", " ") + ".", "invalid_options", 400)
    for key, low, high in (("max_cues", 1, 8 if result["cue_type"] == "hot" else 10),
                           ("min_distance_bars", 0, 128), ("min_phrase_bars", 0, 128),
                           ("offset_beats", -128, 128), ("loop_intro", 0, 512), ("loop_outro", 0, 512)):
        _integer(result[key], key.replace("_", " "), low, high)
    for key in ("loop_intro", "loop_outro"):
        if result[key] and result[key] & (result[key] - 1):
            raise _error("Loop lengths must be 0 or a power of two, up to 512 beats.", "invalid_options", 400)
    if not isinstance(result["snap"], bool):
        raise _error("Choose whether to snap cues to bar starts.", "invalid_options", 400)
    for key, maximum in (("colors", 120), ("comment", 120), ("phrase_names", 250), ("phrase_order", 250)):
        text = result[key]
        if (not isinstance(text, str) or len(text) > maximum or any(ord(c) < 32 or ord(c) == 127 for c in text)):
            raise _error("Use valid CueGen " + key.replace("_", " ") + ".", "invalid_options", 400)
    if result["colors"]:
        parts = [part.strip() for part in result["colors"].split(",")]
        if len(parts) > 16 or any(not re.fullmatch(r"[0-9]{1,2}", p) or not 1 <= int(p) <= 16 for p in parts):
            raise _error("Use a colour sequence of 1 to 16, separated by commas.", "invalid_options", 400)
        result["colors"] = ",".join(str(int(p)) for p in parts)
    names = result["phrase_names"].split(",")
    if len(names) != 7 or any(not part.strip() for part in names):
        raise _error("Provide seven comma-separated phrase names.", "invalid_options", 400)
    order = [part.strip() for group in result["phrase_order"].split(",") for part in group.split("/")]
    if len(order) != 7 or {part.casefold() for part in order} != {"intro", "verse", "bridge", "chorus", "outro", "up", "down"}:
        raise _error("Phrase priority must include Intro, Verse, Bridge, Chorus, Outro, Up and Down once each.", "invalid_options", 400)
    return result


def _arguments(executable, database, path, options):
    # Every argument is a separate argv item; no shell or user-supplied flags.
    args = [str(executable), "--db=" + str(database), "--glob=" + path, "--dryrun", "--merge",
            "--backup-", "--progress-", "--verbosity=info", "--console", "--mytag-", "--energy-",
            "--hotcues" if options["cue_type"] == "hot" else "--hotcues-",
            "--phrase" if options["source"] == "phrases" else "--phrase-",
            "--snap" if options["snap"] else "--snap-",
            "--energycolor" if options["color_mode"] == "energy" else "--energycolor-",
            "--phrasecolor" if options["color_mode"] == "phrase" else "--phrasecolor-",
            "--max=" + str(options["max_cues"]), "--distance=" + str(options["min_distance_bars"]),
            "--phraselength=" + str(options["min_phrase_bars"]), "--offset=" + str(options["offset_beats"]),
            "--comment=" + options["comment"], "--names=" + options["phrase_names"],
            "--order=" + options["phrase_order"], "--loopintro=" + str(options["loop_intro"]),
            "--loopoutro=" + str(options["loop_outro"])]
    if options["colors"]:
        args.append("--colors=" + options["colors"])
    return args


def sandbox_profile(work, executable, audio, analysis, source):
    quote = lambda value: json.dumps(str(value), ensure_ascii=False)
    # The analysis link in work/share is read-only after canonical resolution.
    # Explicit source denials also protect cases where broad paths overlap.
    reads = " ".join("(subpath " + quote(path) + ")" for path in
                     (work, analysis, "/System/Library", "/usr/lib", "/usr/share", "/private/var/db/dyld"))
    literals = " ".join("(literal " + quote(path) + ")" for path in
                        (executable, audio, "/dev/urandom", "/dev/random", "/dev/null", "/private/etc/localtime"))
    return "\n".join([
        "(version 1)", "(deny default)", "(deny network*)",
        "(allow process-fork)", "(allow process-exec (literal " + quote(executable) + "))",
        "(allow sysctl-read)", "(allow mach-lookup)", "(allow file-read-metadata)",
        "(allow file-read-data " + reads + " " + literals + ")",
        "(allow file-write* (subpath " + quote(work) + "))",
        "(deny file-write* (subpath " + quote(source.parent) + ") (subpath " + quote(analysis) + ") (literal " + quote(audio) + "))",
        "(deny file-read-data (literal " + quote(source) + "))",
    ])


def run_bounded(argv, *, cwd, env, timeout=RUN_SECONDS, output_limit=MAX_OUTPUT_BYTES):
    """Capture one isolated child with a real byte and wall-clock budget."""
    process = subprocess.Popen(argv, cwd=str(cwd), env=env, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               start_new_session=True, bufsize=0)
    output = bytearray()
    selector = selectors.DefaultSelector()
    deadline = time.monotonic() + timeout
    try:
        selector.register(process.stdout, selectors.EVENT_READ)
        while selector.get_map():
            if time.monotonic() >= deadline:
                raise _error("CueGen exceeded its two-minute review limit. The live database was not passed to it.", "cuegen_timeout")
            for key, _ in selector.select(timeout=min(1, max(.01, deadline - time.monotonic()))):
                chunk = os.read(key.fileobj.fileno(), 32768)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                output.extend(chunk)
                if len(output) > output_limit:
                    raise _error("CueGen produced too much output for a bounded review.", "cuegen_output_limit")
        code = process.wait(timeout=max(.01, deadline - time.monotonic()))
        return {"returncode": code, "output": bytes(output).decode("utf-8", errors="strict")}
    except (UnicodeError, subprocess.TimeoutExpired):
        raise _error("CueGen did not return a complete, readable result.", "cuegen_output_invalid") from None
    finally:
        selector.close()
        # A leader can exit while a child keeps stdout or the working folder
        # open. The process group belongs to this invocation, so terminate the
        # whole group even when the leader has already been reaped.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=5)
        process.stdout.close()


def parse_proposals(result, source, options):
    output = result.get("output") if isinstance(result, dict) else None
    if not isinstance(output, str) or len(output.encode("utf-8")) > MAX_OUTPUT_BYTES:
        raise _error("CueGen returned an invalid or oversized response.", "cuegen_output_invalid")
    output = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", output)
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    versions = re.findall(r"^Info: Starting CueGen version ([0-9.]+)\s*$", output, re.M)
    if versions != [SUPPORTED_VERSION] and versions != [SUPPORTED_VERSION + ".0"]:
        raise _error("This adapter supports CueGen 1.0.37. Select that version before running a review.", "cuegen_version")
    if (result.get("returncode") not in {0, 1}
            or any(re.match(r"^(Error|Fatal):", line, re.I) for line in lines)
            or lines.count("Info: Finished cue points creation without errors") != 1
            or not lines or lines[-1] != "Info: Finished cue points creation without errors"):
        raise _error("CueGen did not finish successfully. Check its version, the saved analysis folder and available phrase/Mixed In Key data.", "cuegen_failed")
    # Upstream v1.0.37 Generator.Generate returns 'error', while Program negates
    # it. Exit 1 is therefore accepted ONLY with this exact version/success log.
    track_id = source["track"].get("TrackID", "")
    path = source["entry"]["path"]
    expected = ("Info: Getting cue points from phrase analysis for " + track_id + " with file at " + path
                if options["source"] == "phrases" else "Info: Reading cue points for " + track_id + " from tag of " + path)
    headers = [line for line in lines if line.startswith("Info: Getting cue points from phrase analysis for ") or line.startswith("Info: Reading cue points for ")]
    if headers != [expected]:
        raise _error("CueGen did not identify exactly the selected XML track and path. No suggestions were accepted.", "cuegen_track_mismatch")
    if any("BPM is unknown, assuming" in line for line in lines):
        raise _error("CueGen would have assumed a BPM. Analyse this track in Rekordbox and export fresh XML first.", "cuegen_analysis_required")
    proposals = []
    for line in lines:
        if not line.startswith("Info: Created cue point "):
            continue
        if len(proposals) >= 100:
            raise _error("CueGen produced more suggestions than a single-track review allows.", "cuegen_output_invalid")
        try:
            row = json.loads(line[len("Info: Created cue point "):])
            if not isinstance(row, dict) or str(row.get("ContentID", "")) != track_id:
                raise ValueError()
            start, end, kind = row.get("InMsec"), row.get("OutMsec", -1), row.get("Kind")
            if any(isinstance(n, bool) or not isinstance(n, int) for n in (start, end, kind)):
                raise ValueError()
            if not 0 <= start < source["duration"] * 1000 or end != -1 and not start < end <= source["duration"] * 1000:
                raise ValueError()
            if options["cue_type"] == "hot":
                if kind not in {1, 2, 3, 5, 6, 7, 8, 9}:
                    raise ValueError()
                slot = kind - 1 if kind < 4 else kind - 2
                colour = row.get("ColorTableIndex")
                rgb = HOT_COLOURS[colour] if isinstance(colour, int) and not isinstance(colour, bool) and 0 <= colour < len(HOT_COLOURS) else None
            else:
                if kind not in {0, 4} or (kind == 4) != (end != -1):
                    raise ValueError()
                slot = -1
                colour = row.get("Color")
                rgb = MEMORY_COLOURS[colour] if isinstance(colour, int) and not isinstance(colour, bool) and 0 <= colour < len(MEMORY_COLOURS) else None
            name = row.get("Comment") or "CueGen cue " + str(len(proposals) + 1)
            if not isinstance(name, str) or len(name) > 200 or any(ord(c) < 32 or ord(c) == 127 for c in name):
                raise ValueError()
        except (ValueError, TypeError, OverflowError):
            raise _error("CueGen returned an invalid cue record. No suggestions were accepted.", "cuegen_output_invalid") from None
        proposal = {"name": name, "time": start / 1000, "slot": slot}
        if end != -1:
            proposal["end"] = end / 1000
        if rgb is not None:
            proposal["rgb"] = list(rgb)
        proposals.append(proposal)
    warnings = []
    if any(line.startswith("Warn:") for line in lines):
        warnings.append("CueGen reported analysis warnings. Review the generated markers carefully; missing analysis may leave no suggestions.")
    if not proposals:
        warnings.append("CueGen produced no new markers for this track. Check its analysis data and existing cues.")
    return proposals, warnings


class CueGenBridge:
    def __init__(self, store, backups, planner, runner=None, process_checker=None,
                 platform=None, sandbox_path="/usr/bin/sandbox-exec"):
        self.store, self.backups, self.planner = store, backups, planner
        self.platform = platform or sys.platform
        self.runner = runner or run_bounded
        self.process_checker = process_checker or (lambda: check_rekordbox_closed(self.platform))
        self.sandbox_path = sandbox_path
        self._lock = threading.Lock()

    def _supported(self):
        try:
            info = Path(self.sandbox_path).lstat()
            return self.platform == "darwin" and stat.S_ISREG(info.st_mode) and os.access(self.sandbox_path, os.X_OK)
        except OSError:
            return False

    def status(self):
        settings = self.store.settings()
        real = settings.get("mode", "real") == "real"
        configured = bool(settings.get("cuegen_path")) and real
        supported = self._supported()
        message = ("CueGen is paused in the demo." if not real else
                   "CueGen review requires macOS and its sandbox-exec protection. No unsandboxed fallback is available." if not supported else
                   "Choose the installed CueGen 1.0.37 executable in Settings." if not configured else
                   "CueGen is configured. Its version is checked during a reviewed run on an isolated database copy.")
        return {"configured": configured, "supported": supported, "message": message,
                "required_version": SUPPORTED_VERSION, "version": None,
                "live_database_writes": False, "engine": "cuegen"}

    @staticmethod
    def _configuration(settings):
        return tuple(settings.get(key, "") for key in
                     ("mode", "music_root", "rekordbox_xml", "rekordbox_db", "cuegen_path", "rekordbox_analysis_root"))

    def _closed(self):
        result = self.process_checker()
        if (not isinstance(result, dict) or result.get("known") is not True
                or not isinstance(result.get("running"), list) or result["running"]):
            raise _error("Close Rekordbox, CueGen and other library-writing tools before this review. Their process state must be verifiable.", "cuegen_library_busy")

    def _capture(self, settings):
        source = _absolute(settings.get("rekordbox_db"), "the active Rekordbox master.db")
        if source.name != "master.db":
            raise _error("Select the actual active master.db in Settings.")
        executable = _absolute(settings.get("cuegen_path"), "the installed CueGen executable")
        analysis = _absolute(settings.get("rekordbox_analysis_root") or str(source.parent / "share"), "the Rekordbox analysis share folder")
        if source == analysis or analysis in source.parents:
            raise _error("Choose the analysis share folder, not a folder containing master.db.")
        with _directory(source.parent) as directory:
            info = os.stat(source.name, dir_fd=directory, follow_symlinks=False)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise _error("The selected database must be a regular, unlinked file.")
            source_fp = _fingerprint(info)
        with _directory(executable.parent) as directory:
            descriptor = os.open(executable.name, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0), dir_fd=directory)
            try:
                info = os.fstat(descriptor)
                if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or not 0 < info.st_size <= MAX_EXECUTABLE_BYTES
                        or not info.st_mode & 0o111 or os.read(descriptor, 4) not in MACHO_MAGIC):
                    raise _error("Choose a regular macOS CueGen executable. Scripts, symlinks and hardlinks are not supported.")
                executable_fp = _fingerprint(info)
            finally:
                os.close(descriptor)
        with _directory(analysis) as directory:
            analysis_identity = _identity(os.fstat(directory))
        return {"config": self._configuration(settings), "source": source, "source_fp": source_fp,
                "executable": executable, "executable_fp": executable_fp,
                "analysis": analysis, "analysis_identity": analysis_identity}

    def _validate(self, context):
        if self._configuration(self.store.settings()) != context["config"]:
            raise _error("CueGen settings changed. Create a fresh review.", "stale_plan")
        current = self._capture(self.store.settings())
        for key in ("source_fp", "executable_fp", "analysis_identity"):
            if current[key] != context[key]:
                raise _error("The database, executable or analysis folder changed. Create a fresh review.", "stale_plan")

    @contextmanager
    def _workspace(self, context):
        root = self.store.directory / "cuegen-work"
        if context["source"].parent in root.parents or root == context["source"].parent:
            raise _error("DJList's working folder must be separate from the Rekordbox database folder.")
        with _directory(root, create=True) as parent:
            name = "review-" + secrets.token_hex(12)
            os.mkdir(name, 0o700, dir_fd=parent)
            work = root / name
            with _directory(work) as directory:
                identity = _identity(os.fstat(directory))
            try:
                for sub in ("home", "tmp", "bundle"):
                    (work / sub).mkdir(mode=0o700)
                os.symlink(context["analysis"], work / "share", target_is_directory=True)
                _directory_current(root, parent)
                yield work
            finally:
                # Cleanup is confined to this random owned working directory.
                # Snapshots and source files are never passed to rmtree.
                try:
                    _directory_current(root, parent)
                    with _directory(work) as directory:
                        if _identity(os.fstat(directory)) != identity:
                            raise OSError("Working directory replaced")
                    if not shutil.rmtree.avoids_symlink_attacks:
                        raise OSError("Safe cleanup unavailable")
                    shutil.rmtree(work)
                except (OSError, AppError):
                    pass  # Retain a failed work folder rather than deleting an unknown path.

    def _copy_snapshot(self, record, work):
        if record.get("verification") != "verified" or record.get("completed") is not True:
            raise _error("A freshly verified database snapshot is required before running CueGen.")
        file = next((item for item in record.get("files", []) if item.get("name") == "master.db"), None)
        if (not file or not isinstance(file.get("size"), int) or not 0 < file["size"] <= MAX_DATABASE_BYTES
                or not re.fullmatch(r"[a-f0-9]{64}", str(file.get("sha256", "")))):
            raise _error("The database snapshot record is invalid.")
        path = _absolute(record.get("path"), "the verified database snapshot")
        digest, amount, deadline = hashlib.sha256(), 0, time.monotonic() + COPY_SECONDS
        with _directory(path) as directory, _directory(work) as target_dir:
            source = os.open("master.db", os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0), dir_fd=directory)
            target = None
            try:
                original = os.fstat(source)
                if not stat.S_ISREG(original.st_mode) or original.st_nlink != 1 or original.st_size != file["size"]:
                    raise _error("The saved database snapshot changed before CueGen review.")
                disk = os.fstatvfs(target_dir)
                if disk.f_bavail * disk.f_frsize < file["size"] + 64 * 1024 * 1024:
                    raise _error("There is not enough space for a separate CueGen working copy.")
                target = os.open("master.db", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=target_dir)
                while True:
                    if time.monotonic() > deadline:
                        raise _error("Preparing the CueGen working copy took too long.", "cuegen_timeout")
                    chunk = os.read(source, 1024 * 1024)
                    if not chunk:
                        break
                    amount += len(chunk)
                    if amount > file["size"]:
                        raise _error("The database snapshot changed while being copied.")
                    digest.update(chunk)
                    pending = memoryview(chunk)
                    while pending:
                        written = os.write(target, pending)
                        if written <= 0:
                            raise OSError("Short working-copy write")
                        pending = pending[written:]
                os.fsync(target)
                if (amount != file["size"] or digest.hexdigest() != file["sha256"]
                        or _fingerprint(os.fstat(source)) != _fingerprint(original)
                        or _fingerprint(os.stat("master.db", dir_fd=directory, follow_symlinks=False)) != _fingerprint(original)):
                    raise _error("The CueGen working copy did not match the verified snapshot.")
            finally:
                os.close(source)
                if target is not None:
                    os.close(target)
        # Read back the written file as well as hashing the source stream.
        # This catches a short/corrupted destination before an engine can use it.
        with _directory(work) as directory:
            descriptor = os.open("master.db", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
            try:
                original = os.fstat(descriptor)
                if not stat.S_ISREG(original.st_mode) or original.st_nlink != 1 or original.st_size != amount:
                    raise _error("The CueGen working copy is invalid.")
                copied = hashlib.sha256()
                checked = 0
                while True:
                    if time.monotonic() > deadline:
                        raise _error("Verifying the CueGen working copy took too long.", "cuegen_timeout")
                    chunk = os.read(descriptor, 1024 * 1024)
                    if not chunk:
                        break
                    checked += len(chunk)
                    if checked > amount:
                        raise _error("The CueGen working copy changed during verification.")
                    copied.update(chunk)
                if (checked != amount or copied.hexdigest() != file["sha256"]
                        or _fingerprint(os.fstat(descriptor)) != _fingerprint(original)
                        or _fingerprint(os.stat("master.db", dir_fd=directory, follow_symlinks=False)) != _fingerprint(original)):
                    raise _error("The CueGen working copy's saved bytes do not match the checked snapshot.")
            finally:
                os.close(descriptor)
        return work / "master.db"

    def plan(self, entry_id, options):
        options = validate_options(options)
        if not self._lock.acquire(blocking=False):
            raise _error("CueGen is already preparing a review. Wait for it to finish.", "cuegen_busy")
        try:
            settings = self.store.settings()
            if settings.get("mode", "real") != "real":
                raise _error("Leave the demo before running CueGen.", "demo_mode")
            if not self._supported():
                raise _error("CueGen requires macOS sandbox-exec. This app never falls back to an unsandboxed run.")
            source = self.planner._source(entry_id)
            path = source["entry"]["path"]
            if (any(char in path for char in "*?[]{}\\") or any(ord(char) < 32 or ord(char) == 127 for char in path) or path != path.strip()
                    or not re.fullmatch(r"[0-9]{1,20}", source["track"].get("TrackID", ""))):
                raise _error("This track's path or XML identifier cannot be matched safely to one CueGen database record.", "cuegen_track_mismatch")
            self._closed()
            context = self._capture(settings)
            backup = self.backups.create(reason="manual")["backup"]
            if (backup.get("source_path") != str(context["source"])
                    or backup.get("source_fingerprint") != context["source_fp"]):
                raise _error("The database changed while preparing its backup. Create a fresh review.", "stale_plan")
            self._validate(context)
            with self._workspace(context) as work:
                database = self._copy_snapshot(backup, work)
                profile = sandbox_profile(work, context["executable"], path, context["analysis"], context["source"])
                args = [self.sandbox_path, "-p", profile] + _arguments(context["executable"], database, path, options)
                env = {"HOME": str(work / "home"), "TMPDIR": str(work / "tmp") + "/",
                       "DOTNET_BUNDLE_EXTRACT_BASE_DIR": str(work / "bundle"),
                       "DOTNET_CLI_HOME": str(work / "home"), "DOTNET_SKIP_FIRST_TIME_EXPERIENCE": "1",
                       "DOTNET_CLI_TELEMETRY_OPTOUT": "1", "DOTNET_EnableDiagnostics": "0",
                       "DOTNET_SYSTEM_GLOBALIZATION_INVARIANT": "1", "PATH": "/usr/bin:/bin",
                       "LANG": "en_US.UTF-8", "LC_ALL": "en_US.UTF-8", "TERM": "dumb", "NO_COLOR": "1"}
                self._validate(context)
                self.planner._revalidate(source)
                self._closed()
                result = self.runner(args, cwd=work, env=env, timeout=RUN_SECONDS, output_limit=MAX_OUTPUT_BYTES)
                proposals, warnings = parse_proposals(result, source, options)
                warnings.append("The database working copy is checked separately from the analysis files. Analysis is read-only, but another application could change it during this run; this is not an atomic snapshot of both. Close analysis tools and review the markers against the audio.")
                self._validate(context)
                self.planner._revalidate(source)
                self._closed()
            return self.planner.accept_external(source, proposals, options, warnings,
                                                validator=lambda: self._validate(context))
        except (OSError, ValueError, RuntimeError) as error:
            if isinstance(error, AppError):
                raise
            raise _error("CueGen review could not run with the required file and sandbox protections. Check the selected executable and analysis folder.") from None
        finally:
            self._lock.release()
