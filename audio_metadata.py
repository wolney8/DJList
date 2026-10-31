"""Bounded, read-only audio tags and header properties inside a selected folder.

No analysis, artwork extraction, decoding, writes or network access. TinyTag's
MIT-licensed runtime is bundled so the Mac launcher needs no package install.
"""
from contextlib import contextmanager
import math
import os
from pathlib import Path
import re
import stat
import time

from vendor.tinytag import TinyTag, UnsupportedFormatError


READER_VERSION = "tinytag:2.3.2/djlist:1"
AUDIO_EXTENSIONS = {".mp3", ".aiff", ".aif", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".alac"}
MAX_READ_BYTES = 8 * 1024 * 1024
MAX_IO_OPERATIONS = 100000
MAX_PARSE_SECONDS = 2.0


def _fingerprint(details):
    return {"device": details.st_dev, "inode": details.st_ino, "size": details.st_size,
            "mtime_ns": details.st_mtime_ns, "ctime_ns": details.st_ctime_ns}


@contextmanager
def _selected_file(path, music_root, open_content=False):
    """Anchor traversal to an open root; never follow a child or leaf symlink."""
    descriptors = []
    stream = None
    try:
        raw_root = Path(music_root).expanduser()
        candidate = Path(path).expanduser()
        if (not raw_root.is_absolute() or not candidate.is_absolute()
                or ".." in candidate.parts or ".." in raw_root.parts
                or raw_root == Path(raw_root.anchor) or raw_root.is_symlink()
                or candidate.suffix.lower() not in AUDIO_EXTENSIONS):
            raise ValueError("Choose a supported audio file inside the actual music folder.")
        root = raw_root.resolve(strict=True)
        try:
            relative = candidate.relative_to(raw_root)
        except ValueError:
            relative = candidate.relative_to(root)
        if not relative.parts:
            raise ValueError("Choose an audio file.")
        if not (hasattr(os, "O_NOFOLLOW") and hasattr(os, "O_DIRECTORY")
                and os.open in os.supports_dir_fd and os.stat in os.supports_dir_fd):
            raise OSError("Safe metadata reading requires macOS or Linux.")
        root_info = root.stat()
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        current = os.open(str(root), flags)
        descriptors.append(current)
        opened_root = os.fstat(current)
        if (opened_root.st_dev, opened_root.st_ino) != (root_info.st_dev, root_info.st_ino):
            raise OSError("The selected folder changed.")
        for part in relative.parts[:-1]:
            current = os.open(part, flags, dir_fd=current)
            descriptors.append(current)
        details = os.stat(relative.name, dir_fd=current, follow_symlinks=False)
        if not stat.S_ISREG(details.st_mode):
            raise ValueError("Choose a regular audio file, not a symlink or device.")
        if open_content:
            audio_fd = os.open(relative.name, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0), dir_fd=current)
            descriptors.append(audio_fd)
            opened = os.fstat(audio_fd)
            if not stat.S_ISREG(opened.st_mode) or _fingerprint(opened) != _fingerprint(details):
                raise OSError("The audio file changed before it could be read.")
            stream = os.fdopen(audio_fd, "rb")
            descriptors.pop()  # stream now owns this descriptor
        yield stream, details, str(root / relative)
    finally:
        if stream is not None:
            stream.close()
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def file_fingerprint(path, music_root):
    """Return file identity/stat values safely, or None; do not read audio bytes."""
    try:
        with _selected_file(path, music_root) as (_, details, _):
            return _fingerprint(details)
    except (OSError, ValueError, TypeError, RuntimeError, UnicodeError):
        return None


class _ReadLimit(Exception):
    pass


class _BoundedReader:
    """Only read/seek/tell are exposed to the parser, with per-file budgets."""
    def __init__(self, stream, size):
        self.stream, self.size = stream, size
        self.bytes_read = self.operations = 0
        self.deadline = time.monotonic() + MAX_PARSE_SECONDS
        self.limited = False

    def _check(self):
        self.operations += 1
        if self.operations > MAX_IO_OPERATIONS or time.monotonic() > self.deadline:
            self.limited = True
            raise _ReadLimit("Metadata read budget reached.")

    def read(self, size=-1):
        self._check()
        if size is None or size < 0:
            size = self.size - self.stream.tell()
        if size > MAX_READ_BYTES - self.bytes_read:
            self.limited = True
            raise _ReadLimit("Metadata read budget reached.")
        value = self.stream.read(size)
        self.bytes_read += len(value)
        return value

    def seek(self, offset, whence=os.SEEK_SET):
        self._check()
        if whence == os.SEEK_SET:
            position = offset
        elif whence == os.SEEK_CUR:
            position = self.stream.tell() + offset
        elif whence == os.SEEK_END:
            position = self.size + offset
        else:
            raise ValueError("Unsupported seek mode.")
        if not 0 <= position <= self.size:
            raise ValueError("The audio header points outside the file.")
        return self.stream.seek(position)

    def tell(self):
        self._check()
        return self.stream.tell()


