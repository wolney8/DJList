"""Read-only Rekordbox XML inventory, local-file suggestions and duplicate review.

Only DJList's own SQLite records are written. Audio tags/headers are read through
the bounded reader; audio and Rekordbox databases are never modified. XML and
previously read tag snapshots survive missing media.
"""
from collections import defaultdict
from datetime import datetime, timezone
import difflib
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import threading
import time
import unicodedata
from urllib.parse import unquote, urlsplit
import xml.etree.ElementTree as ET

from spotify_api import AppError
from audio_metadata import READER_VERSION, file_fingerprint, read_audio_metadata


MAX_XML_BYTES = 20 * 1024 * 1024 - 1000
MAX_ENTRIES = 50000
AUDIO_EXTENSIONS = {".mp3", ".aiff", ".aif", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".alac"}
GENERIC_WORDS = {"the", "and", "with", "mix", "remix", "original", "extended", "version", "edit", "radio", "feat", "featuring"}


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def xml_fingerprint(info):
    return {"device": info.st_dev, "inode": info.st_ino, "size": info.st_size,
            "mtime_ns": info.st_mtime_ns, "ctime_ns": info.st_ctime_ns}


def _normalise(value):
    value = unicodedata.normalize("NFKD", str(value or "")).casefold()
    return " ".join(re.findall(r"[^\W_]+", "".join(c for c in value if not unicodedata.combining(c))))


def _text(value, maximum=1000):
    return " ".join(str(value or "").split())[:maximum]


def _title_parts(value):
    version = " ".join(a or b for a, b in re.findall(r"\(([^()]*)\)|\[([^\[\]]*)\]", value))
    base = re.sub(r"\([^()]*\)|\[[^\[\]]*\]", " ", value).strip()
    suffix = re.split(r"\s+[–—-]\s+", base, maxsplit=1)
    if len(suffix) == 2 and re.search(r"\b(mix|remix|dub|edit|version|remaster|live|acoustic|instrumental|radio|extended|slowed|sped|vip|rework)\b", suffix[1], re.I):
        base, version = suffix[0], (version + " " + suffix[1]).strip()
    return _normalise(base), _normalise(version)


def _duration(value, seconds=False):
    try:
        if isinstance(value, bool):
            return None
        number = float(value) * (1000 if seconds else 1)
        return round(number) if math.isfinite(number) and 0 < number <= 36_000_000 else None
    except (TypeError, ValueError, OverflowError):
        return None


def _bpm(value):
    value = _text(value, 24)
    try:
        number = float(value)
        return value if math.isfinite(number) and 0 < number <= 999 else ""
    except (TypeError, ValueError):
        return ""


def _roots(music_root):
    try:
        raw = Path(music_root).expanduser()
        if not raw.is_absolute():
            raise ValueError()
        return raw, raw.resolve(strict=False)
    except (TypeError, OSError, RuntimeError, ValueError):
        raise AppError("Choose an absolute music folder in Settings.") from None


def _relative(path, root):
    try:
        return path.relative_to(root)
    except ValueError:
        return None


def _path_info(value, music_root):
    """Inspect metadata only, without traversing symlinks below the chosen root."""
    raw_root, root = _roots(music_root)
    path = Path(value)
    relative = _relative(path, raw_root)
    if relative is None:
        relative = _relative(path, root)
    result = {"path": str(path), "available": False, "within_root": relative is not None,
              "unavailable_reason": "Outside the configured music folder", "size": 0, "mtime": 0.0,
              "fingerprint": None}
    if raw_root.is_symlink():
        result.update(within_root=False, unavailable_reason="Choose the actual music folder, not a symlink")
        return result
    if relative is None:
        return result  # Do not probe arbitrary locations from an XML file.
    candidate = root / relative
    result["path"] = str(candidate)
    result["unavailable_reason"] = "File or music drive unavailable"
    try:
        current = root
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                result["within_root"] = False
                result["unavailable_reason"] = "Symlink: choose the actual file inside the music folder"
                return result
        details = candidate.lstat()
        if stat.S_ISREG(details.st_mode) and candidate.suffix.lower() in AUDIO_EXTENSIONS:
            result.update(available=True, unavailable_reason="", size=details.st_size, mtime=details.st_mtime,
                          fingerprint={"device": details.st_dev, "inode": details.st_ino, "size": details.st_size,
                                       "mtime_ns": details.st_mtime_ns, "ctime_ns": details.st_ctime_ns})
    except (OSError, RuntimeError):
        pass
    return result


