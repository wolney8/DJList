"""Provision the pinned CueGen component inside DJList; never execute it here.

Only the official v1.0.37 macOS release is accepted. Archive and executable
hashes were recorded from that immutable release on 2026-10-31. The release
has no publisher signature/checksum; these pins detect changed bytes, rather
than claiming independent publisher attestation. The executable is run only
by CueGenBridge inside its mandatory macOS sandbox.
"""
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import secrets
import shutil
import stat
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

from cue_planner import CuePlannerError
from file_operations import _rename_exclusive
from rekordbox_backups import _absolute, _directory, _directory_current, _fingerprint, _identity
from spotify_api import AppError


RELEASE = {
    "version": "1.0.37", "architecture": "osx-x64", "entry": "CueGen.Console",
    "url": "https://github.com/mganss/CueGen/releases/download/v1.0.37/CueGen.1.0.37.osx-x64.zip",
    "archive_size": 9375744,
    "archive_sha256": "9c36e8fe9a990bf0bd44db7c13d4bb72e135895ceab6d7d1a126b23f51421087",
    "executable_size": 24398734,
    "executable_sha256": "6cf78c7054a5ebc409bbfbd883ecdcde6e67aceecea2d45692557eb0e6bc684a",
}
DOWNLOAD_SECONDS = 120
ALLOWED_DOWNLOAD_HOSTS = {"github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com"}
NOTICE_FILES = ("LICENSE", "DEPENDENCY_NOTICES.txt", "DEPENDENCIES.json")


def _error(message, code="cuegen_setup", status=409):
    return CuePlannerError(message, code, status)


def _approved_url(url):
    try:
        parsed = urllib.parse.urlsplit(url)
        return (parsed.scheme == "https" and parsed.hostname in ALLOWED_DOWNLOAD_HOSTS
                and parsed.port in (None, 443) and not parsed.username and not parsed.password)
    except ValueError:
        return False


class _ReleaseRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _approved_url(newurl):
            raise _error("The cue-engine download redirected outside its approved HTTPS release hosts.", "cuegen_download")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download_release(progress=None):
    """Read one fixed public asset with HTTPS, origin and byte/time bounds."""
    request = urllib.request.Request(RELEASE["url"], headers={"User-Agent": "DJList cue-engine setup", "Accept": "application/octet-stream"})
    opener = urllib.request.build_opener(_ReleaseRedirect())
    deadline = time.monotonic() + DOWNLOAD_SECONDS
    data = bytearray()
    try:
        with opener.open(request, timeout=15) as response:
            if not _approved_url(response.geturl()):
                raise _error("The cue-engine download did not come from its approved release host.", "cuegen_download")
            declared = response.headers.get("Content-Length")
            if declared is not None and declared != str(RELEASE["archive_size"]):
                raise _error("The cue-engine release size has changed. Setup stopped before installation.", "cuegen_download")
            while True:
                if time.monotonic() > deadline:
                    raise _error("The cue-engine download took too long. Try Check cue engine again.", "cuegen_download")
                chunk = response.read(min(256 * 1024, RELEASE["archive_size"] + 1 - len(data)))
                if not chunk:
                    break
                data.extend(chunk)
                if len(data) > RELEASE["archive_size"]:
                    raise _error("The cue-engine download exceeded the expected size.", "cuegen_download")
                if progress:
                    progress("Downloading cue engine", len(data), RELEASE["archive_size"])
        return bytes(data)
    except (OSError, ValueError, urllib.error.URLError):
        raise _error("The cue engine could not be downloaded from its official release. Check the connection and retry; your music and database were not changed.", "cuegen_download") from None


def unpack_release(data):
    if (not isinstance(data, bytes) or len(data) != RELEASE["archive_size"]
            or hashlib.sha256(data).hexdigest() != RELEASE["archive_sha256"]):
        raise _error("The cue-engine download failed its pinned checksum. Nothing was installed.", "cuegen_checksum")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            members = archive.infolist()
            if len(members) != 1:
                raise ValueError()
            member = members[0]
            mode = member.external_attr >> 16
            if (member.filename != RELEASE["entry"] or member.is_dir()
                    or member.file_size != RELEASE["executable_size"]
                    or member.flag_bits & 1 or stat.S_ISLNK(mode)
                    or stat.S_IFMT(mode) not in (0, stat.S_IFREG)):
                raise ValueError()
            with archive.open(member) as file:
                executable = file.read(RELEASE["executable_size"] + 1)
            if (len(executable) != RELEASE["executable_size"]
                    or executable[:4] != b"\xcf\xfa\xed\xfe"
                    or hashlib.sha256(executable).hexdigest() != RELEASE["executable_sha256"]):
                raise ValueError()
            return executable
    except (ValueError, OSError, zipfile.BadZipFile, RuntimeError):
        raise _error("The cue-engine archive did not match the expected executable. Nothing was installed.", "cuegen_checksum") from None


