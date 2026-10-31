"""Review and explicitly update DJList file links after a folder move.

Only DJList annotations are changed. Old folders are interpreted lexically;
arbitrary old locations are never probed. New files are inspected with directory
descriptors and no-follow metadata calls, without opening their audio contents.
"""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import secrets
import stat
import threading
import time

from spotify_api import AppError


AUDIO_EXTENSIONS = {".mp3", ".aiff", ".aif", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".alac"}
MAX_TRACKS = 50000
BATCH_LIMIT = 500
PLAN_SECONDS = 600


def _absolute(value, label):
    if (not isinstance(value, str) or not value or len(value) > 4096
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        raise AppError("Choose a valid absolute path for " + label + ".")
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts:
        raise AppError("Choose an absolute path without '..' for " + label + ".")
    return path


def _relative(path, root):
    try:
        return path.relative_to(root)
    except ValueError:
        return None


def _fingerprint(info):
    return (info.st_dev, info.st_ino, info.st_size,
            getattr(info, "st_mtime_ns", int(info.st_mtime * 1_000_000_000)),
            getattr(info, "st_ctime_ns", int(info.st_ctime * 1_000_000_000)))


def _root(value):
    try:
        raw = _absolute(str(Path(value).expanduser()), "your current music folder")
        if raw.is_symlink():
            raise AppError("Choose the actual music folder in Settings, rather than a folder symlink.", 409)
        root = raw.resolve(strict=True)
        info = root.stat()
        if not stat.S_ISDIR(info.st_mode):
            raise OSError()
        return raw, root, (info.st_dev, info.st_ino)
    except (OSError, RuntimeError, TypeError, ValueError):
        raise AppError("Your current music folder is unavailable. Connect the drive and check Settings before reviewing moved files.", 409) from None


def _snapshot(root, relative, root_identity):
    """Return a regular file's metadata without following any child symlinks."""
    if (relative.is_absolute() or not relative.parts or ".." in relative.parts
            or relative.suffix.lower() not in AUDIO_EXTENSIONS):
        return None, "unsafe", "Choose a supported audio file inside the current music folder."
    directory_fd = current_fd = None
    try:
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        directory_fd = current_fd = os.open(str(root), flags)
        info = os.fstat(directory_fd)
        if (info.st_dev, info.st_ino) != root_identity:
            return None, "unsafe", "The music folder changed. Make a fresh preview."
        for part in relative.parts[:-1]:
            next_fd = os.open(part, flags, dir_fd=current_fd)
            if current_fd != directory_fd:
                os.close(current_fd)
            current_fd = next_fd
        info = os.stat(relative.parts[-1], dir_fd=current_fd, follow_symlinks=False)
        if stat.S_ISLNK(info.st_mode):
            return None, "unsafe", "The proposed location is a symlink; choose the actual music folder."
        if not stat.S_ISREG(info.st_mode):
            return None, "unsafe", "The proposed location is not a regular audio file."
        return info, "available", "Same relative location. Confirm this is the same recording; audio identity was not checked."
    except FileNotFoundError:
        return None, "unavailable", "No file exists at this relative location in the current music folder."
    except (OSError, RuntimeError):
        return None, "unsafe", "This location is unreadable or passes through a symlink. Check the folder before relinking."
    finally:
        if current_fd is not None and current_fd != directory_fd:
            os.close(current_fd)
        if directory_fd is not None:
            os.close(directory_fd)


class RelocationPlanner:
    def __init__(self, store, clock=time.monotonic):
        self.store = store
        self.clock = clock
        self.lock = threading.RLock()
        self.plan = None

    def clear(self):
        with self.lock:
            self.plan = None

    @staticmethod
    def _settings(db):
        return {row["key"]: json.loads(row["value"]) for row in db.execute(
            "SELECT key,value FROM settings WHERE key IN ('mode','music_root')")}

    @staticmethod
    def _real(settings):
        if settings["mode"] != "real":
            raise AppError("Leave the demo before reviewing or relinking real files.", 409)

    def preview(self, old_root):
        old = _absolute(old_root, "the old music folder")
        if str(old) == old.anchor:
            raise AppError("Choose the old music folder, rather than the filesystem root.")
        if not (hasattr(os, "O_NOFOLLOW") and hasattr(os, "O_DIRECTORY")
                and os.open in os.supports_dir_fd and os.stat in os.supports_dir_fd):
            raise AppError("Moved-file review needs the local macOS or Linux app.", 409)
        with self.lock:
            with self.store.connect() as db:
                db.execute("BEGIN")
                settings = self._settings(db)
                self._real(settings)
                rows = list(db.execute("SELECT id,metadata,annotations FROM tracks WHERE namespace='real' ORDER BY id LIMIT ?", (MAX_TRACKS + 1,)))
                if len(rows) > MAX_TRACKS:
                    raise AppError("Moved-file review supports up to 50,000 tracks at a time.", 413)
                rows = [(row, json.loads(row["annotations"])) for row in rows]
                paths = list({annotations.get("local_file") for _, annotations in rows if annotations.get("local_file")})
                prior_sizes = {}
                for offset in range(0, len(paths), 500):
                    batch = paths[offset:offset + 500]
                    prior_sizes.update((row["path"], row["size"]) for row in db.execute(
                        "SELECT path,size FROM files WHERE path IN (%s)" % ",".join("?" for _ in batch), batch))
            raw_root, root, root_identity = _root(settings["music_root"])
            if old in (raw_root, root):
                raise AppError("The old folder and current music folder are the same. Save the new music folder in Settings first.")
            summary = {"linked": 0, "already_available": 0, "not_under_old_root": 0,
                       "eligible": 0, "unavailable": 0, "size_mismatch": 0, "unsafe": 0, "total": 0}
            available, blocked = [], []
            for row, annotations in rows:
                old_path = annotations.get("local_file", "")
                if not old_path:
                    continue
                summary["linked"] += 1
                try:
                    previous = _absolute(old_path, "the saved file")
                except AppError:
                    summary["not_under_old_root"] += 1
                    continue
                current_relative = _relative(previous, root)
                if current_relative is None:
                    current_relative = _relative(previous, raw_root)
                if current_relative is not None and _snapshot(root, current_relative, root_identity)[0] is not None:
                    summary["already_available"] += 1
                    continue
                relative = _relative(previous, old)
                if relative is None or not relative.parts:
                    summary["not_under_old_root"] += 1
                    continue
                info, status, reason = _snapshot(root, relative, root_identity)
                previous_size = prior_sizes.get(old_path)
                if info is not None and previous_size is not None and info.st_size != previous_size:
                    status, reason = "size_mismatch", "The new file size differs from the previously indexed copy. Review it manually instead."
                elif info is not None:
                    reason += " File size matches the previous index." if previous_size is not None else " Previous file size is unknown."
                selectable = status == "available"
                summary["eligible" if selectable else status] += 1
                summary["total"] += 1
                bucket = available if selectable else blocked
                if len(bucket) >= BATCH_LIMIT:
                    continue
                metadata = json.loads(row["metadata"])
                bucket.append({"track_id": row["id"], "title": metadata.get("title_and_version", ""),
                               "artists": metadata.get("artists", ""), "old_path": old_path,
                               "new_path": str(root / relative), "relative_path": str(relative),
                               "selectable": selectable, "status": status, "reason": reason,
                               "previous_size": previous_size, "current_size": info.st_size if info else None,
                               "_fingerprint": _fingerprint(info) if info else None})
            candidates = (available + blocked)[:BATCH_LIMIT]
            token = secrets.token_urlsafe(32)
            expires_at = (datetime.now(timezone.utc) + timedelta(seconds=PLAN_SECONDS)).isoformat(timespec="seconds")
            self.plan = {"token": token, "deadline": self.clock() + PLAN_SECONDS,
                         "setting": settings["music_root"], "root": root, "root_identity": root_identity,
                         "candidates": {item["track_id"]: item for item in candidates}}
            return {"token": token, "expires_at": expires_at, "old_root": str(old), "music_root": str(root),
                    "candidates": [{key: value for key, value in item.items() if not key.startswith("_")} for item in candidates],
                    "summary": summary, "truncated": summary["total"] > len(candidates), "limit": BATCH_LIMIT}

    def apply(self, token, track_ids):
        if not isinstance(token, str) or len(token) > 128 or not token.isascii():
            raise AppError("Make a fresh moved-file preview before relinking.", 409)
        with self.lock:
            plan = self.plan
            if plan is None or not secrets.compare_digest(token.encode("utf-8"), plan["token"].encode("ascii")):
                raise AppError("This preview is no longer available. Make a fresh preview before relinking.", 409)
            self.plan = None  # Each reviewed plan can be attempted only once.
            if self.clock() >= plan["deadline"]:
                raise AppError("This preview expired. Make a fresh preview before relinking.", 409)
            if (not isinstance(track_ids, list) or not 1 <= len(track_ids) <= BATCH_LIMIT
                    or any(not isinstance(identity, int) or isinstance(identity, bool) or identity < 1 for identity in track_ids)
                    or len(set(track_ids)) != len(track_ids)):
                raise AppError("Select between 1 and 500 distinct tracks from a fresh preview.")
            selected = []
            for identity in track_ids:
                candidate = plan["candidates"].get(identity)
                if candidate is None or not candidate["selectable"]:
                    raise AppError("Only available locations from this preview can be selected.")
                selected.append(candidate)
            with self.store.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                settings = self._settings(db)
                self._real(settings)
                if settings["music_root"] != plan["setting"]:
                    raise AppError("The music folder changed. Make a fresh preview before relinking.", 409)
                raw_root, root, root_identity = _root(settings["music_root"])
                if root != plan["root"] or root_identity != plan["root_identity"]:
                    raise AppError("The music folder changed. Make a fresh preview before relinking.", 409)
                changes = []
                for candidate in selected:
                    row = db.execute("SELECT annotations FROM tracks WHERE id=? AND namespace='real'", (candidate["track_id"],)).fetchone()
                    annotations = json.loads(row["annotations"]) if row else {}
                    if row is None or annotations.get("local_file", "") != candidate["old_path"]:
                        raise AppError("A saved file link changed after this preview. Nothing was relinked; make a fresh preview.", 409)
                    previous = Path(candidate["old_path"])
                    current_relative = _relative(previous, root)
                    if current_relative is None:
                        current_relative = _relative(previous, raw_root)
                    if current_relative is not None and _snapshot(root, current_relative, root_identity)[0] is not None:
                        raise AppError("An original file is available again. Nothing was relinked; make a fresh preview.", 409)
                    info, _, _ = _snapshot(root, Path(candidate["relative_path"]), root_identity)
                    if info is None or _fingerprint(info) != candidate["_fingerprint"]:
                        raise AppError("A proposed file changed or became unavailable. Nothing was relinked; make a fresh preview.", 409)
                    changes.append((json.dumps({**annotations, "local_file": candidate["new_path"]}), candidate["track_id"]))
                if _root(settings["music_root"])[1:] != (root, root_identity):
                    raise AppError("The music folder changed. Nothing was relinked; make a fresh preview.", 409)
                for annotations, identity in changes:
                    db.execute("UPDATE tracks SET annotations=? WHERE id=? AND namespace='real'", (annotations, identity))
            return {"updated": len(selected), "track_ids": track_ids,
                    "message": "Saved file links updated. Ownership, tags, notes and audio files were unchanged."}