def _location(value):
    if not isinstance(value, str) or not value or len(value) > 8192:
        return None
    try:
        url = urlsplit(value)
        if url.scheme != "file" or url.netloc not in ("", "localhost") or url.query or url.fragment:
            return None
        path = unquote(url.path, errors="strict")
        if os.name == "nt" and re.match(r"^/[A-Za-z]:/", path):
            path = path[1:]
        if any(ord(c) < 32 or ord(c) == 127 for c in path):
            return None
        parsed = Path(path)
        if not parsed.is_absolute() or ".." in parsed.parts or parsed.suffix.lower() not in AUDIO_EXTENSIONS:
            return None
        return str(parsed)
    except (ValueError, UnicodeError):
        return None


def _filename_parts(path):
    stem = re.sub(r"^\d{1,3}[\s._-]+", "", Path(path).stem)
    parts = re.split(r"\s+[–—-]\s+", stem, maxsplit=1)
    return (parts[1].strip(), parts[0].strip()) if len(parts) == 2 else (stem, "")


def _identity(path):
    return hashlib.sha256(path.encode("utf-8")).hexdigest()[:24]


class LibraryInventory:
    def __init__(self, store):
        self.store = store
        self._lock = threading.RLock()
        self._cache = None
        with store.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS library_entries (
                    path TEXT PRIMARY KEY, metadata TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS library_snapshot (
                    id INTEGER PRIMARY KEY CHECK(id=1), metadata TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS media_metadata (
                    path TEXT PRIMARY KEY, root TEXT NOT NULL, metadata TEXT NOT NULL);
            """)
            if db.execute("SELECT 1 FROM library_snapshot WHERE id=1").fetchone() is None:
                # Preserve XML metadata from an installed 0.2 collection on upgrade.
                previous = list(db.execute("SELECT * FROM files WHERE origin='rekordbox_xml'"))
                if previous:
                    for row in previous:
                        entry = self._from_file(dict(row))
                        entry["source"] = "rekordbox_xml"
                        # Legacy scans may have a filename but no XML artist/title.
                        # Preserve that absence instead of relabelling inferred
                        # filename identity as verified XML metadata.
                        entry["artist"] = row["artist"] or ""
                        entry["title"] = row["title"] or Path(row["path"]).stem
                        entry["identity_source"] = "rekordbox_xml" if row["artist"] and row["title"] else "filename"
                        db.execute("INSERT OR IGNORE INTO library_entries VALUES (?,?)", (entry["path"], json.dumps(entry)))
                    snapshot = {"source_name": "Previously imported XML", "source_path": "", "refreshed_at": None,
                                "entry_count": len(previous), "skipped": 0, "duplicate_locations": 0}
                    db.execute("INSERT INTO library_snapshot VALUES (1,?)", (json.dumps(snapshot),))

    def invalidate(self):
        with self._lock:
            self._cache = None

    @staticmethod
    def _from_file(row, metadata=None):
        inferred_title, inferred_artist = _filename_parts(row["path"])
        # Hydrated tag values are usable only with their matching fingerprint.
        # Otherwise an edited/replaced file must fall back to its filename.
        row_identity = row.get("origin") != "audio_tags"
        title = (row.get("title") if row_identity else "") or inferred_title
        artist = (row.get("artist") if row_identity else "") or inferred_artist
        if not row.get("artist") and _normalise(title) == _normalise(Path(row["path"]).stem):
            title = inferred_title
        entry = {"id": _identity(row["path"]), "path": row["path"], "name": Path(row["path"]).name,
                 "title": title, "artist": artist, "album": "",
                 "key": row.get("musical_key", "") if row_identity else "",
                 "bpm": row.get("bpm", "") if row_identity else "", "genre": "",
                 "duration_ms": None, "cue_count": row.get("cue_count", 0), "source": "scan",
                 "identity_source": "filename", "metadata_source": "", "metadata_status": "not_read",
                 "metadata_message": "Audio tags have not been read yet.", "mime_type": "", "isrc": "", "version_notes": ""}
        if metadata:
            entry.update(metadata_status=metadata.get("status", "error"), metadata_message=metadata.get("message", ""))
            if metadata.get("status") in {"read", "offline"}:
                entry.update(title=metadata.get("title") or inferred_title,
                             artist=metadata.get("artist") or inferred_artist, album=metadata.get("album", ""),
                             genre=metadata.get("genre", ""), key=metadata.get("musical_key", ""),
                             bpm=metadata.get("bpm", ""), duration_ms=metadata.get("duration_ms"),
                             isrc=metadata.get("isrc", ""), mime_type=metadata.get("mime_type", ""),
                             version_notes=metadata.get("version_notes", ""), metadata_source="audio_tags")
                if metadata.get("title") and metadata.get("artist"):
                    entry["identity_source"] = "audio_tags"
        return entry

    def cached_audio_metadata(self, path, music_root):
        """Reuse successful/definitive tag reads only while every stat field agrees."""
        info = _path_info(str(path), music_root)
        fingerprint = file_fingerprint(info["path"], music_root)
        if fingerprint is None:
            return None
        with self.store.connect() as db:
            row = db.execute("SELECT metadata FROM media_metadata WHERE path=? AND root=?",
                             (info["path"], str(_roots(music_root)[1]))).fetchone()
        if row:
            metadata = json.loads(row[0])
            # Parser errors can hide transient drive I/O failures. Keep their
            # warnings in inventory, but retry them on the next scan.
            if (metadata.get("reader") == READER_VERSION and metadata.get("fingerprint") == fingerprint
                    and metadata.get("status") in {"read", "empty", "unsupported"}):
                return metadata
        return None

    def store_scan_metadata(self, results, music_root, complete=False):
        """Save per-file read results in DJList only; results map paths to metadata."""
        root = str(_roots(music_root)[1])
        stored = set()
        with self._lock, self.store.connect() as db:
            for path, metadata in results.items():
                info = _path_info(str(path), music_root)
                if not info["within_root"] or not isinstance(metadata, dict):
                    continue
                stored.add(info["path"])
                db.execute("INSERT OR REPLACE INTO media_metadata VALUES (?,?,?)",
                           (info["path"], root, json.dumps(metadata)))
            if complete:
                for row in db.execute("SELECT path FROM media_metadata WHERE root=?", (root,)).fetchall():
                    if row[0] not in stored:
                        db.execute("DELETE FROM media_metadata WHERE path=?", (row[0],))
            self._cache = None

    def _snapshot(self):
        with self.store.connect() as db:
            row = db.execute("SELECT metadata FROM library_snapshot WHERE id=1").fetchone()
        return json.loads(row[0]) if row else {"source_name": "", "source_path": "", "refreshed_at": None,
                                             "entry_count": 0, "skipped": 0, "duplicate_locations": 0}

    def _entries(self, music_root):
        key = str(_roots(music_root)[1])
        with self._lock:
            if self._cache and self._cache[0] == key and self._cache[1] > time.monotonic():
                return [dict(row) for row in self._cache[2]]
            with self.store.connect() as db:
                xml_rows = [json.loads(row[0]) for row in db.execute("SELECT metadata FROM library_entries ORDER BY path")]
                files = [dict(row) for row in db.execute("SELECT * FROM files WHERE root=? ORDER BY path", (key,))]
                metadata_rows = {row["path"]: json.loads(row["metadata"]) for row in db.execute(
                    "SELECT path,metadata FROM media_metadata WHERE root=?", (key,))}
            entries = {row["path"]: row for row in xml_rows}
            for row in files:
                if row["path"] in entries:
                    continue  # The selected Rekordbox XML remains the stronger source.
                info = _path_info(row["path"], music_root)
                metadata = metadata_rows.get(row["path"])
                if metadata and metadata.get("status") == "read":
                    if info["available"] and metadata.get("fingerprint") != info["fingerprint"]:
                        metadata = {"status": "stale", "message": "This file changed since its tags were read. Refresh media to read it again."}
                    elif not info["available"]:
                        metadata = {**metadata, "status": "offline", "message": "Previously read audio tags; this file is currently unavailable."}
                entries[row["path"]] = self._from_file(row, metadata)
            result = []
            for row in entries.values():
                info = _path_info(row["path"], music_root)
                result.append({**row, **{name: info[name] for name in ("available", "within_root", "unavailable_reason")}})
            result.sort(key=lambda row: (_normalise(row["artist"]), _normalise(row["title"]), row["path"]))
            self._cache = (key, time.monotonic() + 10, result)
            return [dict(row) for row in result]

    def import_xml(self, xml, music_root, source_name="Uploaded XML"):
        return self._import_xml(xml, music_root, _text(source_name, 255) or "Uploaded XML", "")

    def refresh_xml(self, xml_path, music_root):
        if not isinstance(xml_path, str) or not xml_path or len(xml_path) > 4096 or any(ord(c) < 32 for c in xml_path):
            raise AppError("Set the Rekordbox XML file path in Settings before refreshing it.")
        path = Path(xml_path).expanduser()
        if not path.is_absolute() or path.suffix.lower() != ".xml" or path.is_symlink():
            raise AppError("Choose an absolute path to the actual Rekordbox .xml export.")
        try:
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
            with os.fdopen(os.open(str(path), flags), "rb") as file:
                before = os.fstat(file.fileno())
                if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_XML_BYTES:
                    raise AppError("Choose a Rekordbox XML export smaller than 20 MB.", 413)
                payload = file.read(MAX_XML_BYTES + 1)
                after = os.fstat(file.fileno())
            if len(payload) > MAX_XML_BYTES:
                raise AppError("Choose a Rekordbox XML export smaller than 20 MB.", 413)
            fingerprint = xml_fingerprint(before)
            if fingerprint != xml_fingerprint(after) or fingerprint != xml_fingerprint(path.lstat()):
                raise AppError("The XML export changed while it was being read. Finish exporting and refresh again.", 409)
            xml = payload.decode("utf-8-sig")
        except (OSError, UnicodeError):
            raise AppError("The Rekordbox XML export could not be read. Check its saved path and reconnect its drive.", 409) from None
        return self._import_xml(xml, music_root, path.name, str(path), source_fingerprint=fingerprint)

    def _import_xml(self, xml, music_root, source_name, source_path, source_fingerprint=None):
        try:
            size = len(xml.encode("utf-8")) if isinstance(xml, str) else MAX_XML_BYTES + 1
        except UnicodeError:
            raise AppError("The selected XML contains invalid text.") from None
        if size > MAX_XML_BYTES:
            raise AppError("Choose a Rekordbox XML export smaller than 20 MB.", 413)
        if "<!DOCTYPE" in xml.upper() or "<!ENTITY" in xml.upper():
            raise AppError("This XML contains unsupported declarations.")
        try:
            document = ET.fromstring(xml)
        except (ET.ParseError, ValueError):
            raise AppError("The selected file is not valid XML.") from None
        collection = document.find("COLLECTION")
        if document.tag != "DJ_PLAYLISTS" or collection is None:
            raise AppError("Choose a Rekordbox XML collection export.")
        items = collection.findall("TRACK")
        if len(items) > MAX_ENTRIES:
            raise AppError("This XML contains more than 50,000 tracks. Export a smaller collection.", 413)
        declared = collection.get("Entries")
        if declared is not None and (not declared.isdigit() or int(declared) != len(items)):
            raise AppError("The XML collection count does not match its contents. Export it again.")
        raw_root, root = _roots(music_root)
        entries, hydrated, skipped, duplicate_locations = {}, [], 0, 0
        for item in items:
            path = _location(item.get("Location"))
            if path is None:
                skipped += 1
                continue
            info = _path_info(path, str(raw_root))
            path = info["path"]
            if path in entries:
                duplicate_locations += 1
                continue
            entry = {"id": _identity(path), "path": path, "name": Path(path).name,
                     "title": _text(item.get("Name")) or Path(path).stem,
                     "artist": _text(item.get("Artist")), "album": _text(item.get("Album")),
                     "key": _text(item.get("Tonality"), 128), "bpm": _bpm(item.get("AverageBpm")),
                     "genre": _text(item.get("Genre"), 200), "duration_ms": _duration(item.get("TotalTime"), seconds=True),
                     "cue_count": len(item.findall("POSITION_MARK")), "source": "rekordbox_xml",
                     "identity_source": "rekordbox_xml" if _text(item.get("Name")) and _text(item.get("Artist")) else "filename"}
            entries[path] = entry
            if info["available"] and info["within_root"]:
                hydrated.append((entry, info))
        if items and not entries:
            raise AppError("This XML contains no usable local audio paths. The previous inventory was kept.")
        snapshot = {"source_name": source_name, "source_path": source_path, "refreshed_at": _now(),
                    "entry_count": len(entries), "skipped": skipped, "duplicate_locations": duplicate_locations,
                    "source_fingerprint": source_fingerprint}
        with self._lock, self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = {row[0] for row in db.execute("SELECT path FROM library_entries")}
            db.execute("DELETE FROM library_entries")
            db.executemany("INSERT INTO library_entries VALUES (?,?)", [(path, json.dumps(entry)) for path, entry in entries.items()])
            db.execute("INSERT OR REPLACE INTO library_snapshot VALUES (1,?)", (json.dumps(snapshot),))
            for path in previous - set(entries):
                db.execute("UPDATE files SET title=?,artist='',musical_key='',bpm='',cue_count=0,origin='scan' WHERE path=? AND origin='rekordbox_xml'",
                           (Path(path).stem, path))
            for entry, info in hydrated:
                self._hydrate(db, entry, info, str(root))
            self._cache = None
        return self.status(music_root)

    @staticmethod
    def _hydrate(db, entry, info, root):
        db.execute("""INSERT INTO files(path,root,size,mtime,title,artist,musical_key,bpm,cue_count,origin)
                      VALUES (?,?,?,?,?,?,?,?,?,'rekordbox_xml') ON CONFLICT(path) DO UPDATE SET
                      root=excluded.root,size=excluded.size,mtime=excluded.mtime,title=excluded.title,
                      artist=excluded.artist,musical_key=excluded.musical_key,bpm=excluded.bpm,
                      cue_count=excluded.cue_count,origin='rekordbox_xml'""",
                   (entry["path"], root, info["size"], info["mtime"], entry["title"], entry["artist"],
                    entry["key"], entry["bpm"], entry["cue_count"]))

    def prepare_link(self, path, music_root):
        info = _path_info(str(path), music_root)
        if not info["available"] or not info["within_root"]:
            raise AppError("That audio file is unavailable or outside your music folder.", 409)
        with self.store.connect() as db:
            row = db.execute("SELECT metadata FROM library_entries WHERE path=?", (info["path"],)).fetchone()
        metadata = None
        if not row:
            metadata = self.cached_audio_metadata(info["path"], music_root)
            if metadata is None:
                metadata = read_audio_metadata(info["path"], music_root)
                if metadata["status"] == "changed":
                    raise AppError("That audio file changed while tags were read. Wait for the download or edit to finish, then review it again.", 409)
                self.store_scan_metadata({info["path"]: metadata}, music_root)
            current = file_fingerprint(info["path"], music_root)
            if current is None or current != info["fingerprint"]:
                raise AppError("That audio file changed or became unavailable. Refresh media and review the link again.", 409)
        with self._lock, self.store.connect() as db:
            if row:
                self._hydrate(db, json.loads(row[0]), info, str(_roots(music_root)[1]))
            elif metadata and metadata.get("status") == "read":
                db.execute("""INSERT INTO files(path,root,size,mtime,title,artist,musical_key,bpm,cue_count,origin)
                              VALUES (?,?,?,?,?,?,?,?,0,'audio_tags') ON CONFLICT(path) DO UPDATE SET
                              root=excluded.root,size=excluded.size,mtime=excluded.mtime,title=excluded.title,
                              artist=excluded.artist,musical_key=excluded.musical_key,bpm=excluded.bpm,
                              cue_count=0,origin='audio_tags'""",
                           (info["path"], str(_roots(music_root)[1]), info["size"], info["mtime"],
                            metadata.get("title", ""), metadata.get("artist", ""),
                            metadata.get("musical_key", ""), metadata.get("bpm", "")))
            else:
                # Clear any older hydrated tags if the current file cannot supply them.
                db.execute("UPDATE files SET title=?,artist='',musical_key='',bpm='',cue_count=0,origin='scan' "
                           "WHERE path=? AND origin='audio_tags'", (Path(info["path"]).stem, info["path"]))
            self._cache = None

    @staticmethod
    def _duplicate_groups(entries):
        groups = defaultdict(list)
        for entry in entries:
            if entry["artist"] and entry["title"]:
                groups[(_normalise(entry["artist"]), _normalise(entry["title"]))].append(entry)
        duplicates = []
        for members in groups.values():
            if len(members) < 2:
                continue
            known = sorted((row for row in members if row["duration_ms"]), key=lambda row: row["duration_ms"])
            unknown = [row for row in members if not row["duration_ms"]]
            clusters = []
            for row in known:
                if not clusters or row["duration_ms"] - clusters[-1][0]["duration_ms"] > 2000:
                    clusters.append([])
                clusters[-1].append(row)
            if len(clusters) == 1:
                clusters[0].extend(unknown)
            elif unknown:
                clusters.append(unknown)
            for cluster in clusters:
                if len(cluster) < 2:
                    continue
                first = cluster[0]
                reason = "Artist and full title/version match; compare the files before deciding."
                if any(not row["duration_ms"] for row in cluster):
                    reason += " Some durations are unavailable."
                else:
                    reason += " Durations are within 2 seconds."
                duplicates.append({"title": first["title"], "artist": first["artist"], "count": len(cluster),
                                   "paths": [row["path"] for row in cluster], "reason": reason})
        return sorted(duplicates, key=lambda group: (-group["count"], _normalise(group["artist"]), _normalise(group["title"])))

    def status(self, music_root):
        entries = self._entries(music_root)
        snapshot = self._snapshot()
        # XML metadata overlays scan rows in review, but both describe the same
        # indexed media file. Count that file once, regardless of metadata source.
        with self.store.connect() as db:
            media_count = db.execute("SELECT COUNT(*) FROM files WHERE root=?", (str(_roots(music_root)[1]),)).fetchone()[0]
        return {**snapshot, "indexed": snapshot["entry_count"], "total": len(entries),
                "available": sum(row["available"] for row in entries),
                "missing": sum(row["within_root"] and not row["available"] for row in entries),
                "outside_root": sum(not row["within_root"] for row in entries),
                "scan_count": media_count,
                "audio_tag_count": sum(row.get("identity_source") == "audio_tags" for row in entries),
                "metadata_warning_count": sum(row.get("metadata_status") in {"error", "limited", "changed", "stale"} for row in entries),
                "duplicate_groups_count": len(self._duplicate_groups(entries))}

    @staticmethod
    def _candidate(track, entry):
        title = track.get("title_and_version", "") or track.get("title", "")
        artist = track.get("artists", "") or track.get("artist", "")
        primary = _normalise(artist.split(";", 1)[0])
        local_path = track.get("local_file")
        if local_path and str(Path(local_path)) == entry["path"]:
            status, score, reasons = "linked", 100, ["This file is already linked to the track."]
        else:
            expected_base, expected_version = _title_parts(title)
            base, version = _title_parts(entry["title"])
            entry_artist = _normalise(entry["artist"].split(";", 1)[0])
            if entry_artist and entry_artist != primary:
                return None
            versions_equal = expected_version == version
            unlabelled_original = {expected_version, version} <= {"", "original", "original mix", "original version"}
            if not versions_equal and not unlabelled_original:
                return None
            if not entry_artist:
                filename = _normalise(re.sub(r"^\d{1,3}[\s._-]+", "", Path(entry["path"]).stem))
                if not primary or filename != _normalise(artist.split(";", 1)[0] + " " + title):
                    return None
                status, score, reasons = "filename_match", 75, ["Artist and full title occur in the filename; embedded identity is unverified."]
            else:
                similarity = difflib.SequenceMatcher(None, expected_base, base).ratio()
                if not expected_base or similarity < .84:
                    return None
                exact = expected_base == base and versions_equal
                status, score = ("likely_match", 90) if exact else ("possible_match", round(similarity * 80))
                reasons = ["Artist and full title/version match." if exact else "Artist matches; compare the title and version wording."]
            duration = _duration(track.get("duration_ms"))
            if duration and entry["duration_ms"]:
                difference = abs(duration - entry["duration_ms"])
                if difference > 2000:
                    status, score = "check_duration", min(score, 65)
                    reasons.append("Duration differs by about %d seconds." % round(difference / 1000))
                else:
                    score = min(98, score + 5)
                    reasons.append("Durations are within 2 seconds.")
            else:
                reasons.append("Duration comparison is unavailable.")
            if entry.get("identity_source", "filename" if entry["source"] == "scan" else "rekordbox_xml") == "filename":
                reasons.insert(0, "Artist/title identity was inferred from the filename; complete embedded identity is unavailable.")
                if status == "likely_match":
                    status, score = "filename_match", min(score, 80)
            elif entry.get("identity_source") == "audio_tags":
                reasons.insert(0, "Artist and title were read from the audio file's tags; tag accuracy still needs your confirmation.")
            if entry.get("version_notes"):
                status, score = "check_version", min(score, 65)
                reasons.append("Additional version/subtitle tags require review: " + entry["version_notes"])
            reasons.append("Confirm the recording before linking it or buying another copy.")
        if not entry["available"]:
            reasons.append(entry["unavailable_reason"] + ".")
        return {key: entry[key] for key in ("path", "name", "title", "artist", "album", "key", "bpm", "genre", "duration_ms", "cue_count", "available", "within_root", "source")} | {
            "score": score, "match_status": status, "reasons": reasons,
            "identity_source": entry.get("identity_source", "filename" if entry["source"] == "scan" else "rekordbox_xml"),
            **{key: entry.get(key, "") for key in ("metadata_status", "metadata_message", "metadata_source", "mime_type", "isrc", "version_notes")}}

    def _matches(self, tracks, entries):
        bases, tokens, artists, paths = defaultdict(set), defaultdict(set), defaultdict(set), {}
        for index, entry in enumerate(entries):
            base = _title_parts(entry["title"])[0]
            bases[base].add(index)
            for token in set(base.split()) - GENERIC_WORDS:
                if len(token) > 1:
                    tokens[token].add(index)
            artists[_normalise(entry["artist"].split(";", 1)[0])].add(index)
            paths[entry["path"]] = index
        result = []
        for track in tracks:
            title = track.get("title_and_version", "") or track.get("title", "")
            artist_credit = track.get("artists", "") or track.get("artist", "")
            primary = _normalise(artist_credit.split(";", 1)[0])
            base = _title_parts(title)[0]
            exact = bases.get(base, set())
            indices = set(exact)
            for token in set(base.split()) - GENERIC_WORDS:
                indices.update(tokens.get(token, set()))
            if primary:
                indices.intersection_update(artists.get(primary, set()) | artists.get("", set()))
            if track.get("local_file") in paths:
                indices.add(paths[track["local_file"]])
            ordered = sorted(indices, key=lambda index: (index not in exact, index))[:500]
            candidates = [value for index in ordered if (value := self._candidate(track, entries[index])) is not None]
            if candidates:
                result.append({"track_id": track["id"], "candidates": sorted(candidates, key=lambda row: (-row["score"], not row["available"], row["path"]))[:5]})
        return result

    def matches(self, tracks, music_root):
        return self._matches(tracks, self._entries(music_root))

    def review(self, tracks, music_root, query="", page=1, page_size=25, filter_name="all"):
        if not isinstance(query, str) or len(query) > 500 or any(ord(c) < 32 for c in query):
            raise AppError("Use a library search of 500 characters or fewer.")
        if isinstance(page, bool) or not isinstance(page, int) or page < 1:
            raise AppError("Choose a valid library page.")
        if isinstance(page_size, bool) or not isinstance(page_size, int) or not 1 <= page_size <= 100:
            raise AppError("Choose a library page size from 1 to 100.")
        if filter_name not in {"all", "missing", "duplicates"}:
            raise AppError("Choose a valid library filter.")
        entries = self._entries(music_root)
        groups = self._duplicate_groups(entries)
        duplicate_paths = {path for group in groups for path in group["paths"]}
        linked = defaultdict(list)
        for track in tracks:
            if track.get("local_file"):
                linked[track["local_file"]].append(track["id"])
        query = _normalise(query)
        filtered = []
        for row in entries:
            row.update(duplicate=row["path"] in duplicate_paths, linked_track_ids=linked.get(row["path"], []))
            if filter_name == "duplicates" and not row["duplicate"]:
                continue
            if filter_name == "missing" and (row["available"] or not row["within_root"]):
                continue
            if query and query not in _normalise(" ".join(str(row[key] or "") for key in ("title", "artist", "album", "genre", "key", "bpm", "path"))):
                continue
            filtered.append(row)
        pages = max(1, math.ceil(len(filtered) / page_size))
        page = min(page, pages)
        start = (page - 1) * page_size
        return {"entries": filtered[start:start + page_size], "total": len(filtered), "page": page,
                "page_size": page_size, "pages": pages, "summary": self.status(music_root),
                "duplicate_groups": [{**group, "paths": group["paths"][:20], "paths_truncated": len(group["paths"]) > 20}
                                     for group in groups[:50]], "duplicate_groups_truncated": len(groups) > 50,
                "matches": self._matches(tracks, entries)}
