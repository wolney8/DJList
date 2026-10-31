"""Reviewed, reversible audio renames and same-drive removal.

Only explicitly selected, indexed audio can move. Atomic no-replace filesystem
renames never overwrite a destination. Removed files remain in a private folder
on the same drive; there is no permanent deletion, source-content write or
Rekordbox database/XML write. DJList keeps a durable operation journal. An
interrupted operation requires review and never silently resumes on startup.
"""
from contextlib import contextmanager, ExitStack
from datetime import datetime, timedelta, timezone
import ctypes
import errno
import json
import os
from pathlib import Path
import re
import secrets
import stat
import sys
import threading
import time
import unicodedata

from library_inventory import _identity, cue_evidence
from metadata_review import fields as reviewed_fields
from rekordbox_backups import check_rekordbox_closed
from relocation import AUDIO_EXTENSIONS
from spotify_api import AppError

BATCH_LIMIT = 50
MAX_INDEX = 50000
PLAN_SECONDS = 600
REMOVED_DIRECTORY = ".djlist-removed"
MARKER_NAME = ".djlist-removed-v1"
MARKER_BYTES = b"DJList recoverable audio removal, format 1\n"
ID = re.compile(r"^[a-f0-9]{24}$")
OPERATION_ID = re.compile(r"^\d{8}T\d{6}Z-[a-f0-9]{16}$")
WARNING = "Rekordbox locations are unchanged. Relocate renamed files in Rekordbox, or remove the chosen record there, then export fresh XML."


def _now():
    # Retain sub-second order for quick rename/undo/remove sequences. Random
    # operation IDs are only a tie-breaker, not a chronological clock.
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _fingerprint(info):
    return {"device": info.st_dev, "inode": info.st_ino, "size": info.st_size,
            "mtime_ns": info.st_mtime_ns, "ctime_ns": info.st_ctime_ns,
            "mode": stat.S_IMODE(info.st_mode)}


def _same_content_identity(a, b):
    return all(a.get(key) == b.get(key) for key in ("device", "inode", "size", "mtime_ns", "mode"))


def _metadata_identity(entry):
    return {key: entry.get(key) for key in ("path", "title", "artist", "source", "cue_count", "identity_source",
                                           "xml_cue_evidence", "xml_audio_fingerprint", "_reviewed_override")}


def _dir_identity(info):
    return [info.st_dev, info.st_ino]


def _absolute(value):
    if not isinstance(value, str) or not value or len(value) > 4096 or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise AppError("Choose a valid selected audio folder in Settings.", 409)
    path = Path(value).expanduser()
    if not path.is_absolute() or ".." in path.parts or path == Path(path.anchor):
        raise AppError("Choose a specific absolute audio folder in Settings.", 409)
    return path


@contextmanager
def _directory(path):
    """Walk every component with no-follow descriptors, including ancestors."""
    descriptors = []
    try:
        descriptor = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        descriptors.append(descriptor)
        for part in path.parts[1:]:
            descriptor = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            descriptors.append(descriptor)
        yield descriptor
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _regular(descriptor, name):
    info = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise AppError("A selected file is not a regular, single-link audio file. Symlinks and hardlinks cannot be moved.", 409)
    return info


def _absent(descriptor, name):
    try:
        os.stat(name, dir_fd=descriptor, follow_symlinks=False)
    except FileNotFoundError:
        return
    raise AppError("A destination already exists. Nothing will be overwritten; choose another name.", 409)


