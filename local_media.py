"""Bounded read-only streaming of explicitly indexed local audio.

The browser supplies an inventory identifier, never a filesystem path. All
directories are opened without following symlinks; range reads retain that
descriptor and stop if its identity changes. No audio is cached or written.
"""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import stat

from audio_metadata import _fingerprint
from spotify_api import AppError

MAX_MEDIA_BYTES = 512 * 1024 * 1024
MAX_INDEXED_PATHS = 50000
CHUNK_BYTES = 128 * 1024
MIME_TYPES = {".mp3": "audio/mpeg", ".wav": "audio/wav", ".aif": "audio/aiff",
              ".aiff": "audio/aiff", ".flac": "audio/flac", ".m4a": "audio/mp4",
              ".aac": "audio/aac", ".alac": "audio/mp4", ".ogg": "audio/ogg"}


def _configuration(settings):
    return tuple(settings.get(key, "") for key in ("mode", "music_root", "download_root"))


def _range(value, size):
    if value is None:
        return 0, size - 1, 200
    if not isinstance(value, str) or len(value) > 100:
        match = None
    else:
        match = re.fullmatch(r"bytes=([0-9]*)-([0-9]*)", value.strip())
    try:
        if not match or not (match[1] or match[2]):
            raise ValueError()
        if not match[1]:
            suffix = int(match[2])
            if suffix <= 0:
                raise ValueError()
            start, end = max(0, size - suffix), size - 1
        else:
            start, end = int(match[1]), int(match[2]) if match[2] else size - 1
            if start >= size or end < start:
                raise ValueError()
            end = min(end, size - 1)
        return start, end, 206
    except ValueError:
        error = AppError("That audio range is unavailable.", 416)
        error.content_range = "bytes */%d" % size
        raise error from None


def indexed_path(store, entry_id):
    if not isinstance(entry_id, str) or not re.fullmatch(r"[a-f0-9]{24}", entry_id):
        raise AppError("Choose a valid library entry.")
    with store.connect() as db:
        paths = db.execute("SELECT path FROM library_entries UNION SELECT path FROM files ORDER BY path LIMIT ?",
                           (MAX_INDEXED_PATHS + 1,)).fetchall()
    if len(paths) > MAX_INDEXED_PATHS:
        raise AppError("Preview supports up to 50,000 indexed paths. Narrow your indexed collection first.", 413)
    for row in paths:
        value = row[0]
        if isinstance(value, str) and hashlib.sha256(value.encode("utf-8")).hexdigest()[:24] == entry_id:
            path = Path(value)
            if not path.is_absolute() or ".." in path.parts or ".djlist-removed" in path.parts:
                break
            return path
    raise AppError("This library entry is no longer indexed. Refresh the library.", 404)


class MediaStream:
    def __init__(self, stream, info, start, end, status, mime, current):
        self.file, self.fd = stream, stream.fileno()
        self.start, self.end, self.status = start, end, status
        self.size = info.st_size
        self.fingerprint = _fingerprint(info)
        self._current = current
        self.headers = {"Content-Type": mime, "Content-Length": str(end - start + 1),
                        "Accept-Ranges": "bytes", "Cache-Control": "no-store",
                        "X-Content-Type-Options": "nosniff",
                        "ETag": '"' + hashlib.sha256(json.dumps(self.fingerprint, sort_keys=True).encode()).hexdigest() + '"'}
        if status == 206:
            self.headers["Content-Range"] = "bytes %d-%d/%d" % (start, end, info.st_size)

    def iter_chunks(self):
        self.file.seek(self.start)
        remaining = self.end - self.start + 1
        while remaining:
            if not self._current() or _fingerprint(os.fstat(self.fd)) != self.fingerprint:
                raise AppError("The audio file or folder settings changed during preview.", 409)
            chunk = self.file.read(min(CHUNK_BYTES, remaining))
            if not chunk or _fingerprint(os.fstat(self.fd)) != self.fingerprint:
                raise AppError("The audio file changed during preview.", 409)
            remaining -= len(chunk)
            yield chunk


class LocalMedia:
    def __init__(self, store, library):
        self.store, self.library = store, library

    @contextmanager
    def open(self, entry_id, range_header=None):
        settings = self.store.settings()
        if settings.get("mode") != "real":
            raise AppError("Leave the demo before previewing local audio.", 409)
        path = indexed_path(self.store, entry_id)
        if path.suffix.lower() not in MIME_TYPES:
            raise AppError("Choose a supported local audio file.", 409)
        chosen = None
        for value in (settings.get("music_root"), settings.get("download_root")):
            if not value:
                continue
            root = Path(value).expanduser()
            if not root.is_absolute() or ".." in root.parts or root == Path(root.anchor):
                continue
            try:
                relative = path.relative_to(root)
            except ValueError:
                continue
            if relative.parts:
                chosen = root
                break
        if chosen is None:
            raise AppError("This audio file is outside the selected music and Incoming folders.", 409)
        if not (hasattr(os, "O_NOFOLLOW") and hasattr(os, "O_DIRECTORY") and os.open in os.supports_dir_fd):
            raise AppError("Local audio preview requires the Mac or Linux app.", 409)
        descriptors, directories, stream = [], [], None
        try:
            flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY
            current = os.open(path.anchor, flags)
            descriptors.append(current)
            directories.append((Path(path.anchor), current))
            traversed = Path(path.anchor)
            for part in path.parts[1:-1]:
                current = os.open(part, flags, dir_fd=current)
                descriptors.append(current)
                traversed = traversed / part
                directories.append((traversed, current))
            before = os.stat(path.name, dir_fd=current, follow_symlinks=False)
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                raise AppError("Preview requires a regular audio file without symlinks or hardlinks.", 409)
            if not 0 < before.st_size <= MAX_MEDIA_BYTES:
                raise AppError("Local previews support non-empty audio files up to 512 MiB.", 413)
            fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0), dir_fd=current)
            stream = os.fdopen(fd, "rb")
            after = os.fstat(stream.fileno())
            if _fingerprint(before) != _fingerprint(after) or after.st_nlink != 1:
                raise AppError("The audio file changed. Refresh the library and preview it again.", 409)
            root_identity = chosen.stat()
            config = _configuration(settings)
            def current_file():
                try:
                    for directory, directory_fd in directories:
                        current_directory, opened_directory = directory.lstat(), os.fstat(directory_fd)
                        if (not stat.S_ISDIR(current_directory.st_mode)
                                or (current_directory.st_dev, current_directory.st_ino) != (opened_directory.st_dev, opened_directory.st_ino)):
                            return False
                    current_root = chosen.lstat()
                    return (config == _configuration(self.store.settings())
                            and not chosen.is_symlink()
                            and (current_root.st_dev, current_root.st_ino) == (root_identity.st_dev, root_identity.st_ino)
                            and _fingerprint(path.lstat()) == _fingerprint(after)
                            and os.fstat(stream.fileno()).st_nlink == 1)
                except OSError:
                    return False
            if not current_file():
                raise AppError("The file or its folder changed before preview started.", 409)
            start, end, status = _range(range_header, after.st_size)
            yield MediaStream(stream, after, start, end, status, MIME_TYPES[path.suffix.lower()], current_file)
        except (OSError, RuntimeError, ValueError, TypeError):
            raise AppError("The local audio file is unavailable or its path is unsafe. Reconnect the drive and refresh media.", 409) from None
        finally:
            if stream is not None:
                stream.close()
            for fd in reversed(descriptors):
                os.close(fd)
