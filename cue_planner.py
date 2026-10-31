"""Reviewed cue suggestions and standalone Rekordbox XML export.

This module reads the saved XML and audio-file identity only. It never opens a
Rekordbox database, executes CueGen, changes audio or imports its output. Grid
marks are calculations from supplied beat positions, not detected song phrases.
"""
from bisect import bisect_left
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import math
import os
from pathlib import Path
import re
import secrets
import stat
import threading
import time
import xml.etree.ElementTree as ET

from audio_metadata import file_fingerprint
from library_inventory import MAX_ENTRIES, MAX_XML_BYTES, _location, xml_fingerprint
from relocation import _root
from spotify_api import AppError


PLAN_SECONDS = 600
MAX_PLANS = 20
MAX_MARKERS = 4096
MAX_GRID_BARS = 100000
MAX_TRACK_BYTES = 256 * 1024
EXPORT_VERSION = "0.7.0"
OPTIONS = {"interval_bars", "max_cues", "bpm", "first_downbeat_seconds", "confirm_manual_grid"}


class CuePlannerError(AppError):
    def __init__(self, message, code="cue_review_required", status=409):
        super().__init__(message, status)
        self.code = code


def _number(value, label, low, high, inclusive_low=True):
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        number = float("nan")
    if isinstance(value, bool) or not math.isfinite(number) or number > high or (
            number < low if inclusive_low else number <= low):
        raise CuePlannerError("Use a valid " + label + ".", "invalid_grid", 400)
    return number


def _integer(value, label, low, high):
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise CuePlannerError("Use a valid " + label + ".", "invalid_options", 400)
    return value


def _xml_integer(value, label, low, high):
    if not isinstance(value, str) or not re.fullmatch(r"-?\d{1,10}", value):
        raise CuePlannerError("The XML has an invalid " + label + ". Export a fresh copy.", "invalid_grid")
    number = int(value)
    if not low <= number <= high:
        raise CuePlannerError("The XML has an invalid " + label + ". Export a fresh copy.", "invalid_grid")
    return number


def _seconds(value):
    return format(value, ".6f").rstrip("0").rstrip(".") or "0"


def _config(settings):
    return (settings.get("mode", "real"), settings.get("music_root", ""), settings.get("rekordbox_xml", ""))


def _read_xml(value):
    """Read one selected export with bounded bytes and stable file identity."""
    if (not isinstance(value, str) or not value or len(value) > 4096
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        raise CuePlannerError("Save a Rekordbox XML export path in Settings, then refresh XML before planning cues.", "xml_required")
    path = Path(value).expanduser()
    if not path.is_absolute() or ".." in path.parts or path.suffix.lower() != ".xml":
        raise CuePlannerError("Choose an absolute path to the actual Rekordbox XML export.", "xml_required")
    try:
        if not hasattr(os, "O_NOFOLLOW"):
            raise OSError()
        flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0)
        with os.fdopen(os.open(str(path), flags), "rb") as file:
            before = os.fstat(file.fileno())
            if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= MAX_XML_BYTES:
                raise CuePlannerError("Choose a nonempty Rekordbox XML export smaller than 20 MB.", "xml_required", 413)
            payload = file.read(MAX_XML_BYTES + 1)
            after = os.fstat(file.fileno())
        fingerprint = xml_fingerprint(before)
        if (fingerprint != xml_fingerprint(after) or fingerprint != xml_fingerprint(path.lstat())
                or len(payload) > MAX_XML_BYTES):
            raise CuePlannerError("The XML export changed while it was read. Refresh XML and make a new cue plan.", "xml_refresh_required")
        text = payload.decode("utf-8-sig")
    except (OSError, UnicodeError, RuntimeError):
        raise CuePlannerError("The saved XML is unavailable. Reconnect its drive and refresh XML before planning cues.", "xml_required") from None
    if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
        raise CuePlannerError("The XML contains unsupported declarations.", "invalid_xml", 400)
    try:
        document = ET.fromstring(text)
    except (ET.ParseError, ValueError):
        raise CuePlannerError("The selected file is not valid XML. Export and refresh it again.", "invalid_xml", 400) from None
    collection = document.find("COLLECTION")
    if document.tag != "DJ_PLAYLISTS" or collection is None:
        raise CuePlannerError("Choose a Rekordbox XML collection export.", "invalid_xml", 400)
    items = collection.findall("TRACK")
    if len(items) > MAX_ENTRIES:
        raise CuePlannerError("Export a collection of no more than 50,000 tracks.", "invalid_xml", 413)
    declared = collection.get("Entries")
    if declared is not None and (not re.fullmatch(r"[0-9]{1,6}", declared) or int(declared) != len(items)):
        raise CuePlannerError("The XML track count does not match its contents. Export it again.", "invalid_xml", 400)
    return items, fingerprint, hashlib.sha256(payload).hexdigest()