def _rename_exclusive(source_fd, source_name, target_fd, target_name):
    """Atomic no-replace rename; never fall back to a replacing rename()."""
    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform == "darwin":
        function = getattr(libc, "renameatx_np", None)
        flag = 0x00000004  # Apple's public RENAME_EXCL (bsd/sys/stdio.h).
    elif sys.platform.startswith("linux"):
        function = getattr(libc, "renameat2", None)
        flag = 1  # Linux RENAME_NOREPLACE.
    else:
        function = None
    if function is None:
        raise AppError("This system cannot guarantee a rename without overwriting. No files were moved.", 409)
    function.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    function.restype = ctypes.c_int
    if function(source_fd, os.fsencode(source_name), target_fd, os.fsencode(target_name), flag):
        error = ctypes.get_errno()
        if error in (errno.EEXIST, errno.ENOTEMPTY):
            raise AppError("A destination appeared during review. Nothing was overwritten.", 409)
        if error in (errno.EINVAL, errno.ENOSYS, errno.ENOTSUP, errno.EXDEV):
            raise AppError("This drive cannot perform the required same-drive, no-overwrite rename. No fallback copy or deletion was attempted.", 409)
        raise OSError(error, os.strerror(error))


def _configuration(settings):
    return {key: settings.get(key, "real" if key == "mode" else "")
            for key in ("mode", "music_root", "download_root", "rekordbox_db")}


def _selection(values):
    if (not isinstance(values, list) or not 1 <= len(values) <= BATCH_LIMIT
            or any(not isinstance(value, str) or not ID.fullmatch(value) for value in values)
            or len(set(values)) != len(values)):
        raise AppError("Select between 1 and 50 different files from the current review.")
    return values


def _filename(value, extension):
    if (not isinstance(value, str) or not value.strip() or value != value.strip()
            or len(os.fsencode(value)) > 240 or value.startswith(".")
            or value.endswith((".", " ")) or any(ord(c) < 32 or ord(c) == 127 for c in value)
            or any(c in value for c in '/\\:') or value in {".", ".."}
            or Path(value).suffix.lower() != extension.lower()
            or value.casefold().startswith("master.db")):
        raise AppError("Use a filename of 240 bytes or fewer, keep its audio extension, and omit folders or reserved names.")
    return value


def _clean_component(value):
    value = re.sub(r'[\\/:*?"<>|]', " - ", value)
    value = re.sub(r"\s+", " ", value).strip(" .")
    return value


def _proposed_name(entry, options):
    path = Path(entry["path"])
    explicit = options.get("names", {}).get(entry["id"])
    if explicit is not None:
        return _filename(explicit, path.suffix)
    if options["mode"] == "artist_title":
        if not entry.get("artist", "").strip() or not entry.get("title", "").strip():
            raise AppError("Artist and title are required for the Artist - Title naming pattern. Review metadata first.")
        stem = _clean_component(entry["artist"]) + " - " + _clean_component(entry["title"])
    else:
        stem = path.stem
        if options["strip_underscores"]:
            stem = re.sub(r"^_+\s*", "", stem)
        if options["strip_track_numbers"]:
            stem = re.sub(r"^\d{1,3}[\s._-]+", "", stem)
        stem = stem.strip()
    return _filename(stem + path.suffix, path.suffix)