class CueGenRuntime:
    def __init__(self, store, platform=None, downloader=None, home=None):
        self.store = store
        self.platform = platform or sys.platform
        self.downloader = downloader or download_release
        self.home = Path.home() if home is None else Path(home)
        self.root = store.directory / "engines" / "cuegen"
        self.name = RELEASE["version"] + "-" + RELEASE["architecture"]
        self.path = self.root / self.name / RELEASE["entry"]
        self._lock = threading.Lock()
        self._checked = None

    def _read_binary(self, path, need_execute=False):
        with _directory(path.parent) as parent:
            descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0), dir_fd=parent)
            try:
                info = os.fstat(descriptor)
                if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                        or info.st_size != RELEASE["executable_size"]
                        or need_execute and not info.st_mode & 0o111):
                    raise _error("The cue-engine file does not match the supported release.", "cuegen_checksum")
                content = bytearray()
                while len(content) <= RELEASE["executable_size"]:
                    chunk = os.read(descriptor, min(1024 * 1024, RELEASE["executable_size"] + 1 - len(content)))
                    if not chunk:
                        break
                    content.extend(chunk)
                if (len(content) != RELEASE["executable_size"]
                        or hashlib.sha256(content).hexdigest() != RELEASE["executable_sha256"]
                        or _fingerprint(os.fstat(descriptor)) != _fingerprint(info)
                        or _fingerprint(os.stat(path.name, dir_fd=parent, follow_symlinks=False)) != _fingerprint(info)):
                    raise _error("The cue-engine file failed its pinned checksum or changed during verification.", "cuegen_checksum")
                _directory_current(path.parent, parent)
                return bytes(content), _fingerprint(info)
            finally:
                os.close(descriptor)

    def verified_path(self, force=False):
        """Read-only verification; no installation or executable launch."""
        with _directory(self.path.parent) as parent:
            info = os.stat(self.path.name, dir_fd=parent, follow_symlinks=False)
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or not info.st_mode & 0o111 or info.st_size != RELEASE["executable_size"]):
                raise _error("The installed cue engine is not a valid regular executable.", "cuegen_checksum")
            fingerprint = _fingerprint(info)
            if force or self._checked != fingerprint:
                _, self._checked = self._read_binary(self.path, need_execute=True)
            _directory_current(self.path.parent, parent)
        return self.path

    def status(self):
        installed, issue = False, None
        try:
            self.verified_path()
            installed = True
        except FileNotFoundError:
            pass
        except (OSError, AppError):
            issue = "The installed cue-engine files failed verification. Setup will not overwrite them."
        real = self.store.settings().get("mode", "real") == "real"
        supported = self.platform == "darwin"
        if not real:
            message = "Cue engine is paused in the demo."
        elif not supported:
            message = "The internal CueGen component runs on macOS."
        elif issue:
            message = issue
        elif installed:
            message = "Cue engine is installed and its pinned bytes are verified."
        else:
            message = "DJList will prepare CueGen internally when you request cue suggestions. No separate application needs to be opened."
        return {"installed": installed, "managed": True, "required_version": RELEASE["version"],
                "architecture": RELEASE["architecture"], "download_bytes": RELEASE["archive_size"],
                "supported": supported and real, "message": message, "integrity_error": issue,
                "installed_path": str(self.path) if installed else None,
                "architecture_note": "The upstream component is Intel x64. Apple Silicon requires an existing compatible Rosetta runtime; DJList does not install Rosetta."}

    def _existing_binary(self):
        settings = self.store.settings()
        candidates = []
        configured = settings.get("cuegen_path")
        if configured:
            try:
                candidates.append(_absolute(configured, "the existing cue engine"))
            except AppError:
                pass
        # This is the ordinary previous-install location, never a user's name
        # or a recursive disk search. Only bytes matching our pinned release
        # are copied; unknown versions are never executed to identify them.
        candidates.extend((self.home / "Downloads" / RELEASE["entry"],
                           self.home / "Downloads" / ("CueGen." + RELEASE["version"] + ".osx-x64") / RELEASE["entry"]))
        for path in dict.fromkeys(candidates):
            try:
                content, _ = self._read_binary(path)
                return content
            except (OSError, AppError):
                continue
        return None

    @staticmethod
    def _copy_notices(target_directory):
        """Keep the supplied notices with the managed, unchanged component."""
        source = Path(__file__).resolve().parent / "vendor" / "cuegen"
        digests = {}
        with _directory(source) as directory:
            for name in NOTICE_FILES:
                descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0), dir_fd=directory)
                try:
                    info = os.fstat(descriptor)
                    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or not 0 < info.st_size <= 512 * 1024:
                        raise _error("The cue-engine licence notices are missing or invalid. Restore the DJList application files before setup.")
                    data = bytearray()
                    while len(data) <= info.st_size:
                        chunk = os.read(descriptor, min(64 * 1024, info.st_size + 1 - len(data)))
                        if not chunk:
                            break
                        data.extend(chunk)
                    if (len(data) != info.st_size or _fingerprint(os.fstat(descriptor)) != _fingerprint(info)
                            or _fingerprint(os.stat(name, dir_fd=directory, follow_symlinks=False)) != _fingerprint(info)):
                        raise _error("The cue-engine licence notices changed during setup. Retry from the current application folder.")
                finally:
                    os.close(descriptor)
                target = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=target_directory)
                try:
                    pending = memoryview(data)
                    while pending:
                        written = os.write(target, pending)
                        if written <= 0:
                            raise OSError("Short licence notice write")
                        pending = pending[written:]
                    os.fsync(target)
                finally:
                    os.close(target)
                digests[name] = hashlib.sha256(data).hexdigest()
            _directory_current(source, directory)
        return digests

    def prepare(self, progress=None):
        if self.platform != "darwin":
            raise _error("The internal CueGen component is available in the local macOS app.")
        if self.store.settings().get("mode", "real") != "real":
            raise _error("Leave the demo before preparing the cue engine.", "demo_mode")
        if not self._lock.acquire(blocking=False):
            raise _error("The cue engine is already being prepared. Check Activity.", "cuegen_busy")
        try:
            try:
                return self.verified_path()
            except FileNotFoundError:
                pass
            if progress:
                progress("Checking existing cue engine", None, None)
            executable = self._existing_binary()
            source = "existing verified installation"
            if executable is None:
                source = "official pinned release"
                executable = unpack_release(self.downloader(progress))
            if progress:
                progress("Installing verified cue engine", None, None)
            with _directory(self.root, create=True) as parent:
                # Refuse any existing destination, including incomplete setup;
                # do not delete or replace unidentified files during repair.
                try:
                    os.stat(self.name, dir_fd=parent, follow_symlinks=False)
                except FileNotFoundError:
                    pass
                else:
                    raise _error("A cue-engine folder already exists but is not valid. It has been preserved. Keep your existing DJList data folder and ask for a cue-engine repair.", "cuegen_checksum")
                stage_name = "install-" + secrets.token_hex(12)
                os.mkdir(stage_name, 0o700, dir_fd=parent)
                stage = self.root / stage_name
                identity = None
                try:
                    with _directory(stage) as directory:
                        identity = _identity(os.fstat(directory))
                        disk = os.fstatvfs(directory)
                        if disk.f_bavail * disk.f_frsize < RELEASE["executable_size"] + 32 * 1024 * 1024:
                            raise _error("There is not enough local space to install the cue engine.")
                        descriptor = os.open(RELEASE["entry"], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o700, dir_fd=directory)
                        try:
                            pending = memoryview(executable)
                            while pending:
                                written = os.write(descriptor, pending)
                                if written <= 0:
                                    raise OSError("Short engine write")
                                pending = pending[written:]
                            os.fsync(descriptor)
                        finally:
                            os.close(descriptor)
                        self._read_binary(stage / RELEASE["entry"], need_execute=True)
                        notices = self._copy_notices(directory)
                        manifest = {"version": RELEASE["version"], "architecture": RELEASE["architecture"],
                                    "sha256": RELEASE["executable_sha256"], "source": source,
                                    "notices": notices,
                                    "installed_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
                        descriptor = os.open("manifest.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
                        try:
                            payload = json.dumps(manifest, sort_keys=True).encode("utf-8")
                            if os.write(descriptor, payload) != len(payload):
                                raise OSError("Short engine manifest write")
                            os.fsync(descriptor)
                        finally:
                            os.close(descriptor)
                        os.fsync(directory)
                        _directory_current(stage, directory)
                    _directory_current(self.root, parent)
                    _rename_exclusive(parent, stage_name, parent, self.name)
                    os.fsync(parent)
                finally:
                    # This is only an owned, random setup directory. Never
                    # remove the published component, any source, or backup.
                    try:
                        _directory_current(self.root, parent)
                        with _directory(stage) as directory:
                            if _identity(os.fstat(directory)) != identity:
                                raise OSError("Setup directory changed")
                        if not shutil.rmtree.avoids_symlink_attacks:
                            raise OSError("Safe cleanup unavailable")
                        shutil.rmtree(stage)
                    except (OSError, AppError):
                        pass
            return self.verified_path()
        except (OSError, ValueError, zipfile.BadZipFile):
            raise _error("The cue engine could not be installed with the required file checks. Your library was not changed.") from None
        finally:
            self._lock.release()