def _text(value, limit=1000):
    if value is None:
        return ""
    value = str(value)
    value = " ".join("".join(c if ord(c) >= 32 and ord(c) != 127 and not 0xD800 <= ord(c) <= 0xDFFF else " "
                             for c in value).split())
    return value[:limit]


def _first(other, name):
    values = other.get(name, [])
    return values[0] if isinstance(values, list) and values else ""


def _bpm(value):
    value = _text(value, 24)
    try:
        number = float(value)
        return value if math.isfinite(number) and 0 < number <= 999 else ""
    except (ValueError, TypeError, OverflowError):
        return ""


def _duration(value):
    try:
        number = float(value)
        return round(number * 1000) if math.isfinite(number) and 0 < number <= 36000 else None
    except (ValueError, TypeError, OverflowError):
        return None


def _empty(path, status, message, fingerprint=None):
    return {"status": status, "message": message, "reader": READER_VERSION,
            "fingerprint": fingerprint, "title": "", "artist": "", "album": "",
            "genre": "", "musical_key": "", "bpm": "", "duration_ms": None,
            "isrc": "", "version_notes": "", "format": Path(path).suffix.lstrip(".").upper() if isinstance(path, (str, Path)) else "",
            "mime_type": ""}


def read_audio_metadata(path, music_root):
    """Read optional tags and duration; ordinary file/parser failures are explicit.

    The supplied root must be the user-selected music or download folder.
    Missing values stay empty. A partial or changed read supplies no guessed tags.
    """
    fingerprint = None
    reader = None
    result = _empty(path, "unavailable", "Audio tags could not be read. Check the file and selected folder.")
    try:
        with _selected_file(path, music_root, open_content=True) as (stream, before, canonical):
            fingerprint = _fingerprint(before)
            reader = _BoundedReader(stream, before.st_size)
            try:
                if Path(canonical).suffix.lower() == ".aac":
                    raise UnsupportedFormatError("Raw AAC is not supported by this reader.")
                tag = TinyTag.get(filename=canonical, file_obj=reader, tags=True, duration=True, image=False)
            except UnsupportedFormatError:
                result = _empty(path, "unsupported", "This audio format has no supported tag reader; filename matching is still available.", fingerprint)
            except Exception:
                if reader.limited:
                    result = _empty(path, "limited", "The file's metadata exceeded the read limit; filename matching is still available.", fingerprint)
                else:
                    result = _empty(path, "error", "The file's audio tags could not be parsed; filename matching is still available.", fingerprint)
            else:
                other = tag.other
                artists = [_text(tag.artist)] + [_text(value) for value in other.get("artist", [])[:10]]
                artist = "; ".join(dict.fromkeys(value for value in artists if value))[:1000]
                isrc = re.sub(r"[\s-]", "", _text(_first(other, "isrc"), 24)).upper()
                version_notes = "; ".join(dict.fromkeys(_text(_first(other, key), 300) for key in
                    ("version", "mix", "mixname", "subtitle", "tit3", "tt3") if _first(other, key)))[:1000]
                result = {**_empty(path, "read", "Read from audio file tags and headers; no audio analysis was performed.", fingerprint),
                          "title": _text(tag.title), "artist": artist, "album": _text(tag.album),
                          "genre": _text(tag.genre, 200), "musical_key": _text(_first(other, "initial_key"), 128),
                          "bpm": _bpm(_first(other, "bpm")), "duration_ms": _duration(tag.duration),
                          "mime_type": _text(getattr(tag, "mime_type", ""), 128),
                          "version_notes": version_notes,
                          "isrc": isrc if re.fullmatch(r"[A-Z]{2}[A-Z0-9]{3}[0-9]{7}", isrc) else ""}
                if not any(result[key] for key in ("title", "artist", "album", "genre", "musical_key", "bpm", "duration_ms", "isrc", "version_notes")):
                    result.update(status="empty", message="No usable audio tags or duration were supplied; filename matching is still available.")
                elif not (result["title"] and result["artist"]):
                    result["message"] = "Some audio properties were read, but artist/title identity still relies on the filename."
            after = _fingerprint(os.fstat(stream.fileno()))
        # Also detect replacement/renaming of the path while its original FD was open.
        if fingerprint != after or fingerprint != file_fingerprint(canonical, music_root):
            return _empty(path, "changed", "The audio file changed while tags were read. Refresh again after the download or edit finishes.")
        return result
    except (OSError, ValueError, TypeError, RuntimeError, UnicodeError):
        return _empty(path, "unavailable", "Audio tags could not be read. Check the file and selected folder.", fingerprint)