class FileOperations:
    def __init__(self, store, library, process_checker=None, clock=time.monotonic):
        self.store, self.library, self.clock = store, library, clock
        self.process_checker = process_checker or check_rekordbox_closed
        self.lock = threading.RLock()
        self.plan = None
        with self.store.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS file_operations (
                id TEXT PRIMARY KEY, created_at TEXT NOT NULL, metadata TEXT NOT NULL)""")

    def clear(self):
        with self.lock:
            self.plan = None

    @staticmethod
    def _real(settings):
        if settings.get("mode", "real") != "real":
            raise AppError("Leave demo mode before changing real audio files.", 409)

    def _processes(self):
        try:
            result = self.process_checker()
        except Exception:
            result = None
        if not isinstance(result, dict) or result.get("known") is not True or not isinstance(result.get("running"), list):
            raise AppError("DJList cannot verify that Rekordbox and library tools are closed. File changes are paused.", 409)
        if result["running"]:
            raise AppError("Close Rekordbox, CueGen and Library Fixer before renaming or removing audio files.", 409)

    def _roots(self, settings):
        self._real(settings)
        roots = []
        for key in ("music_root", "download_root"):
            value = settings.get(key, "")
            if not value:
                continue
            path = _absolute(value)
            try:
                with _directory(path) as descriptor:
                    identity = _dir_identity(os.fstat(descriptor))
            except OSError:
                continue  # An offline unrelated root does not block an online selected file.
            if not any(root["path"] == str(path) for root in roots):
                roots.append({"setting": key, "path": str(path), "identity": identity})
        return sorted(roots, key=lambda root: -len(Path(root["path"]).parts))

    @staticmethod
    def _context(path, roots, allow_removed=False):
        path = _absolute(path)
        if path.suffix.lower() not in AUDIO_EXTENSIONS or path.name.casefold().startswith("master.db"):
            raise AppError("Only indexed audio files can be renamed or moved to Removed.", 409)
        for root in roots:
            try:
                relative = path.relative_to(root["path"])
            except ValueError:
                continue
            if not relative.parts or (not allow_removed and REMOVED_DIRECTORY in relative.parts):
                raise AppError("Removed files can only be restored from File history.", 409)
            return root
        raise AppError("A selected file is outside an available configured audio folder. Reconnect its drive and refresh the library.", 409)

    @staticmethod
    def _inspect(path, root):
        try:
            with _directory(Path(root["path"])) as descriptor:
                if _dir_identity(os.fstat(descriptor)) != root["identity"]:
                    raise AppError("The selected audio folder changed. Make a fresh review.", 409)
            with _directory(Path(path).parent) as descriptor:
                info = _regular(descriptor, Path(path).name)
                return _fingerprint(info), _dir_identity(os.fstat(descriptor))
        except OSError:
            raise AppError("A selected file is unavailable or passes through an unsafe folder. Refresh the library and review again.", 409) from None

    def _entries(self, ids):
        """Resolve opaque IDs against bounded saved paths, without probing every file."""
        with self.store.connect() as db:
            paths = [row[0] for row in db.execute("SELECT path FROM library_entries UNION SELECT path FROM files ORDER BY path LIMIT ?", (MAX_INDEX + 1,))]
            if len(paths) > MAX_INDEX:
                raise AppError("File changes support up to 50,000 indexed locations. Reduce the selected library first.", 413)
            selected = [path for path in paths if _identity(path) in ids]
            if len(selected) != len(ids):
                raise AppError("A selected library entry changed. Refresh the library and make a new review.", 409)
            entries = []
            for path in selected:
                xml = db.execute("SELECT metadata FROM library_entries WHERE path=?", (path,)).fetchone()
                if xml:
                    entry = json.loads(xml[0])
                    if not isinstance(entry, dict) or entry.get("path") != path:
                        raise AppError("Saved XML metadata is invalid. Refresh XML before changing files.", 409)
                else:
                    row = db.execute("SELECT * FROM files WHERE path=?", (path,)).fetchone()
                    entry = self.library._from_file(dict(row))
                    tags = db.execute("SELECT metadata FROM media_metadata WHERE path=?", (path,)).fetchone()
                    if tags:
                        metadata = json.loads(tags[0])
                        if isinstance(metadata, dict) and metadata.get("status") == "read":
                            entry = self.library._from_file(dict(row), metadata)
                entry = dict(entry, id=_identity(path))
                override = db.execute("SELECT metadata,fingerprint FROM library_metadata_overrides WHERE path=?", (path,)).fetchone()
                entry["_reviewed_override"] = dict(override) if override else None
                if any(not isinstance(entry.get(key, ""), str) for key in ("title", "artist")):
                    raise AppError("Saved metadata is invalid. Refresh media before changing files.", 409)
                entries.append(entry)
            return entries

    @staticmethod
    def _enrich(entry, fingerprint):
        entry = dict(entry)
        override = entry.get("_reviewed_override")
        if override:
            try:
                saved, expected = json.loads(override["metadata"]), json.loads(override["fingerprint"])
                if expected == {key: value for key, value in fingerprint.items() if key != "mode"}:
                    entry.update(reviewed_fields(saved), identity_source="djlist_reviewed")
            except (ValueError, TypeError, AttributeError):
                raise AppError("The saved metadata correction is invalid. Review metadata before changing this file.", 409) from None
        return entry

    @staticmethod
    def _options(action, options):
        if options is None:
            options = {}
        if not isinstance(options, dict) or set(options) - {"mode", "strip_underscores", "strip_track_numbers", "names"}:
            raise AppError("Choose valid filename review options.")
        if action == "remove":
            if options:
                raise AppError("Removal does not use filename options.")
            return {}
        result = {"mode": "clean", "strip_underscores": True, "strip_track_numbers": False, "names": {}, **options}
        if (not isinstance(result["mode"], str) or result["mode"] not in {"clean", "artist_title"}
                or type(result["strip_underscores"]) is not bool or type(result["strip_track_numbers"]) is not bool
                or not isinstance(result["names"], dict) or len(result["names"]) > BATCH_LIMIT
                or any(not isinstance(key, str) or not ID.fullmatch(key) for key in result["names"])):
            raise AppError("Choose valid filename review options.")
        return result

    def _public_plan(self, plan):
        return {"token": plan["token"], "expires_at": plan["expires_at"], "action": plan["action"],
                "operation_id": plan["operation_id"], "items": [{key: value for key, value in item.items() if not key.startswith("_")}
                                                               for item in plan["items"]],
                "summary": {"files": len(plan["items"]), "selectable": sum(item["selectable"] for item in plan["items"])},
                "message": WARNING, "limit": BATCH_LIMIT}

    def _plan(self, action, settings, operation_id, items, undo_id=None):
        self.plan = {"token": secrets.token_urlsafe(32), "deadline": self.clock() + PLAN_SECONDS,
                     "expires_at": (datetime.now(timezone.utc) + timedelta(seconds=PLAN_SECONDS)).isoformat(timespec="seconds"),
                     "action": action, "configuration": _configuration(settings), "operation_id": operation_id,
                     "items": items, "undo_id": undo_id}
        return self._public_plan(self.plan)

    def preview(self, action, entry_ids, options=None):
        if not isinstance(action, str) or action not in {"rename", "remove"}:
            raise AppError("Choose rename or move to Removed.")
        ids = _selection(entry_ids)
        options = self._options(action, options)
        if set(options.get("names", {})) - set(ids):
            raise AppError("Filename overrides must refer to the selected files.")
        with self.lock:
            settings = self.store.settings()
            roots = self._roots(settings)
            entries = self._entries(ids)
            operation_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + secrets.token_hex(8)
            items = []
            for entry in entries:
                path = Path(entry["path"])
                root = self._context(str(path), roots)
                fingerprint, parent_identity = self._inspect(str(path), root)
                entry = self._enrich(entry, fingerprint)
                target = (Path(root["path"]) / REMOVED_DIRECTORY / operation_id / (entry["id"][:8] + "-" + path.name)
                          if action == "remove" else path.with_name(_proposed_name(entry, options)))
                if len(os.fsencode(target.name)) > 255:
                    raise AppError("This filename is too long for the recoverable Removed folder. Review a shorter name first.")
                warnings = [WARNING]
                evidence = cue_evidence(entry, {"available": True, "fingerprint": {key: value for key, value in fingerprint.items() if key != "mode"}}, self.library._snapshot())
                cues = entry.get("cue_count")
                cues = cues if isinstance(cues, int) and not isinstance(cues, bool) and cues >= 0 else None
                if cues:
                    warnings.append("This XML snapshot contains %d cue/loop markers for this file. Removing it can leave those markers without audio." % cues)
                elif entry.get("source") == "rekordbox_xml":
                    warnings.append("No cue/loop markers appear in this XML snapshot. Unexported Rekordbox changes are unknown.")
                else:
                    warnings.append("Cue information is unknown for this file; there is no matching XML record.")
                if evidence.get("stale"):
                    warnings.append(evidence["message"])
                if action == "rename" and options.get("strip_track_numbers"):
                    warnings.append("Leading numbers may be part of a song title. Check the proposed filename.")
                selectable = target != path
                if not selectable:
                    warnings.append("The proposed filename is unchanged.")
                if action == "rename" and target != path:
                    try:
                        with _directory(target.parent) as descriptor:
                            _absent(descriptor, target.name)
                    except AppError:
                        selectable = False
                        warnings.append("The destination already exists; it will not be overwritten.")
                items.append({"entry_id": entry["id"], "title": entry.get("title", path.stem),
                              "artist": entry.get("artist", ""), "before": str(path), "after": str(target),
                              "cue_count": cues, "cue_evidence": evidence, "selectable": selectable, "warnings": warnings,
                              "_root": root, "_fingerprint": fingerprint, "_parent_identity": parent_identity,
                              "_metadata_identity": _metadata_identity(entry),
                              "_target_parent_identity": parent_identity if action == "rename" else None})
            destinations = {}
            for item in items:
                key = unicodedata.normalize("NFC", item["after"]).casefold()
                if key in destinations:
                    for value in (item, destinations[key]):
                        value["selectable"] = False
                        value["warnings"].append("More than one selection would use this filename. Choose different names.")
                destinations[key] = item
            if _configuration(self.store.settings()) != _configuration(settings):
                raise AppError("Folder settings changed. Make a fresh file review.", 409)
            return self._plan(action, settings, operation_id, items)

    def _load_record(self, identity):
        if not isinstance(identity, str) or not OPERATION_ID.fullmatch(identity):
            raise AppError("Choose a valid file operation from history.")
        with self.store.connect() as db:
            row = db.execute("SELECT metadata FROM file_operations WHERE id=?", (identity,)).fetchone()
        if row is None:
            raise AppError("This file operation is no longer in history.", 404)
        record = json.loads(row[0])
        if not isinstance(record, dict) or record.get("id") != identity:
            raise AppError("The saved operation record is invalid. Files have not been changed.", 409)
        return record

    def _save(self, record):
        with self.store.connect() as db:
            db.execute("INSERT OR REPLACE INTO file_operations VALUES (?,?,?)", (record["id"], record["created_at"], json.dumps(record)))

    def history(self):
        self._real(self.store.settings())
        with self.store.connect() as db:
            rows = list(db.execute("SELECT metadata FROM file_operations ORDER BY created_at DESC,id DESC LIMIT 100"))
        result = []
        for row in rows:
            record = json.loads(row[0])
            record = dict(record)
            record["can_undo"] = record.get("status") == "completed" and record.get("action") != "undo" and any(not item.get("undone") for item in record.get("items", []))
            record["items"] = [{key: value for key, value in item.items() if not key.startswith("_")} for item in record.get("items", [])]
            record.pop("configuration", None)
            result.append(record)
        return {"operations": result, "message": "Removed audio stays on its original drive. History never permanently deletes audio.", "limit": 100}

    def undo_preview(self, operation_id):
        with self.lock:
            settings = self.store.settings()
            self._real(settings)
            record = self._load_record(operation_id)
            if record.get("action") == "undo" or record.get("status") != "completed":
                raise AppError("This operation needs manual recovery review or cannot be undone again. No files were changed.", 409)
            if _configuration(settings) != record.get("configuration"):
                raise AppError("Folder settings changed since this operation. Restore its original folder settings before reviewing undo.", 409)
            roots = self._roots(settings)
            items = []
            for old in record["items"]:
                if old.get("undone"):
                    continue
                root = self._context(old["after"], roots, allow_removed=True)
                fingerprint, parent_identity = self._inspect(old["after"], root)
                if fingerprint != old.get("_after_fingerprint"):
                    raise AppError("A moved file changed after the operation. Undo is paused; inspect it manually.", 409)
                before, after = old["after"], old["before"]
                self._context(after, roots)
                with _directory(Path(after).parent) as descriptor:
                    _absent(descriptor, Path(after).name)
                    target_parent_identity = _dir_identity(os.fstat(descriptor))
                items.append({"entry_id": old["entry_id"], "title": old.get("title", ""), "artist": old.get("artist", ""),
                              "before": before, "after": after, "cue_count": old.get("cue_count"),
                              "selectable": True, "warnings": ["Restore only the reviewed file to its original location.", WARNING],
                              "_root": root, "_fingerprint": fingerprint, "_parent_identity": parent_identity,
                              "_target_parent_identity": target_parent_identity})
            if not items:
                raise AppError("All files from this operation have already been restored.", 409)
            identity = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + secrets.token_hex(8)
            return self._plan("undo", settings, identity, items, undo_id=operation_id)

    def undo_apply(self, token, selected_ids):
        return self._apply(token, selected_ids, undo=True)

    def apply(self, token, selected_ids):
        return self._apply(token, selected_ids, undo=False)

    @staticmethod
    def _removed_folder(root, operation_id):
        """Create only owned, exclusive quarantine descendants with no symlinks."""
        root_path = Path(root["path"])
        with _directory(root_path) as root_fd:
            if _dir_identity(os.fstat(root_fd)) != root["identity"]:
                raise AppError("The audio folder changed. Make a new review.", 409)
            created = False
            try:
                os.mkdir(REMOVED_DIRECTORY, 0o700, dir_fd=root_fd)
                created = True
            except FileExistsError:
                pass
            folder_fd = os.open(REMOVED_DIRECTORY, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
            try:
                if created:
                    marker = os.open(MARKER_NAME, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=folder_fd)
                    try:
                        os.write(marker, MARKER_BYTES)
                        os.fsync(marker)
                    finally:
                        os.close(marker)
                else:
                    marker = os.open(MARKER_NAME, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=folder_fd)
                    try:
                        info = os.fstat(marker)
                        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or os.read(marker, 256) != MARKER_BYTES:
                            raise AppError("The Removed folder is not a recognised DJList folder. Choose another root or inspect it manually.", 409)
                    finally:
                        os.close(marker)
                os.mkdir(operation_id, 0o700, dir_fd=folder_fd)
                os.fsync(folder_fd)
            finally:
                os.close(folder_fd)

    def _check_plan(self, plan):
        if _configuration(self.store.settings()) != plan["configuration"]:
            raise AppError("Folder settings changed after review. Nothing further was moved.", 409)

    @staticmethod
    def _update_links(db, action, items):
        # Fresh annotations are merged in the transaction; ownership/tags/notes
        # are preserved even when edited after the filename preview.
        replacements = {item["before"]: item["after"] for item in items} if action != "remove" else {}
        if replacements:
            rows = list(db.execute("SELECT id,annotations FROM tracks WHERE namespace='real' ORDER BY id LIMIT ?", (MAX_INDEX + 1,)))
            if len(rows) > MAX_INDEX:
                raise AppError("More than 50,000 track links need review. File changes are paused.", 413)
            for row in rows:
                annotation = json.loads(row["annotations"])
                if not isinstance(annotation, dict):
                    raise AppError("A saved track annotation is invalid. File changes are paused.", 409)
                target = replacements.get(annotation.get("local_file"))
                if target:
                    annotation["local_file"] = target
                    db.execute("UPDATE tracks SET annotations=? WHERE id=? AND namespace='real'", (json.dumps(annotation), row["id"]))
        # Files moved to Removed disappear from scan inventory. XML is retained
        # at its original locations to make the required Rekordbox repair visible.
        for item in items:
            db.execute("DELETE FROM files WHERE path=?", (item["before"],))
            db.execute("DELETE FROM media_metadata WHERE path=?", (item["before"],))
            if action != "remove":
                info = item["_after_fingerprint"]
                db.execute("""INSERT OR IGNORE INTO files(path,root,size,mtime,title,artist,origin)
                              VALUES (?,?,?,?,?,?,'scan')""",
                           (item["after"], item["_root"]["path"], info["size"], info["mtime_ns"] / 1e9, item.get("title", ""), item.get("artist", "")))

    def _apply(self, token, selected_ids, undo):
        with self.lock:
            if not isinstance(token, str) or not token.isascii() or len(token) > 128:
                raise AppError("Make a fresh file review before applying changes.", 409)
            plan = self.plan
            if plan is None or not secrets.compare_digest(token, plan["token"]):
                raise AppError("This review is no longer available. Make a fresh file review.", 409)
            self.plan = None
            if self.clock() >= plan["deadline"] or (plan["action"] == "undo") != undo:
                raise AppError("This review expired or is for another action. Make a fresh review.", 409)
            ids = _selection(selected_ids)
            items = [dict(item) for item in plan["items"] if item["entry_id"] in ids]
            if len(items) != len(ids) or any(not item["selectable"] for item in items):
                raise AppError("Only explicitly selected, available files from this review can change.")
            self._check_plan(plan)
            self._real(self.store.settings())
            self._processes()
            if not undo:
                current_entries = {entry["id"]: entry for entry in self._entries(ids)}
                if any(_metadata_identity(self._enrich(current_entries[item["entry_id"]], item["_fingerprint"])) != item["_metadata_identity"] for item in items):
                    raise AppError("Library metadata changed after review. Make a fresh review before changing files.", 409)
            for item in items:
                fp, parent = self._inspect(item["before"], item["_root"])
                if fp != item["_fingerprint"] or parent != item["_parent_identity"]:
                    raise AppError("A selected file changed after review. No files were moved.", 409)
            # Persist intent before touching source paths. An OS/process crash
            # leaves a discoverable recovery record, not an invented success.
            record = {"id": plan["operation_id"], "created_at": _now(), "action": plan["action"],
                      "status": "applying", "configuration": plan["configuration"], "items": items,
                      "undo_of": plan.get("undo_id"), "message": "File operation started; inspect history if interrupted."}
            self._save(record)
            moved = []
            committed = False
            try:
                if plan["action"] == "remove":
                    made = set()
                    for item in items:
                        if item["_root"]["path"] not in made:
                            self._removed_folder(item["_root"], plan["operation_id"])
                            made.add(item["_root"]["path"])
                with ExitStack() as stack:
                    pairs = []
                    for item in items:
                        source, target = Path(item["before"]), Path(item["after"])
                        source_fd = stack.enter_context(_directory(source.parent))
                        target_fd = stack.enter_context(_directory(target.parent))
                        if _dir_identity(os.fstat(source_fd)) != item["_parent_identity"]:
                            raise AppError("A source folder changed. No further files were moved.", 409)
                        if item.get("_target_parent_identity") is not None and _dir_identity(os.fstat(target_fd)) != item["_target_parent_identity"]:
                            raise AppError("A destination folder changed after review. No files were moved.", 409)
                        if os.fstat(source_fd).st_dev != os.fstat(target_fd).st_dev:
                            raise AppError("Files must stay on the same drive. No cross-drive copy or deletion is allowed.", 409)
                        _absent(target_fd, target.name)
                        pairs.append((item, source, target, source_fd, target_fd))
                    for item, source, target, source_fd, target_fd in pairs:
                        self._check_plan(plan)
                        self._processes()
                        fp, parent = self._inspect(str(source), item["_root"])
                        if fp != item["_fingerprint"] or parent != _dir_identity(os.fstat(source_fd)):
                            raise AppError("A source changed while applying the batch. Earlier moves will be rolled back where safe.", 409)
                        with _directory(target.parent) as current:
                            if _dir_identity(os.fstat(current)) != _dir_identity(os.fstat(target_fd)):
                                raise AppError("A destination folder changed. Earlier moves will be rolled back where safe.", 409)
                        _rename_exclusive(source_fd, source.name, target_fd, target.name)
                        moved.append((item, source, target, source_fd, target_fd))
                        after = _fingerprint(_regular(target_fd, target.name))
                        item["_after_fingerprint"] = after
                        if not _same_content_identity(fp, after):
                            raise AppError("A file changed during its move. Stop and inspect the operation history.", 409)
                        os.fsync(source_fd)
                        os.fsync(target_fd)
                        self._save(record)
                    self._check_plan(plan)
                    self._processes()
                    for item, source, target, source_fd, target_fd in moved:
                        fp, _ = self._inspect(str(target), item["_root"])
                        if fp != item["_after_fingerprint"]:
                            raise AppError("A moved file changed before completion. Inspect the file history.", 409)
                    with self.store.connect() as db:
                        db.execute("BEGIN IMMEDIATE")
                        current = {row["key"]: json.loads(row["value"]) for row in db.execute("SELECT key,value FROM settings")}
                        if _configuration(current) != plan["configuration"]:
                            raise AppError("Settings changed during the operation. Earlier moves will be rolled back where safe.", 409)
                        self._update_links(db, plan["action"], items)
                        if plan.get("undo_id"):
                            old_row = db.execute("SELECT metadata FROM file_operations WHERE id=?", (plan["undo_id"],)).fetchone()
                            old = json.loads(old_row[0])
                            for item in old["items"]:
                                if item["entry_id"] in ids:
                                    item["undone"] = True
                            if all(item.get("undone") for item in old["items"]):
                                old["status"] = "undone"
                            db.execute("UPDATE file_operations SET metadata=? WHERE id=?", (json.dumps(old), old["id"]))
                        record.update(status="completed", completed_at=_now(), message=WARNING)
                        db.execute("UPDATE file_operations SET metadata=? WHERE id=?", (json.dumps(record), record["id"]))
                    committed = True
                self.library.invalidate()
                return {"operation_id": record["id"], "action": plan["action"], "updated": len(items),
                        "items": [{key: value for key, value in item.items() if not key.startswith("_")} for item in items],
                        "message": ("Files moved to Removed and kept for recovery. " if plan["action"] == "remove" else "Reviewed file locations updated. ") + WARNING}
            except Exception as error:
                if committed:
                    # A cache/UI invalidation failure after the durable commit
                    # must not reverse files whose links are already committed.
                    raise AppError("The file operation was recorded, but the library view could not refresh. Check File history and refresh the library.", 409) from None
                # Descriptors from ExitStack may be closed here; reopen using the
                # recorded identities. Never overwrite an occupied original path.
                failed = []
                for item, source, target, _, _ in reversed(moved):
                    try:
                        current, _ = self._inspect(str(target), item["_root"])
                        if current != item.get("_after_fingerprint"):
                            raise AppError("Changed moved file")
                        with _directory(source.parent) as source_fd, _directory(target.parent) as target_fd:
                            if _dir_identity(os.fstat(source_fd)) != item["_parent_identity"]:
                                raise AppError("Changed original directory")
                            _absent(source_fd, source.name)
                            _rename_exclusive(target_fd, target.name, source_fd, source.name)
                            item["rolled_back"] = True
                            os.fsync(source_fd)
                            os.fsync(target_fd)
                    except Exception:
                        failed.append(item["entry_id"])
                record.update(status="needs_recovery" if failed else "rolled_back",
                              message=("Some files could not be safely returned. Inspect the before/after paths in history; no destination was overwritten."
                                       if failed else "The operation did not complete. Earlier moves were returned without overwriting files."),
                              failed_ids=failed)
                try:
                    self._save(record)
                except Exception:
                    pass  # The durable pre-operation record still exposes intent.
                self.library.invalidate()
                if failed:
                    raise AppError(record["message"], 409) from None
                if isinstance(error, AppError):
                    raise
                raise AppError("The file operation failed. Check File history before trying again; no existing destination was overwritten.", 409) from None