def _canonical_location(value, raw_root, root):
    location = _location(value)
    if location is None:
        return None
    path = Path(location)
    for selected in (raw_root, root):
        try:
            return str(root / path.relative_to(selected))
        except ValueError:
            pass
    return None


def _track_bounds(track):
    count = 0
    pending = [(track, 0)]
    while pending:
        element, depth = pending.pop()
        count += 1
        if depth > 8 or count > MAX_MARKERS + 100:
            raise CuePlannerError("This track contains too much nested XML to export safely.", "invalid_xml", 413)
        pending.extend((child, depth + 1) for child in element)
    encoded = ET.tostring(track, encoding="utf-8")
    if len(encoded) > MAX_TRACK_BYTES:
        raise CuePlannerError("This track's XML is too large for cue review.", "invalid_xml", 413)
    return encoded


def _grid(track, duration):
    result = []
    for element in track.findall("TEMPO"):
        if len(result) >= MAX_MARKERS:
            raise CuePlannerError("This track has too many beatgrid changes for cue review.", "unsupported_grid", 413)
        start = _number(element.get("Inizio"), "XML beatgrid start", 0, duration)
        bpm = _number(element.get("Bpm"), "XML BPM", 0, 999, inclusive_low=False)
        meter = element.get("Metro", "")
        if meter != "4/4":
            raise CuePlannerError("Cue calculations currently support 4/4 beatgrids. The original XML is unchanged.", "unsupported_grid")
        beat = _xml_integer(element.get("Battito"), "beat number", 1, 4)
        if result and start <= result[-1]["time"]:
            raise CuePlannerError("XML beatgrid changes must have increasing positions. Check the grid in Rekordbox and export again.", "invalid_grid")
        result.append({"time": start, "bpm": bpm, "meter": meter, "beat": beat})
    return result


def _cues(track, duration):
    result, occupied = [], set()
    for element in track.findall("POSITION_MARK"):
        if len(result) >= MAX_MARKERS:
            raise CuePlannerError("This track has too many existing cues for review.", "invalid_xml", 413)
        # Rekordbox's TotalTime field may be rounded to a whole second. Preserve
        # valid existing final marks, while proposals stay strictly before it.
        start = _number(element.get("Start"), "existing cue position", 0, duration + 1)
        kind = _xml_integer(element.get("Type"), "position-mark type", 0, 4)
        slot = _xml_integer(element.get("Num", "-1"), "cue slot", -1, 32767)
        end = None
        if element.get("End") is not None:
            end = _number(element.get("End"), "existing cue end", start, duration + 1)
        if kind == 4 and (end is None or end <= start):
            raise CuePlannerError("An existing XML loop has an invalid end. Check it in Rekordbox and export again.", "invalid_cues")
        if slot >= 0:
            if slot in occupied:
                raise CuePlannerError("The XML assigns more than one cue to the same hotcue slot. Resolve that before adding cues.", "invalid_cues")
            occupied.add(slot)
        result.append({"name": element.get("Name", ""), "time": start, "end": end, "type": kind, "slot": slot})
    return result, occupied


