"""Cold, byte-verified Rekordbox database snapshots; never a database writer.

Only DJList's own records and newly created backup files are written. The
selected master.db is opened O_RDONLY, with POSIX read locks while copying.
Nonempty journals, running known library tools and an unverifiable process
state block a snapshot. No journal recovery, checkpoint, restore, overwrite or
automatic deletion is provided. A checksum proves copied bytes, not the
logical health or restorability of the encrypted Rekordbox collection.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import sys
import threading
import time

try:
    import fcntl
except ImportError:  # pragma: no cover - production support is macOS only
    fcntl = None

from spotify_api import AppError


INTERVAL_SECONDS = 24 * 60 * 60
MAX_DATABASE_BYTES = 4 * 1024 * 1024 * 1024
MAX_COMPANION_BYTES = 32 * 1024 * 1024
COPY_SECONDS = 180
CHUNK_BYTES = 1024 * 1024
RESERVE_BYTES = 64 * 1024 * 1024
SQLITE_PENDING_BYTE = 0x40000000
SQLITE_LOCK_BYTES = 512
BACKUP_ID = re.compile(r"^\d{8}T\d{6}Z-[a-f0-9]{16}$")
FILE_NAMES = {"master.db", "masterPlaylists6.xml"}
LIMITATIONS = [
    "These are database snapshots, not full Rekordbox library backups. Analysis files and music are not included.",
    "Checksums verify copied bytes. Encrypted database structure and restoration in Rekordbox have not been verified.",
    "Automatic backups run only while DJList is open, normally once every 24 hours when the database has changed and Rekordbox is closed.",
    "DJList never restores over the source, deletes old backups, or modifies the selected database or its journals.",
]


def _iso(timestamp=None):
    return datetime.fromtimestamp(time.time() if timestamp is None else timestamp, timezone.utc).isoformat(timespec="seconds")


def _timestamp(value):
    try:
        return datetime.fromisoformat(value).timestamp()
    except (TypeError, ValueError, OverflowError):
        return 0


def _fingerprint(info):
    return {"device": info.st_dev, "inode": info.st_ino, "size": info.st_size,
            "mtime_ns": info.st_mtime_ns, "ctime_ns": info.st_ctime_ns}


def _identity(info):
    return info.st_dev, info.st_ino


def _absolute(value, label):
    if (not isinstance(value, str) or not value or len(value) > 4096
            or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise AppError("Choose a valid absolute path for " + label + ".", 409)
    try:
        path = Path(value).expanduser()
    except (ValueError, RuntimeError):
        raise AppError("Choose a valid absolute path for " + label + ".", 409) from None
    if not path.is_absolute() or ".." in path.parts or path == Path(path.anchor):
        raise AppError("Choose a specific absolute path without '..' for " + label + ".", 409)
    return path


@contextmanager
def _directory(path, create=False, forbidden_identity=None):
    """Open every component without following a symlink; writes use dir_fds."""
    descriptors = []
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        current = os.open(path.anchor, flags)
        descriptors.append(current)
        for component in path.parts[1:]:
            # macOS usually has case-insensitive paths. Compare directory
            # identity as well as lexical paths before creating descendants.
            if forbidden_identity == _identity(os.fstat(current)):
                raise AppError("Keep backups outside the Rekordbox database folder, including aliases of that folder.", 409)
            if create:
                try:
                    os.mkdir(component, 0o700, dir_fd=current)
                except FileExistsError:
                    pass
            current = os.open(component, flags, dir_fd=current)
            descriptors.append(current)
        if forbidden_identity == _identity(os.fstat(current)):
            raise AppError("Keep backups outside the Rekordbox database folder, including aliases of that folder.", 409)
        yield current
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _directory_current(path, descriptor):
    with _directory(path) as reopened:
        if _identity(os.fstat(reopened)) != _identity(os.fstat(descriptor)):
            raise AppError("A selected folder changed during backup. Check the paths and try again.", 409)


def _regular(directory_fd, name, maximum, optional=False):
    try:
        info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        if optional:
            return None
        raise AppError("The selected master.db is unavailable. Reconnect its drive and check Settings.", 409) from None
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size <= 0 or info.st_size > maximum:
        raise AppError("The selected database or companion file is empty, too large, linked elsewhere, or is not a regular file. Symlinks and hardlinks are not accepted.", 409)
    return info


def _journals(directory_fd):
    result = {}
    for name in ("master.db-wal", "master.db-journal", "master.db-shm"):
        try:
            info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        except FileNotFoundError:
            result[name] = None
            continue
        if not stat.S_ISREG(info.st_mode):
            raise AppError("A database sidecar is not a regular file. Backup is paused; the source files have not been changed.", 409)
        if name != "master.db-shm" and info.st_size:
            raise AppError("A database journal still contains data. Open and close Rekordbox normally, then try again. DJList will not remove or change that journal.", 409)
        # Existing SHM is not copied or changed. Its creation/replacement during
        # the operation is detected even if the WAL is briefly empty.
        result[name] = _fingerprint(info)
    return result


def check_rekordbox_closed(platform=None, runner=None):
    """Inspect known library applications, without returning process arguments."""
    if (platform or sys.platform) != "darwin":
        return {"known": False, "running": [], "message": "Automatic process checks are supported by the local macOS app."}
    runner = runner or subprocess.run
    try:
        result = runner(["/bin/ps", "-axww", "-o", "command="], capture_output=True,
                        text=True, timeout=5, check=False)
        if result.returncode != 0 or not result.stdout.strip() or len(result.stdout) > 2 * 1024 * 1024:
            raise ValueError()
        running = set()
        for line in result.stdout.splitlines():
            compact = re.sub(r"[^a-z0-9]", "", line.casefold())
            if "rekordbox" in compact:
                running.add("Rekordbox")
            if "cuegen" in compact:
                running.add("CueGen")
            if "libraryfixer" in compact:
                running.add("Library Fixer")
        return {"known": True, "running": sorted(running), "message": "Known library applications checked."}
    except (OSError, ValueError, subprocess.SubprocessError):
        return {"known": False, "running": [], "message": "Could not verify that Rekordbox and library tools are closed. Backup is paused."}


class RekordboxBackups:
    def __init__(self, store, work_lock, process_checker=None, clock=None, interval=60, platform=None):
        self.store, self.work_lock = store, work_lock
        self.platform = platform or sys.platform
        self.process_checker = process_checker or (lambda: check_rekordbox_closed(self.platform))
        self.clock = clock or time.time
        self.interval = max(1, interval)
        self._lock = threading.RLock()
        self._operation = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        with self.store.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS rekordbox_backups (
                    id TEXT PRIMARY KEY, created_at TEXT NOT NULL, metadata TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS rekordbox_backup_state (
                    id INTEGER PRIMARY KEY CHECK(id=1), metadata TEXT NOT NULL);
            """)
            row = db.execute("SELECT metadata FROM rekordbox_backup_state WHERE id=1").fetchone()
        self._state = {"running": False, "last_checked_at": None, "last_successful_check_at": None,
                       "last_config": None, "last_attempt_config": None,
                       "message": "Choose your active master.db and a backup folder in Settings.",
                       "error": "", "revision": 0}
        if row:
            try:
                self._state.update(json.loads(row[0]))
            except (ValueError, TypeError):
                pass
        self._state["running"] = False

    @property
    def supported(self):
        return (self.platform == "darwin" and fcntl is not None
                and all(hasattr(os, attr) for attr in ("O_NOFOLLOW", "O_DIRECTORY"))
                and os.open in os.supports_dir_fd and os.stat in os.supports_dir_fd)

    def _root(self, settings):
        value = settings.get("rekordbox_backup_root") or str(self.store.directory / "rekordbox-backups")
        return _absolute(value, "the backup folder")

    def _config(self, settings):
        return [settings.get("rekordbox_db", ""), str(self._root(settings))]

    def _update(self, **updates):
        with self._lock:
            self._state.update(updates)
            self._state["revision"] += 1
            state = dict(self._state)
        with self.store.connect() as db:
            db.execute("INSERT OR REPLACE INTO rekordbox_backup_state VALUES (1,?)", (json.dumps(state),))

    def _records(self, limit=50):
        with self.store.connect() as db:
            return [json.loads(row[0]) for row in db.execute(
                "SELECT metadata FROM rekordbox_backups ORDER BY created_at DESC,id DESC LIMIT ?", (limit,))]

    def _save(self, record):
        with self.store.connect() as db:
            db.execute("INSERT OR REPLACE INTO rekordbox_backups VALUES (?,?,?)",
                       (record["id"], record["created_at"], json.dumps(record)))

    def _latest(self, source, root):
        with self.store.connect() as db:
            rows = db.execute("SELECT metadata FROM rekordbox_backups ORDER BY created_at DESC,id DESC")
            for row in rows:
                record = json.loads(row[0])
                if (record.get("source_path") == str(source) and record.get("backup_root") == str(root)
                        and record.get("verification") == "verified"):
                    return record
        return None

    def status(self):
        settings = self.store.settings()
        real = settings.get("mode", "real") == "real"
        with self._lock:
            state = dict(self._state)
        if not real:
            return {**state, "enabled": False, "running": False, "supported": self.supported,
                    "configured": False, "source_path": "", "backup_root": "", "interval_hours": 24,
                    "last_config": None, "last_attempt_config": None,
                    "last_backup_at": None, "next_due_at": None, "backups": [],
                    "message": "Real database backups are paused in the demo.", "error": "",
                    "limitations": list(LIMITATIONS)}
        source = settings.get("rekordbox_db", "")
        configured = bool(source)
        enabled = bool(settings.get("auto_rekordbox_backup", True))
        records = self._records()
        try:
            root = str(self._root(settings))
            selected_source = str(_absolute(source, "your active master.db")) if configured else ""
        except AppError as error:
            return {**state, "enabled": enabled, "supported": self.supported, "configured": False,
                    "source_path": source, "backup_root": settings.get("rekordbox_backup_root", ""),
                    "interval_hours": 24, "last_backup_at": None, "next_due_at": None,
                    "message": str(error), "error": str(error), "backups": records,
                    "limitations": list(LIMITATIONS)}
        latest = self._latest(selected_source, root) if configured else None
        config = [source, root]
        if state.get("last_attempt_config") != config:
            # A blocker from another database or destination must not appear
            # to describe the user's new selection. Preserve history on disk.
            state["error"] = ""
            state["message"] = "The selected database has a saved snapshot. Automatic checks run while DJList is open." if latest else ""
        checked = _timestamp(state.get("last_successful_check_at")) if state.get("last_config") == config else 0
        next_due = _iso(checked + INTERVAL_SECONDS) if checked else None
        message = state["message"]
        if not configured:
            message = "Choose your active master.db in Settings. No database is selected for backup."
        elif state["error"] and not state["running"]:
            message = state["error"]
        elif not enabled and not state["running"]:
            message = "Automatic backups are off. Use Back up now for a reviewed database snapshot."
        elif state.get("last_config") != config and not state["running"]:
            message = "The selected database is due for its first backup. Close Rekordbox and use Back up now, or leave DJList open."
        return {**state, "enabled": enabled, "supported": self.supported, "configured": configured,
                "source_path": source, "backup_root": root, "interval_hours": 24,
                "last_backup_at": latest.get("created_at") if latest else None,
                "next_due_at": next_due, "message": message, "backups": records,
                "limitations": list(LIMITATIONS)}

    def start(self):
        if self._thread is None and not self._stop.is_set():
            self._thread = threading.Thread(target=self._run, name="djlist-rekordbox-backups", daemon=True)
            self._thread.start()

    def close(self):
        self._stop.set()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=3)

    def _run(self):
        while not self._stop.wait(self.interval):
            try:
                self.tick()
            except Exception:
                # No private process arguments or paths are logged. Keep the
                # scheduler alive; public status describes the failed attempt.
                with self._lock:
                    self._state.update(running=False, error="Automatic backup could not finish.",
                                       message="Automatic backup could not finish. Check backup status and folder access.")
                    self._state["revision"] += 1

    def tick(self):
        if self._stop.is_set():
            return
        settings = self.store.settings()
        if settings.get("mode", "real") != "real" or not settings.get("auto_rekordbox_backup", True) or not settings.get("rekordbox_db"):
            return
        config = self._config(settings)
        with self._lock:
            previous = _timestamp(self._state.get("last_successful_check_at")) if self._state.get("last_config") == config else 0
        if previous and self.clock() - previous < INTERVAL_SECONDS:
            return
        try:
            return self.create(reason="automatic")
        except AppError:
            return None

    def _check_processes(self):
        try:
            result = self.process_checker()
        except Exception:
            result = None
        if not isinstance(result, dict) or result.get("known") is not True or not isinstance(result.get("running"), list):
            raise AppError("Could not verify that Rekordbox and library tools are closed. Backup is paused.", 409)
        if result["running"]:
            raise AppError("Close Rekordbox, CueGen and Library Fixer before backing up. DJList will try again while it stays open.", 409)

    def _check_config(self, expected, automatic=False):
        if self._stop.is_set():
            raise AppError("DJList is stopping. The unfinished backup will not be marked verified.", 409)
        current = self.store.settings()
        if (current.get("mode", "real") != "real" or self._config(current) != expected
                or (automatic and not current.get("auto_rekordbox_backup", True))):
            raise AppError("Backup settings changed during this operation. Check the paths and try again.", 409)

    @contextmanager
    def _guard(self):
        if self._stop.is_set():
            raise AppError("DJList is stopping; no new backup action can start.", 409)
        if not self._operation.acquire(blocking=False):
            raise AppError("Another backup action is running. Try again shortly.", 409)
        acquired = False
        try:
            acquired = self.work_lock.acquire(blocking=False)
            if not acquired:
                raise AppError("Another library action is running. Backups will wait; try again shortly.", 409)
            if self.store.settings().get("mode", "real") != "real":
                raise AppError("Leave the demo before accessing real database backups.", 409)
            if not self.supported:
                raise AppError("Database backups require the local macOS app and supported read-only file locking.", 409)
            yield
        finally:
            if acquired:
                self.work_lock.release()
            self._operation.release()

    def _check_budget(self, deadline):
        if self._stop.is_set() or time.monotonic() > deadline:
            raise AppError("The backup was interrupted or exceeded its time limit. It was not marked verified; previous backups are kept.", 409)

    def _hash(self, descriptor, expected_size, deadline):
        digest, size = hashlib.sha256(), 0
        os.lseek(descriptor, 0, os.SEEK_SET)
        while True:
            self._check_budget(deadline)
            chunk = os.read(descriptor, CHUNK_BYTES)
            if not chunk:
                break
            size += len(chunk)
            if size > expected_size:
                raise AppError("A file changed during backup verification. No successful snapshot was recorded.", 409)
            digest.update(chunk)
        if size != expected_size:
            raise AppError("A file changed during backup verification. No successful snapshot was recorded.", 409)
        return digest.hexdigest()

    def _copy(self, source_fd, destination_fd, name, info, deadline):
        digest, size = hashlib.sha256(), 0
        target = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=destination_fd)
        try:
            while True:
                self._check_budget(deadline)
                chunk = os.read(source_fd, CHUNK_BYTES)
                if not chunk:
                    break
                size += len(chunk)
                if size > info.st_size:
                    raise AppError("The database changed while copying. The incomplete backup is not verified.", 409)
                digest.update(chunk)
                view = memoryview(chunk)
                while view:
                    written = os.write(target, view)
                    if written <= 0:
                        raise OSError("Short backup write")
                    view = view[written:]
            os.fsync(target)
            if size != info.st_size:
                raise AppError("The database changed while copying. The incomplete backup is not verified.", 409)
        finally:
            os.close(target)
        copied_hash = digest.hexdigest()
        if self._hash(source_fd, size, deadline) != copied_hash or _fingerprint(os.fstat(source_fd)) != _fingerprint(info):
            raise AppError("The source changed during backup. The incomplete copy is not verified.", 409)
        target = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0), dir_fd=destination_fd)
        try:
            target_info = os.fstat(target)
            if (not stat.S_ISREG(target_info.st_mode) or target_info.st_nlink != 1 or target_info.st_size != size
                    or _identity(target_info) == _identity(info) or self._hash(target, size, deadline) != copied_hash):
                raise AppError("Backup verification failed. The source and earlier backups have not been changed.", 409)
        finally:
            os.close(target)
        return {"name": name, "size": size, "sha256": copied_hash}

    def _verify_record(self, record, deadline, allow_pending=False):
        backup_id = record.get("id", "")
        if (not BACKUP_ID.fullmatch(backup_id) or not record.get("manifest_sha256")
                or (not allow_pending and record.get("completed") is not True)):
            raise AppError("This backup is incomplete and cannot be verified. Keep using an earlier verified snapshot.", 409)
        root = _absolute(record.get("backup_root", ""), "the saved backup folder")
        files = record.get("files")
        if (not isinstance(files, list) or not 1 <= len(files) <= 2
                or {f.get("name") for f in files} not in ({"master.db"}, FILE_NAMES)):
            raise AppError("The backup record is invalid and cannot be verified.", 409)
        with _directory(root) as root_fd:
            folder = os.open(backup_id, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
            try:
                manifest = os.open("manifest.json", os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0), dir_fd=folder)
                try:
                    info = os.fstat(manifest)
                    if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= 65536 or info.st_nlink != 1:
                        raise AppError("The backup manifest is unavailable or invalid.", 409)
                    if self._hash(manifest, info.st_size, deadline) != record["manifest_sha256"]:
                        raise AppError("The backup manifest has changed. This snapshot is not verified.", 409)
                    if _fingerprint(os.stat("manifest.json", dir_fd=folder, follow_symlinks=False)) != _fingerprint(info):
                        raise AppError("The backup manifest changed during verification.", 409)
                finally:
                    os.close(manifest)
                for item in files:
                    name, size, expected = item["name"], item.get("size"), item.get("sha256")
                    if (name not in FILE_NAMES or not isinstance(size, int) or not 0 < size <= MAX_DATABASE_BYTES
                            or not isinstance(expected, str) or not re.fullmatch(r"[a-f0-9]{64}", expected)):
                        raise AppError("The backup record is invalid and cannot be verified.", 409)
                    descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0), dir_fd=folder)
                    try:
                        before = os.fstat(descriptor)
                        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size != size:
                            raise AppError("A backup file is missing, changed or linked elsewhere. This snapshot is not verified.", 409)
                        if self._hash(descriptor, size, deadline) != expected:
                            raise AppError("A backup checksum no longer matches. This snapshot is not verified.", 409)
                        if (_fingerprint(os.fstat(descriptor)) != _fingerprint(before)
                                or _fingerprint(os.stat(name, dir_fd=folder, follow_symlinks=False)) != _fingerprint(before)):
                            raise AppError("A backup file changed during verification. Try again after checking its folder.", 409)
                    finally:
                        os.close(descriptor)
                _directory_current(root, root_fd)
                if _identity(os.stat(backup_id, dir_fd=root_fd, follow_symlinks=False)) != _identity(os.fstat(folder)):
                    raise AppError("The backup folder changed during verification.", 409)
            finally:
                os.close(folder)
        return True

    def create(self, reason="manual"):
        if reason not in {"manual", "automatic"}:
            raise AppError("Choose a manual or automatic database backup.")
        with self._guard():
            settings = self.store.settings()
            source = _absolute(settings.get("rekordbox_db", ""), "your active master.db")
            if source.name != "master.db":
                raise AppError("Select the active Rekordbox file named master.db.", 409)
            root = self._root(settings)
            expected = self._config(settings)
            record = None
            try:
                self._update(running=True, last_checked_at=_iso(self.clock()), last_attempt_config=expected,
                             error="", message="Checking the closed database before making a new backup…")
                if source.parent == root or source.parent in root.parents or root in source.parents:
                    raise AppError("Keep backups in a separate folder outside the Rekordbox database folder.", 409)
                self._check_config(expected, automatic=reason == "automatic")
                self._check_processes()
                with _directory(source.parent) as source_dir:
                    before = _regular(source_dir, "master.db", MAX_DATABASE_BYTES)
                    # Never share a POSIX lock inode with DJList's own SQLite
                    # connection; closing another descriptor would drop locks.
                    if _identity(before) == _identity(self.store.path.stat()):
                        raise AppError("Choose the Rekordbox database, separate from DJList's own records.", 409)
                    descriptor = os.open("master.db", os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0), dir_fd=source_dir)
                    try:
                        if _fingerprint(os.fstat(descriptor)) != _fingerprint(before):
                            raise AppError("The selected database changed before backup could start.", 409)
                        try:
                            # Shared locks across pending, reserved and shared
                            # bytes block cooperating rollback writers, without
                            # a writable file descriptor or any SQLite PRAGMA.
                            fcntl.lockf(descriptor, fcntl.LOCK_SH | fcntl.LOCK_NB,
                                        SQLITE_LOCK_BYTES, SQLITE_PENDING_BYTE, os.SEEK_SET)
                        except OSError:
                            raise AppError("The database is busy or its filesystem cannot provide the required read lock. Close library tools and try again.", 409) from None
                        journals = _journals(source_dir)
                        companion = _regular(source_dir, "masterPlaylists6.xml", MAX_COMPANION_BYTES, optional=True)
                        signature = {"master.db": _fingerprint(before)}
                        if companion:
                            signature["masterPlaylists6.xml"] = _fingerprint(companion)
                        self._check_processes()
                        latest = self._latest(source, root)
                        deadline = time.monotonic() + COPY_SECONDS
                        if latest and latest.get("source_signature") == signature:
                            try:
                                self._verify_record(latest, deadline)
                            except (AppError, OSError):
                                latest.update(verification="failed", verified_at=_iso(self.clock()), message="The previous backup could not be verified; making a new snapshot.")
                                self._save(latest)
                            else:
                                self._assert_source(source, source_dir, descriptor, before, signature, journals)
                                self._check_processes()
                                self._check_config(expected, automatic=reason == "automatic")
                                latest.update(verified_at=_iso(self.clock()), message="Checksums match. Source unchanged since this snapshot.")
                                self._save(latest)
                                self._update(running=False, last_successful_check_at=_iso(self.clock()), last_config=expected,
                                             message="Database unchanged; the existing backup's checksums were verified again.", error="")
                                return {"created": False, "unchanged": True, "backup": latest,
                                        "message": "Database unchanged. The existing backup was verified; no duplicate snapshot was created."}
                        with _directory(root, create=True, forbidden_identity=_identity(os.fstat(source_dir))) as root_fd:
                            disk = os.fstatvfs(root_fd)
                            total = before.st_size + (companion.st_size if companion else 0)
                            if disk.f_bavail * disk.f_frsize < total + RESERVE_BYTES:
                                raise AppError("There is not enough free space for a verified database backup. Previous backups are kept.", 409)
                            same_device = os.fstat(root_fd).st_dev == before.st_dev
                            backup_id = datetime.fromtimestamp(self.clock(), timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + secrets.token_hex(8)
                            os.mkdir(backup_id, 0o700, dir_fd=root_fd)
                            folder = os.open(backup_id, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
                            try:
                                record = {"id": backup_id, "created_at": _iso(self.clock()), "backup_root": str(root),
                                          "path": str(root / backup_id), "source_path": str(source),
                                          "source_signature": signature, "source_fingerprint": _fingerprint(before),
                                          "reason": reason, "scope": "database_snapshot", "bytes": total,
                                          "same_filesystem": same_device, "verification": "failed", "verified_at": None,
                                          "completed": False,
                                          "message": "Incomplete backup. Do not use this snapshot.", "files": [],
                                          "warnings": ["Backup is on the same filesystem as the source. Choose another drive for independent protection."] if same_device else []}
                                self._save(record)
                                self._update(message="Copying and comparing database bytes. Keep Rekordbox closed…")
                                record["files"].append(self._copy(descriptor, folder, "master.db", before, deadline))
                                if companion:
                                    extra = os.open("masterPlaylists6.xml", os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0), dir_fd=source_dir)
                                    try:
                                        if _fingerprint(os.fstat(extra)) != _fingerprint(companion):
                                            raise AppError("The companion playlist file changed during backup.", 409)
                                        record["files"].append(self._copy(extra, folder, "masterPlaylists6.xml", companion, deadline))
                                    finally:
                                        os.close(extra)
                                self._assert_source(source, source_dir, descriptor, before, signature, journals)
                                _directory_current(root, root_fd)
                                if _identity(os.stat(backup_id, dir_fd=root_fd, follow_symlinks=False)) != _identity(os.fstat(folder)):
                                    raise AppError("The new backup folder changed during copying.", 409)
                                self._check_processes()
                                self._check_config(expected, automatic=reason == "automatic")
                                manifest = {"format": "djlist-rekordbox-database-backup", "schema_version": 1,
                                            **{key: record[key] for key in ("id", "created_at", "source_path", "source_signature", "reason", "scope", "files")},
                                            "verification": "sha256-byte-comparison", "limitations": list(LIMITATIONS)}
                                content = (json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
                                target = os.open("manifest.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=folder)
                                try:
                                    view = memoryview(content)
                                    while view:
                                        amount = os.write(target, view)
                                        if amount <= 0:
                                            raise OSError("Short manifest write")
                                        view = view[amount:]
                                    os.fsync(target)
                                finally:
                                    os.close(target)
                                os.fsync(folder)
                                os.fsync(root_fd)
                                record["manifest_sha256"] = hashlib.sha256(content).hexdigest()
                                self._verify_record(record, deadline, allow_pending=True)
                                self._assert_source(source, source_dir, descriptor, before, signature, journals)
                                self._check_processes()
                                self._check_config(expected, automatic=reason == "automatic")
                                record.update(completed=True, verification="verified", verified_at=_iso(self.clock()),
                                              message="Copied bytes and SHA-256 checksums match. Full-library restore has not been tested.")
                                self._save(record)
                            finally:
                                os.close(folder)
                    finally:
                        # The only source close also releases its POSIX locks.
                        os.close(descriptor)
                self._update(running=False, last_successful_check_at=_iso(self.clock()), last_config=expected,
                             error="", message="Database snapshot created and byte-verified. Existing backups are kept.")
                return {"created": True, "unchanged": False, "backup": record,
                        "message": "Database snapshot created and byte-verified. It does not include analysis files or music."}
            except Exception as error:
                message = str(error) if isinstance(error, AppError) else "Backup could not finish. Check the source drive, folder access and free space. Previous backups are kept."
                if record:
                    record.update(completed=False, verification="failed", verified_at=None, message="Incomplete backup; do not use. " + message)
                    try:
                        self._save(record)
                    except Exception:
                        pass  # An unavailable DJList DB cannot mark success.
                try:
                    self._update(running=False, error=message, message=message)
                except Exception:
                    pass  # In-memory failure status is set before persistence.
                if isinstance(error, AppError):
                    raise
                raise AppError(message, 409) from None

    def _assert_source(self, source, directory_fd, descriptor, before, signature, journals):
        _directory_current(source.parent, directory_fd)
        if (_fingerprint(os.fstat(descriptor)) != _fingerprint(before)
                or _fingerprint(os.stat("master.db", dir_fd=directory_fd, follow_symlinks=False)) != _fingerprint(before)):
            raise AppError("The selected database changed during backup. The incomplete copy is not verified.", 409)
        companion = _regular(directory_fd, "masterPlaylists6.xml", MAX_COMPANION_BYTES, optional=True)
        if (_fingerprint(companion) if companion else None) != signature.get("masterPlaylists6.xml"):
            raise AppError("The companion playlist file changed during backup. The incomplete copy is not verified.", 409)
        if _journals(directory_fd) != journals:
            raise AppError("Database journal state changed during backup. Close Rekordbox and try again.", 409)

    def verify(self, backup_id):
        if not isinstance(backup_id, str) or not BACKUP_ID.fullmatch(backup_id):
            raise AppError("Choose a backup listed by DJList.")
        with self._guard():
            expected = self._config(self.store.settings())
            with self.store.connect() as db:
                row = db.execute("SELECT metadata FROM rekordbox_backups WHERE id=?", (backup_id,)).fetchone()
            if not row:
                raise AppError("This backup is not listed in DJList.", 404)
            record = json.loads(row[0])
            checked_at = _iso(self.clock())
            try:
                self._update(running=True, last_attempt_config=expected,
                             message="Checking the selected backup's stored checksums…", error="")
                try:
                    self._verify_record(record, time.monotonic() + COPY_SECONDS)
                    self._check_config(expected)
                    message = "Backup checksums match. This verifies copied bytes, not a full Rekordbox restore."
                    verified = True
                except (AppError, OSError, ValueError, RuntimeError) as error:
                    message = str(error) if isinstance(error, AppError) else "The backup is missing or unreadable. It is not verified; other backups are kept."
                    verified = False
                record.update(verification="verified" if verified else "failed", verified_at=checked_at, message=message)
                self._save(record)
                self._update(running=False, error="" if verified else message, message=message)
                return {"id": backup_id, "verified": verified, "checked_at": checked_at,
                        "message": message, "files": record.get("files", [])}
            except Exception as error:
                message = str(error) if isinstance(error, AppError) else "DJList could not record the backup verification. No backup or source files have been changed. Try again after checking local storage."
                try:
                    self._update(running=False, error=message, message=message)
                except Exception:
                    pass  # In-memory status is reset even if DJList storage fails.
                if isinstance(error, AppError):
                    raise
                raise AppError(message, 409) from None
