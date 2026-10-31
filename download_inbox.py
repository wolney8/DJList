"""Review purchased downloads and explicitly link selected pairs in DJList.

Only DJList's own annotations are written. The configured download folder is
an explicit read boundary; it may be separate from the main music folder. Audio
files are never moved, renamed or changed, and Rekordbox is never opened here.
"""
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat
import threading
import time

from library_inventory import AUDIO_EXTENSIONS, GENERIC_WORDS, _filename_parts, _normalise, _title_parts
from spotify_api import AppError


BATCH_LIMIT = 500
SCAN_LIMIT = 5000
MAX_WALK_ENTRIES = 50000
MAX_DEPTH = 64
MAX_TRACKS = 50000
MAX_CANDIDATES = 10
PLAN_SECONDS = 600
PREVIEW_SECONDS = 20


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _fingerprint(info):
    return {"device": info.st_dev, "inode": info.st_ino, "size": info.st_size,
            "mtime_ns": getattr(info, "st_mtime_ns", int(info.st_mtime * 1_000_000_000)),
            "ctime_ns": getattr(info, "st_ctime_ns", int(info.st_ctime * 1_000_000_000))}


def _root(value):
    if (not isinstance(value, str) or not value or len(value) > 4096
            or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise AppError("Choose your download folder in Settings before opening the inbox.", 409)
    try:
        raw = Path(value).expanduser()
        if not raw.is_absolute() or ".." in raw.parts or raw == Path(raw.anchor):
            raise AppError("Choose a specific absolute download folder in Settings.", 409)
        if raw.is_symlink():
            raise AppError("Choose the actual download folder, rather than a folder symlink.", 409)
        root = raw.resolve(strict=True)
        info = root.stat()
        if not stat.S_ISDIR(info.st_mode):
            raise OSError()
        return raw, root, (info.st_dev, info.st_ino)
    except (OSError, RuntimeError, TypeError, ValueError, UnicodeError):
        raise AppError("Your download folder is unavailable. Connect its drive and check Settings.", 409) from None


def _supported():
    if not (hasattr(os, "O_NOFOLLOW") and hasattr(os, "O_DIRECTORY")
            and os.open in os.supports_dir_fd and os.stat in os.supports_dir_fd
            and os.scandir in os.supports_fd):
        raise AppError("The download inbox needs the local macOS or Linux app.", 409)


def _snapshot(root, relative, identity):
    """Inspect a regular audio file without following any child symlinks."""
    if (relative.is_absolute() or not relative.parts or ".." in relative.parts
            or relative.suffix.lower() not in AUDIO_EXTENSIONS):
        return None
    first = current = None
    try:
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        first = current = os.open(str(root), flags)
        if (os.fstat(first).st_dev, os.fstat(first).st_ino) != identity:
            return None
        for part in relative.parts[:-1]:
            next_fd = os.open(part, flags, dir_fd=current)
            if current != first:
                os.close(current)
            current = next_fd
        info = os.stat(relative.name, dir_fd=current, follow_symlinks=False)
        return _fingerprint(info) if stat.S_ISREG(info.st_mode) else None
    except (OSError, RuntimeError, ValueError, UnicodeError):
        return None
    finally:
        if current is not None and current != first:
            os.close(current)
        if first is not None:
            os.close(first)


def _scan(root, identity):
    """Bounded descriptor-based traversal; never follow an untrusted directory."""
    files, skipped, visited, truncated = [], 0, 0, False
    stack = []
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        descriptor = os.open(str(root), flags)
        info = os.fstat(descriptor)
        if (info.st_dev, info.st_ino) != identity:
            os.close(descriptor)
            raise AppError("The download folder changed. Open a fresh inbox preview.", 409)
        try:
            iterator = os.scandir(descriptor)
        except BaseException:
            os.close(descriptor)
            raise
        stack.append((descriptor, iterator, Path()))
        while stack:
            current, iterator, relative = stack[-1]
            try:
                item = next(iterator)
            except StopIteration:
                iterator.close()
                os.close(current)
                stack.pop()
                continue
            except OSError:
                skipped += 1
                iterator.close()
                os.close(current)
                stack.pop()
                continue
            visited += 1
            if visited > MAX_WALK_ENTRIES:
                truncated = True
                break
            child = relative / item.name
            try:
                details = os.stat(item.name, dir_fd=current, follow_symlinks=False)
                if stat.S_ISLNK(details.st_mode):
                    skipped += 1
                    continue
                if stat.S_ISDIR(details.st_mode):
                    if len(child.parts) > MAX_DEPTH:
                        skipped += 1
                        truncated = True
                        continue
                    next_fd = os.open(item.name, flags, dir_fd=current)
                    try:
                        opened = os.fstat(next_fd)
                        if (opened.st_dev, opened.st_ino) != (details.st_dev, details.st_ino):
                            os.close(next_fd)
                            skipped += 1
                            continue
                        next_iterator = os.scandir(next_fd)
                    except BaseException:
                        os.close(next_fd)
                        raise
                    stack.append((next_fd, next_iterator, child))
                elif child.suffix.lower() in AUDIO_EXTENSIONS:
                    if not stat.S_ISREG(details.st_mode):
                        skipped += 1
                        continue
                    if len(files) >= SCAN_LIMIT:
                        truncated = True
                        break
                    files.append({"relative": child, "fingerprint": _fingerprint(details)})
            except (OSError, RuntimeError, ValueError, UnicodeError):
                skipped += 1
    except (OSError, RuntimeError, ValueError, UnicodeError):
        raise AppError("The download folder could not be read. Check its permissions and try again.", 409) from None
    finally:
        for descriptor, iterator, _ in reversed(stack):
            iterator.close()
            os.close(descriptor)
    return files, skipped, truncated


def _canonical_link(value, raw, root):
    """Normalize configured-root aliases lexically, without probing other paths."""
    if not isinstance(value, str) or not value:
        return ""
    path = Path(value)
    try:
        return str(root / path.relative_to(raw))
    except ValueError:
        return str(path)


def _clean_text(value, limit=1000):
    return " ".join(str(value or "").split())[:limit]


class DownloadInbox:
    def __init__(self, store, library, metadata_reader=None, clock=time.monotonic):
        self.store = store
        self.library = library
        if metadata_reader is None:
            from audio_metadata import read_audio_metadata
            metadata_reader = read_audio_metadata
        self.metadata_reader = metadata_reader
        self.clock = clock
        self.lock = threading.RLock()
        self.plan = None

    def clear(self):
        with self.lock:
            self.plan = None

    @staticmethod
    def _settings(db):
        return {row["key"]: json.loads(row["value"]) for row in db.execute(
            "SELECT key,value FROM settings WHERE key IN ('mode','download_root')")}

    @staticmethod
    def _real(settings):
        if settings.get("mode") != "real":
            raise AppError("Leave the demo before reviewing or linking real downloads.", 409)

    def preview(self):
        _supported()
        with self.lock:
            self.plan = None
            started = self.clock()
            with self.store.connect() as db:
                db.execute("BEGIN")
                settings = self._settings(db)
                self._real(settings)
                records = list(db.execute("SELECT id,metadata,annotations FROM tracks WHERE namespace='real' ORDER BY id LIMIT ?", (MAX_TRACKS + 1,)))
            if len(records) > MAX_TRACKS:
                raise AppError("Download matching supports up to 50,000 tracks at a time.", 413)
            raw, root, identity = _root(settings.get("download_root", ""))
            tracks, linked, by_token, by_artist, by_base = {}, defaultdict(list), defaultdict(set), defaultdict(set), defaultdict(set)
            for row in records:
                annotations = json.loads(row["annotations"])
                metadata = json.loads(row["metadata"])
                track = {**metadata, "id": row["id"], "local_file": annotations.get("local_file", "")}
                tracks[row["id"]] = {"track": track, "metadata": metadata}
                if track["local_file"]:
                    linked[_canonical_link(track["local_file"], raw, root)].append(row["id"])
                title = track.get("title_and_version", "") or track.get("title", "")
                artist = track.get("artists", "") or track.get("artist", "")
                by_artist[_normalise(artist.split(";", 1)[0])].add(row["id"])
                base = _title_parts(title)[0]
                by_base[base].add(row["id"])
                for token in set(base.split()) - GENERIC_WORDS:
                    if len(token) > 1:
                        by_token[token].add(row["id"])
            files, skipped, truncated_scan = _scan(root, identity)
            files.sort(key=lambda item: (bool(linked.get(str(root / item["relative"]))),
                                         -item["fingerprint"]["mtime_ns"], str(item["relative"])))
            entries, stored, timed_out = [], {}, False
            for item in files[:BATCH_LIMIT]:
                if self.clock() - started >= PREVIEW_SECONDS:
                    timed_out = True
                    break
                relative, before = item["relative"], item["fingerprint"]
                path = root / relative
                current = _snapshot(root, relative, identity)
                if current != before:
                    skipped += 1
                    continue
                try:
                    metadata = self.metadata_reader(path, root)
                    if not isinstance(metadata, dict):
                        metadata = {"status": "error", "message": "File metadata could not be read."}
                except (OSError, ValueError, RuntimeError, AppError):
                    metadata = {"status": "error", "message": "File metadata could not be read; check the recording yourself."}
                after = _snapshot(root, relative, identity)
                available = (after == before and metadata.get("status") not in {"unavailable", "changed"}
                             and (not metadata.get("fingerprint") or metadata["fingerprint"] == before))
                title, artist = _filename_parts(path)
                has_identity = bool(metadata.get("title") and metadata.get("artist"))
                any_tags = any(metadata.get(key) for key in ("title", "artist", "album", "genre", "musical_key", "bpm"))
                entry_id = hashlib.sha256(str(path).encode("utf-8", errors="surrogatepass")).hexdigest()[:24]
                entry = {"id": entry_id, "path": str(path), "name": path.name,
                         "title": _clean_text(metadata.get("title") or title),
                         "artist": _clean_text(metadata.get("artist") or artist),
                         "album": _clean_text(metadata.get("album")), "genre": _clean_text(metadata.get("genre")),
                         "version_notes": _clean_text(metadata.get("version_notes")),
                         "bpm": _clean_text(metadata.get("bpm"), 24), "key": _clean_text(metadata.get("musical_key"), 100),
                         "duration_ms": metadata.get("duration_ms"),
                         "metadata_source": "file_tags" if any_tags else "filename",
                         "metadata_status": metadata.get("status", "error") if available else "changed",
                         "message": _clean_text(metadata.get("message")) if available else "This file changed or became unavailable. Make a fresh preview.",
                         "available": available, "linked_track_ids": linked.get(str(path), []), "candidates": []}
                synthetic = {**entry, "source": "audio_tags" if has_identity else "scan",
                             "identity_source": "audio_tags" if has_identity else "filename", "within_root": True,
                             "cue_count": 0, "unavailable_reason": "File currently unavailable" if not available else ""}
                base = _title_parts(entry["title"])[0]
                ids = set(by_base.get(base, ()))
                for token in set(base.split()) - GENERIC_WORDS:
                    ids.update(by_token.get(token, ()))
                if entry["artist"]:
                    ids.intersection_update(by_artist.get(_normalise(entry["artist"].split(";", 1)[0]), ()))
                candidates = []
                for track_id in sorted(ids)[:500]:
                    track = tracks[track_id]["track"]
                    match = self.library._candidate(track, synthetic)
                    if match is not None:
                        candidates.append({"track_id": track_id, "title": track.get("title_and_version", "") or track.get("title", ""),
                                           "artists": track.get("artists", "") or track.get("artist", ""),
                                           "score": match["score"], "match_status": match["match_status"],
                                           "reasons": match["reasons"], "existing_file": track["local_file"]})
                entry["candidates"] = sorted(candidates, key=lambda match: (bool(match["existing_file"]), -match["score"], match["track_id"]))[:MAX_CANDIDATES]
                stored[entry_id] = {"entry": entry, "relative": relative, "fingerprint": before,
                                    "tracks": {match["track_id"]: tracks[match["track_id"]] for match in entry["candidates"]}}
                entries.append(entry)
            if _root(settings["download_root"])[1:] != (root, identity):
                raise AppError("The download folder changed during scanning. Make a fresh preview.", 409)
            token = secrets.token_urlsafe(32)
            expires_at = (datetime.now(timezone.utc) + timedelta(seconds=PLAN_SECONDS)).isoformat(timespec="seconds")
            self.plan = {"token": token, "deadline": self.clock() + PLAN_SECONDS, "setting": settings["download_root"],
                         "root": root, "root_identity": identity, "entries": stored}
            return {"token": token, "expires_at": expires_at, "root": str(root), "entries": entries,
                    "total": len(files), "limit": BATCH_LIMIT, "truncated": truncated_scan or timed_out or len(files) > BATCH_LIMIT,
                    "skipped": skipped, "checked_at": _now()}

    def apply(self, token, selections):
        if not isinstance(token, str) or len(token) > 128 or not token.isascii():
            raise AppError("Make a fresh download-inbox preview before linking.", 409)
        with self.lock:
            plan = self.plan
            if plan is None or not secrets.compare_digest(token.encode("ascii"), plan["token"].encode("ascii")):
                raise AppError("This preview is no longer available. Make a fresh preview before linking.", 409)
            self.plan = None
            if self.clock() >= plan["deadline"]:
                raise AppError("This preview expired. Make a fresh preview before linking.", 409)
            if not isinstance(selections, list) or not 1 <= len(selections) <= BATCH_LIMIT:
                raise AppError("Select between 1 and 500 file/track pairs from a fresh preview.")
            chosen, entry_ids, track_ids = [], set(), set()
            for pair in selections:
                if (not isinstance(pair, dict) or set(pair) != {"entry_id", "track_id"}
                        or not isinstance(pair["entry_id"], str) or len(pair["entry_id"]) > 128
                        or not isinstance(pair["track_id"], int) or isinstance(pair["track_id"], bool)
                        or pair["track_id"] < 1):
                    raise AppError("Choose a valid file/track pair from this preview.")
                entry_id, track_id = pair["entry_id"], pair["track_id"]
                if entry_id in entry_ids or track_id in track_ids:
                    raise AppError("Select each file and each track only once.")
                item = plan["entries"].get(entry_id)
                if (item is None or track_id not in item["tracks"] or not item["entry"]["available"]
                        or item["entry"]["linked_track_ids"] or item["tracks"][track_id]["track"]["local_file"]):
                    raise AppError("Only available, unlinked files and unlinked suggested tracks can be selected.")
                chosen.append((item, track_id))
                entry_ids.add(entry_id)
                track_ids.add(track_id)
            with self.store.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                settings = self._settings(db)
                self._real(settings)
                if settings.get("download_root") != plan["setting"]:
                    raise AppError("The download folder changed. Make a fresh preview before linking.", 409)
                raw, root, identity = _root(settings["download_root"])
                if root != plan["root"] or identity != plan["root_identity"]:
                    raise AppError("The download folder changed. Make a fresh preview before linking.", 409)
                linked_paths = {_canonical_link(json.loads(row["annotations"]).get("local_file", ""), raw, root)
                                for row in db.execute("SELECT annotations FROM tracks WHERE namespace='real'")}
                changes = []
                for item, track_id in chosen:
                    entry = item["entry"]
                    row = db.execute("SELECT metadata,annotations FROM tracks WHERE id=? AND namespace='real'", (track_id,)).fetchone()
                    if row is None or json.loads(row["metadata"]) != item["tracks"][track_id]["metadata"]:
                        raise AppError("A track changed after this preview. Nothing was linked; make a fresh preview.", 409)
                    annotations = json.loads(row["annotations"])
                    if annotations.get("local_file", "") or entry["path"] in linked_paths:
                        raise AppError("A file link changed after this preview. Nothing was linked; make a fresh preview.", 409)
                    if _snapshot(root, item["relative"], identity) != item["fingerprint"]:
                        raise AppError("A selected download changed or became unavailable. Nothing was linked; make a fresh preview.", 409)
                    annotations.update(local_file=entry["path"], owned=True)
                    if annotations.get("prep_status", "wishlist") in {"wishlist", "bought"}:
                        annotations["prep_status"] = "downloaded"
                    for field, source in (("bpm", "bpm"), ("musical_key", "key")):
                        if not annotations.get(field) and entry[source]:
                            annotations[field] = entry[source]
                    changes.append((json.dumps(annotations), track_id))
                if _root(settings["download_root"])[1:] != (root, identity) or any(
                        _snapshot(root, item["relative"], identity) != item["fingerprint"] for item, _ in chosen):
                    raise AppError("Selected files changed during confirmation. Nothing was linked; make a fresh preview.", 409)
                for annotations, track_id in changes:
                    db.execute("UPDATE tracks SET annotations=? WHERE id=? AND namespace='real'", (annotations, track_id))
            return {"updated": len(chosen), "track_ids": [track_id for _, track_id in chosen],
                    "message": "Selected downloads linked. Existing notes, tags and preparation work were kept; audio files were not moved."}