def _bar_positions(grid, duration):
    """Honor each TEMPO anchor and its bar phase, including tempo changes."""
    positions = []
    for index, section in enumerate(grid):
        end = grid[index + 1]["time"] if index + 1 < len(grid) else duration
        beat_seconds = 60 / section["bpm"]
        first = section["time"] + ((5 - section["beat"]) % 4) * beat_seconds
        step = beat_seconds * 4
        if not math.isfinite(first) or not math.isfinite(step):
            raise CuePlannerError("The XML tempo is outside the supported numeric range.", "invalid_grid")
        count = max(0, int(math.ceil((end - first) / step - 1e-9)))
        if len(positions) + count > MAX_GRID_BARS:
            raise CuePlannerError("The grid contains too many bars for a bounded cue plan.", "unsupported_grid", 413)
        for number in range(count):
            value = first + number * step
            if value < end - 1e-8 and value < duration and (not positions or value - positions[-1] > 1e-5):
                positions.append(value)
    return positions


def _bar_coordinate(positions, seconds):
    index = bisect_left(positions, seconds)
    if index == 0:
        return 0.0
    if index >= len(positions):
        return float(len(positions))
    before, after = positions[index - 1], positions[index]
    return index - 1 + (seconds - before) / (after - before)


class CuePlanner:
    def __init__(self, store, library, clock=None):
        self.store = store
        self.library = library
        self.clock = clock or time.monotonic
        self._lock = threading.RLock()
        self._plans = {}

    def clear(self):
        with self._lock:
            self._plans.clear()

    def _revalidate(self, source):
        """Detect changes during parsing/calculation as well as between requests."""
        try:
            root_identity = _root(source["config"][1])[2]
            xml_identity = xml_fingerprint(Path(source["config"][2]).lstat())
        except (AppError, OSError, ValueError, RuntimeError):
            raise CuePlannerError("A source file or folder became unavailable. Refresh and make a new cue plan.", "stale_plan") from None
        if (_config(self.store.settings()) != source["config"]
                or root_identity != source["root_identity"]
                or xml_identity != source["xml_fingerprint"]
                or file_fingerprint(source["entry"]["path"], source["config"][1]) != source["audio_fingerprint"]):
            raise CuePlannerError("A source file or folder changed. Refresh and make a new cue plan.", "stale_plan")

    def _source(self, entry_id):
        settings = self.store.settings()
        config = _config(settings)
        if config[0] != "real":
            raise CuePlannerError("Leave the demo before preparing a real cue export.", "demo_mode")
        if not isinstance(entry_id, str) or not re.fullmatch(r"[a-f0-9]{24}", entry_id):
            raise CuePlannerError("Choose a track from Review library.", "entry_required", 400)
        raw_root, root, root_identity = _root(config[1])
        entry = next((item for item in self.library._entries(config[1]) if item["id"] == entry_id), None)
        if entry is None:
            raise CuePlannerError("That library entry is no longer available. Refresh the library and choose it again.", "entry_required")
        audio_fingerprint = file_fingerprint(entry["path"], config[1])
        if audio_fingerprint is None:
            raise CuePlannerError("Reconnect the music drive and locate this audio file before preparing cues.", "file_unavailable")
        snapshot = self.library._snapshot()
        if (not config[2] or not snapshot.get("source_fingerprint")
                or snapshot.get("source_path") != config[2]):
            raise CuePlannerError("Save the XML export path and use Refresh XML before preparing cues. Existing cues must be available to preserve them.", "xml_required")
        imported_audio = entry.get("xml_audio_fingerprint")
        if not isinstance(imported_audio, dict):
            raise CuePlannerError("No audio-file check was saved with this XML entry. Connect the music drive and use Refresh XML before planning cues.", "xml_refresh_required")
        if imported_audio != audio_fingerprint:
            raise CuePlannerError("The audio file changed since Refresh XML. Check the recording in Rekordbox, export a fresh XML and refresh it before making another cue plan.", "xml_refresh_required")
        items, xml_fp, xml_hash = _read_xml(config[2])
        if xml_fp != snapshot["source_fingerprint"]:
            raise CuePlannerError("The XML file has changed since the library was imported. Refresh XML, then make a new cue plan.", "xml_refresh_required")
        matches = [item for item in items if _canonical_location(item.get("Location"), raw_root, root) == entry["path"]]
        if not matches:
            raise CuePlannerError("This file is not in the selected XML. Export it from Rekordbox and refresh XML before planning cues.", "xml_track_required")
        if len(matches) != 1:
            raise CuePlannerError("The XML contains multiple entries for this file. Review those duplicates before preparing cues.", "ambiguous_xml_track")
        track = matches[0]
        track_xml = _track_bounds(track)
        duration = _number(track.get("TotalTime"), "track duration from XML", 0, 36000, inclusive_low=False)
        source = {"config": config, "root_identity": root_identity, "entry_id": entry_id,
                  "entry": entry, "track": track, "track_xml": track_xml, "duration": duration,
                  "xml_fingerprint": xml_fp, "xml_hash": xml_hash, "audio_fingerprint": audio_fingerprint}
        self._revalidate(source)
        return source

    def plan(self, entry_id, options=None):
        if options is None:
            options = {}
        if not isinstance(options, dict) or set(options) - OPTIONS:
            raise CuePlannerError("Choose valid cue planning options.", "invalid_options", 400)
        interval = _integer(options.get("interval_bars", 32), "bar interval", 8, 64)
        if interval not in {8, 16, 32, 64}:
            raise CuePlannerError("Choose 8, 16, 32 or 64 bars between suggestions.", "invalid_options", 400)
        maximum = _integer(options.get("max_cues", 8), "maximum number of cues", 1, 8)
        source = self._source(entry_id)
        grid = _grid(source["track"], source["duration"])
        existing, occupied = _cues(source["track"], source["duration"])
        manual = bool({"bpm", "first_downbeat_seconds", "confirm_manual_grid"} & set(options))
        if grid and manual:
            raise CuePlannerError("This XML already has a beatgrid. Use it to preserve existing beat markers; edit the grid in Rekordbox if it is wrong.", "existing_grid")
        grid_source = "rekordbox_xml"
        if not grid:
            if options.get("confirm_manual_grid") is not True:
                raise CuePlannerError("This XML track has no beatgrid. Enter BPM and the first downbeat in seconds, then confirm that you checked them.", "manual_grid_required")
            bpm = _number(options.get("bpm"), "BPM between 20 and 400", 20, 400)
            start = _number(options.get("first_downbeat_seconds"), "first downbeat before the end of the track", 0, source["duration"])
            if start >= source["duration"]:
                raise CuePlannerError("The first downbeat must be before the end of the track.", "invalid_grid", 400)
            grid = [{"time": start, "bpm": bpm, "meter": "4/4", "beat": 1}]
            grid_source = "manual"
        positions = _bar_positions(grid, source["duration"])
        occupied_positions = [_bar_coordinate(positions, cue[key]) for cue in existing for key in ("time", "end") if cue[key] is not None] if positions else []
        free_slots = [slot for slot in range(8) if slot not in occupied]
        proposals = []
        skipped = 0
        for index in range(0, len(positions), interval):
            if len(proposals) >= min(maximum, len(free_slots)):
                break
            if any(abs(index - other) < 4 - 1e-7 for other in occupied_positions):
                skipped += 1
                continue
            position = round(positions[index], 6)
            if not 0 <= position < source["duration"]:
                continue  # XML precision must not round a marker to the track end.
            slot = free_slots[len(proposals)]
            proposals.append({"id": "cue-" + str(slot), "name": "DJList grid bar " + str(index + 1),
                              "time": position, "slot": slot, "bar": index + 1})
        warnings = ["These are calculated grid positions, not detected intros, drops, phrases or outros. Listen and review every selected marker.",
                    "Exporting does not import or update Rekordbox. The XML preserves the cues in the selected export; export a fresh copy if you changed cues in Rekordbox.",
                    "Refreshing XML checks which file is present. It cannot prove that the saved beatgrid matches the recording or include changes not exported from Rekordbox.",
                    "Back up the full Rekordbox library before manually importing a cue export. Import behavior on your Rekordbox version is not verified."]
        if grid_source == "manual":
            warnings.append("The constant 4/4 grid uses your confirmed BPM and first downbeat. No audio analysis checked those values.")
        if len(grid) > 1:
            warnings.append("Calculations honor the XML's tempo changes and beat numbers. Check that its grid is correct across transitions.")
        if skipped:
            warnings.append(str(skipped) + " proposed positions were skipped because they were within four grid bars of existing cue or loop positions.")
        if not free_slots:
            warnings.append("All eight hotcue slots are occupied. Existing cues are preserved; no new hotcues can be exported.")
        elif not proposals:
            warnings.append("No unused positions were found with these settings. Existing cues are preserved.")
        self._revalidate(source)
        now = self.clock()
        token = secrets.token_urlsafe(32)
        public = {"token": token, "expires_at": (datetime.now(timezone.utc) + timedelta(seconds=PLAN_SECONDS)).isoformat(timespec="seconds"),
                  "entry_id": entry_id, "track": {"title": source["track"].get("Name") or source["entry"]["title"],
                  "artist": source["track"].get("Artist", ""), "path": source["entry"]["path"], "duration_seconds": source["duration"]},
                  "grid_source": grid_source, "grid": grid, "existing_cues": existing, "suggestions": proposals,
                  "warnings": warnings, "options": {"interval_bars": interval, "max_cues": maximum},
                  "selected_count": 0, "export_kind": "rekordbox_xml", "source_name": Path(source["config"][2]).name}
        with self._lock:
            self._plans = {key: value for key, value in self._plans.items() if value["deadline"] > now}
            while len(self._plans) >= MAX_PLANS:
                self._plans.pop(next(iter(self._plans)))
            self._plans[token] = {**source, "deadline": now + PLAN_SECONDS, "public": deepcopy(public)}
        return public

    def accept_external(self, source, proposals, options, warnings=None, validator=None):
        """Store bounded CueGen suggestions against the same refreshed XML source.

        External processes never receive this planner's XML output path. Existing
        markers remain untouched; only reviewed, unused slots can be exported.
        """
        if not isinstance(source, dict) or not isinstance(proposals, list) or len(proposals) > 100:
            raise CuePlannerError("CueGen returned an invalid or oversized suggestion list.", "invalid_engine_output")
        self._revalidate(source)
        if validator:
            validator()
        existing, occupied = _cues(source["track"], source["duration"])
        grid = _grid(source["track"], source["duration"])
        maximum = _integer(options.get("max_cues", 8), "maximum CueGen suggestions", 1, 10)
        cue_type = options.get("cue_type", "hot")
        if cue_type not in {"hot", "memory"} or (cue_type == "hot" and maximum > 8):
            raise CuePlannerError("Choose up to eight hot cues or ten memory cues.", "invalid_options", 400)
        minimum = _integer(options.get("min_distance_bars", 4), "minimum cue spacing", 0, 128)
        positions = _bar_positions(grid, source["duration"]) if grid else []
        boundaries = [row[key] for row in existing for key in ("time", "end") if row[key] is not None]
        accepted, skipped, seen = [], 0, set()
        for raw in proposals:
            if not isinstance(raw, dict):
                raise CuePlannerError("CueGen returned an invalid suggestion.", "invalid_engine_output")
            start = _number(raw.get("time"), "CueGen cue position", 0, source["duration"])
            if start >= source["duration"]:
                raise CuePlannerError("CueGen placed a cue outside this XML track's duration.", "invalid_engine_output")
            slot = _integer(raw.get("slot"), "CueGen slot", -1, 7)
            if (cue_type == "memory") != (slot == -1):
                raise CuePlannerError("CueGen returned the wrong cue type.", "invalid_engine_output")
            end = raw.get("end")
            if end is not None:
                end = _number(end, "CueGen loop end", start, source["duration"])
                if end <= start:
                    raise CuePlannerError("CueGen returned an invalid loop.", "invalid_engine_output")
            name = raw.get("name", "CueGen cue")
            if (not isinstance(name, str) or len(name) > 200
                    or any(ord(char) < 32 or ord(char) == 127 for char in name)):
                raise CuePlannerError("CueGen returned an invalid cue label.", "invalid_engine_output")
            colour = raw.get("rgb")
            if colour is not None and (not isinstance(colour, (list, tuple)) or len(colour) != 3
                    or any(isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 255 for value in colour)):
                raise CuePlannerError("CueGen returned an invalid cue colour.", "invalid_engine_output")
            identity = (round(start, 6), slot, end)
            if identity in seen:
                raise CuePlannerError("CueGen returned repeated suggestions.", "invalid_engine_output")
            seen.add(identity)
            collision = any(abs(start - other) <= .001 for other in boundaries)
            if positions and minimum:
                at = _bar_coordinate(positions, start)
                collision = collision or any(abs(at - _bar_coordinate(positions, other)) < minimum - 1e-7 for other in boundaries)
            if collision or (slot >= 0 and slot in occupied):
                skipped += 1
                continue
            if len(accepted) >= maximum:
                break
            row = {"id": "external-" + str(len(accepted)), "name": name or "CueGen cue",
                   "time": round(start, 6), "slot": slot, "cue_type": cue_type,
                   "type": 4 if end is not None else 0, "source": "cuegen"}
            if end is not None:
                row["end"] = round(end, 6)
            if colour is not None:
                row["rgb"] = list(colour)
            accepted.append(row)
            boundaries.append(start)
            if end is not None:
                boundaries.append(end)
            if slot >= 0:
                occupied.add(slot)
        notes = list(warnings or []) + [
            "CueGen suggestions use existing Rekordbox phrase or Mixed In Key data. Listen to every selected marker.",
            "Existing XML cues, loops and beatgrid information are preserved. No live database changes are applied.",
            "XML can describe a loop's start and end, but cannot guarantee that Rekordbox imports its active-loop flag.",
            "Use Rekordbox's full Backup Library before importing. Import behaviour on your installed version is unverified."]
        if skipped:
            notes.append(str(skipped) + " CueGen suggestions were skipped to preserve occupied XML slots or nearby existing markers.")
        if not accepted:
            notes.append("No additional safe suggestions remain. Existing XML markers are preserved.")
        self._revalidate(source)
        if validator:
            validator()
        token, current_time = secrets.token_urlsafe(32), self.clock()
        public = {"token": token, "expires_at": (datetime.now(timezone.utc) + timedelta(seconds=PLAN_SECONDS)).isoformat(timespec="seconds"),
                  "entry_id": source["entry_id"], "track": {"title": source["track"].get("Name") or source["entry"]["title"],
                  "artist": source["track"].get("Artist", ""), "path": source["entry"]["path"], "duration_seconds": source["duration"]},
                  "engine": "cuegen", "grid_source": "rekordbox_xml", "grid": grid,
                  "existing_cues": existing, "suggestions": accepted, "warnings": notes,
                  "options": deepcopy(options), "selected_count": 0, "export_kind": "rekordbox_xml",
                  "source_name": Path(source["config"][2]).name}
        with self._lock:
            self._plans = {key: value for key, value in self._plans.items() if value["deadline"] > current_time}
            while len(self._plans) >= MAX_PLANS:
                self._plans.pop(next(iter(self._plans)))
            self._plans[token] = {**source, "deadline": current_time + PLAN_SECONDS,
                                  "public": deepcopy(public), "external_validator": validator}
        return public

    def export(self, token, selected_ids):
        if (not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{40,64}", token)
                or not isinstance(selected_ids, list) or not 1 <= len(selected_ids) <= 10
                or any(not isinstance(value, str) for value in selected_ids)
                or len(set(selected_ids)) != len(selected_ids)):
            raise CuePlannerError("Select one or more reviewed cue suggestions before exporting.", "invalid_selection", 400)
        with self._lock:
            plan = self._plans.pop(token, None)
        if plan is None or self.clock() >= plan["deadline"]:
            raise CuePlannerError("This cue review has expired or was used. Make a new plan.", "stale_plan")
        if len(selected_ids) > (10 if plan["public"].get("engine") == "cuegen" else 8):
            raise CuePlannerError("Choose no more than eight grid hot cues.", "invalid_selection", 400)
        validator = plan.get("external_validator")
        if validator:
            validator()
        choices = {row["id"]: row for row in plan["public"]["suggestions"]}
        if any(value not in choices for value in selected_ids):
            raise CuePlannerError("Choose only the suggestions from this cue plan.", "invalid_selection", 400)
        current = self._source(plan["entry_id"])
        for key in ("config", "root_identity", "xml_fingerprint", "xml_hash", "audio_fingerprint", "track_xml"):
            if current[key] != plan[key]:
                raise CuePlannerError("The XML, audio file or folder changed. Refresh and make a new cue plan.", "stale_plan")
        track = deepcopy(current["track"])
        if plan["public"]["grid_source"] == "manual":
            grid = plan["public"]["grid"][0]
            element = ET.Element("TEMPO", {"Inizio": _seconds(grid["time"]), "Bpm": _seconds(grid["bpm"]), "Metro": "4/4", "Battito": "1"})
            track.insert(0, element)
        selected = [choices[value] for value in selected_ids]
        for choice in sorted(selected, key=lambda row: (row["slot"], row["time"])):
            attributes = {"Name": choice["name"], "Type": str(choice.get("type", 0)),
                          "Start": _seconds(choice["time"]), "Num": str(choice["slot"])}
            if choice.get("end") is not None:
                attributes["End"] = _seconds(choice["end"])
            if choice.get("rgb") is not None:
                attributes.update({key: str(value) for key, value in zip(("Red", "Green", "Blue"), choice["rgb"])})
            ET.SubElement(track, "POSITION_MARK", attributes)
        document = ET.Element("DJ_PLAYLISTS", {"Version": "1.0.0"})
        ET.SubElement(document, "PRODUCT", {"Name": "DJList cue review", "Version": EXPORT_VERSION, "Company": "DJList"})
        collection = ET.SubElement(document, "COLLECTION", {"Entries": "1"})
        collection.append(track)
        playlists = ET.SubElement(document, "PLAYLISTS")
        node = ET.SubElement(playlists, "NODE", {"Type": "0", "Name": "ROOT", "Count": "1"})
        playlist = ET.SubElement(node, "NODE", {"Type": "1", "Name": "DJList cue review", "Entries": "1", "KeyType": "1"})
        ET.SubElement(playlist, "TRACK", {"Key": track.get("Location")})
        payload = ET.tostring(document, encoding="utf-8", xml_declaration=True)
        self._revalidate(current)
        if validator:
            validator()
        # Keep generated names independent of artist/title/path text and never
        # return a destination that could overwrite a source export.
        filename = "DJList-cue-review-" + plan["entry_id"][:8] + "-" + secrets.token_hex(4) + ".xml"
        return {"filename": filename, "xml": payload, "selected_count": len(selected)}
