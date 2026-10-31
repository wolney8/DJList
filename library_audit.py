"""Read-only library health, reviewed missing links and exact-file comparison.

Only DJList's records are updated. No audio or Rekordbox file is edited, moved,
removed or opened for writing. Metadata matches remain suggestions; SHA-256
compares complete file bytes, not decoded audio or different encodings.
"""
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import csv
import hashlib
import io
import json
import math
import os
from pathlib import Path
import secrets
import threading
import time

from audio_metadata import _fingerprint, _selected_file, file_fingerprint
from library_inventory import _duration, _normalise, _identity, _text
from relocation import _absolute, _relative, _root, _snapshot
from spotify_api import AppError


MAX_ENTRIES = 50000
MAX_CANDIDATES = 20
PLAN_SECONDS = 600
MAX_HASH_FILES = 20
MAX_HASH_BYTES = 2 * 1024 * 1024 * 1024
MAX_HASH_SECONDS = 20
HASH_CHUNK = 1024 * 1024
MAX_REPORT_ROWS = 10000
MAX_REPORT_BYTES = 8 * 1024 * 1024
KINDS = {"all", "missing", "offline", "duplicates", "outside_root", "unsafe"}


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _identifier(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


def _query(value):
    if (not isinstance(value, str) or len(value) > 500
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        raise AppError("Use a library health search of 500 characters or fewer.")
    return value.strip()


def _safe_cell(value):
    value = "" if value is None else str(value)
    return "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r", "\n")) else value


def _configuration(settings):
    return (settings.get("mode", "real"), settings.get("music_root", ""), settings.get("download_root", ""))


def _context(value, name):
    context = {"name": name, "setting": value, "available": False,
               "state": "not_set", "raw": None, "root": None, "identity": None}
    if not value:
        return context
    try:
        raw = _absolute(str(Path(value).expanduser()), "the selected folder")
        if raw == Path(raw.anchor) or raw.is_symlink():
            context.update(state="unsafe", raw=raw, root=raw)
            return context
        context.update(raw=raw, root=raw.resolve(strict=False), state="offline")
        raw, root, identity = _root(value)
        context.update(raw=raw, root=root, identity=identity, available=True, state="available")
    except (AppError, OSError, ValueError, TypeError, RuntimeError):
        if context["raw"] is None:
            context["state"] = "unsafe"
    return context


def _location_status(path, contexts):
    """Inspect only a lexically contained path in explicitly selected folders."""
    try:
        path = _absolute(path, "the saved audio file")
    except AppError:
        return {"status": "unsafe", "reason": "The saved path is invalid. Review its source before linking.", "fingerprint": None}
    for context in contexts:
        if context["raw"] is None:
            continue
        relative = _relative(path, context["raw"])
        if relative is None:
            relative = _relative(path, context["root"])
        if relative is None:
            continue
        if not context["available"]:
            status = "unsafe" if context["state"] == "unsafe" else "offline"
            reason = ("The selected folder is unsafe or invalid. Choose its actual location in Settings."
                      if status == "unsafe" else "The selected folder is unavailable. Reconnect it before deciding that this file is missing.")
            return {"status": status, "reason": reason, "fingerprint": None, "context": context}
        info, status, reason = _snapshot(context["root"], relative, context["identity"])
        if info is not None:
            return {"status": "available", "reason": "The file is available in a selected folder.",
                    "fingerprint": _fingerprint(info), "context": context,
                    "canonical": str(context["root"] / relative)}
        if status == "unavailable":
            return {"status": "missing", "reason": "The selected folder is available, but this saved file location is missing.",
                    "fingerprint": None, "context": context}
        return {"status": "unsafe", "reason": reason, "fingerprint": None, "context": context}
    return {"status": "outside_root", "reason": "This location is outside the selected music and inbox folders. Its availability was not checked.",
            "fingerprint": None}


def _metadata_identity(entry):
    return (entry.get("path"), entry.get("title"), entry.get("artist"), _duration(entry.get("duration_ms")),
            _text(entry.get("version_notes", "")), _text(entry.get("identity_source") or "filename"),
            _text(entry.get("source") or "scan"))


class LibraryAudit:
    def __init__(self, store, library, clock=time.monotonic):
        self.store, self.library, self.clock = store, library, clock
        self.lock = threading.RLock()
        self.plan = None
        with store.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS library_audit_hashes (
                path TEXT NOT NULL, root TEXT NOT NULL, fingerprint TEXT NOT NULL,
                sha256 TEXT NOT NULL, checked_at TEXT NOT NULL,
                PRIMARY KEY(path,root))""")

    def clear(self):
        with self.lock:
            self.plan = None

    @staticmethod
    def _real(settings):
        if settings.get("mode", "real") != "real":
            raise AppError("Leave the demo before reviewing real library files.", 409)

    @staticmethod
    def _settings(db):
        return {row["key"]: json.loads(row["value"]) for row in db.execute(
            "SELECT key,value FROM settings WHERE key IN ('mode','music_root','download_root')")}

    def _load(self):
        settings = self.store.settings()
        self._real(settings)
        contexts = [_context(settings.get("music_root", ""), "music"),
                    _context(settings.get("download_root", ""), "downloads")]
        warnings = []
        if contexts[0]["raw"] is None:
            raise AppError("Save a valid music folder in Settings before reviewing library health.", 409)
        try:
            self.library.invalidate()
            entries = self.library._entries(settings["music_root"], limit=MAX_ENTRIES + 1)
        except (ValueError, KeyError, TypeError, AttributeError, UnicodeError):
            raise AppError("Saved library metadata could not be read. Refresh media and XML, then review library health again.", 409) from None
        truncated = len(entries) > MAX_ENTRIES
        entries = entries[:MAX_ENTRIES]
        if truncated:
            warnings.append("This review is limited to 50,000 indexed files; some inventory entries were not checked.")
        tracks = []
        entry_paths = [entry["path"] for entry in entries if isinstance(entry, dict) and isinstance(entry.get("path"), str)]
        with self.store.connect() as db:
            raw_tracks = list(db.execute("SELECT id,metadata,annotations FROM tracks WHERE namespace='real' ORDER BY id LIMIT ?", (MAX_ENTRIES + 1,)))
            hashes = {}
            # A library can retain historical cache rows after files move.
            # Read checksums only for this bounded inventory selection, using
            # small batches that also fit older SQLite parameter limits.
            for start in range(0, len(entry_paths), 500):
                batch = entry_paths[start:start + 500]
                placeholders = ",".join("?" for _ in batch)
                hashes.update((row["path"], dict(row)) for row in db.execute(
                    "SELECT * FROM library_audit_hashes WHERE root=? AND path IN (" + placeholders + ")",
                    (str(contexts[0]["root"]), *batch)))
        if len(raw_tracks) > MAX_ENTRIES:
            warnings.append("This review is limited to 50,000 DJList tracks; some saved links were not checked.")
            truncated = True
        for row in raw_tracks[:MAX_ENTRIES]:
            try:
                metadata, annotations = json.loads(row["metadata"]), json.loads(row["annotations"])
                if not isinstance(metadata, dict) or not isinstance(annotations, dict):
                    raise ValueError()
                if any(not isinstance(metadata.get(key, ""), str) for key in ("artists", "title_and_version")):
                    raise ValueError()
                path = annotations.get("local_file", "")
                if not isinstance(path, str):
                    raise ValueError()
                tracks.append({**metadata, **annotations, "id": row["id"], "local_file": path})
            except (ValueError, TypeError):
                warnings.append("A malformed DJList track record was skipped; its saved data was left unchanged.")
                truncated = True
        safe_entries = []
        for entry in entries:
            if not isinstance(entry, dict) or not all(isinstance(entry.get(key), str) for key in ("path", "title", "artist")):
                warnings.append("Malformed library metadata was skipped; refresh its source to review it.")
                truncated = True
                continue
            value = dict(entry)
            value["name"] = _text(value.get("name") or Path(value["path"]).name)
            for key in ("album", "key", "bpm", "genre", "version_notes", "source", "identity_source"):
                value[key] = _text(value.get(key, ""))
            value["source"] = value["source"] or "scan"
            value["identity_source"] = value["identity_source"] or "filename"
            value["duration_ms"] = _duration(value.get("duration_ms"))
            value["cue_count"] = value.get("cue_count") if isinstance(value.get("cue_count"), int) and not isinstance(value.get("cue_count"), bool) else 0
            value["entry_id"] = _identity(value["path"])
            value["_location"] = _location_status(value["path"], contexts)
            value["available"] = value["_location"]["status"] == "available"
            value["within_root"] = value["_location"].get("context", {}).get("name") == "music"
            value["unavailable_reason"] = "" if value["available"] else value["_location"]["reason"]
            safe_entries.append(value)
        if _configuration(self.store.settings()) != _configuration(settings):
            raise AppError("Folder settings changed while library health was being checked. Refresh the review.", 409)
        return settings, contexts, safe_entries, tracks, hashes, sorted(set(warnings)), truncated

    @staticmethod
    def _cached_hash(entry, hashes):
        row = hashes.get(entry["path"])
        fingerprint = entry["_location"].get("fingerprint")
        if row and fingerprint:
            try:
                if (json.loads(row["fingerprint"]) == fingerprint and len(row["sha256"]) == 64
                        and all(char in "0123456789abcdef" for char in row["sha256"])):
                    return row
            except (ValueError, TypeError):
                pass
        return None

    def _groups(self, entries, hashes):
        by_version = defaultdict(list)
        for entry in entries:
            # Separate subtitle/mix tags are never discarded when grouping.
            by_version[_normalise(entry.get("version_notes", ""))].append(entry)
        groups = []
        by_path = {entry["path"]: entry for entry in entries}
        for version, rows in by_version.items():
            for group in self.library._duplicate_groups(rows):
                members, physical = [], set()
                alias_count = 0
                for path in group["paths"]:
                    entry = by_path[path]
                    fingerprint = entry["_location"].get("fingerprint")
                    identity = (fingerprint["device"], fingerprint["inode"]) if fingerprint else None
                    if identity is not None and identity in physical:
                        alias_count += 1
                        continue
                    if identity:
                        physical.add(identity)
                    members.append(entry)
                if len(members) < 2:
                    continue
                group_id = _identifier("duplicate\0" + version + "\0" + "\0".join(sorted(row["path"] for row in members)))
                verification = self._verification(members, hashes)
                groups.append({"group_id": group_id, "title": group["title"], "artist": group["artist"],
                               "reason": group["reason"] + (" Separate version notes also match." if version else ""),
                               "members": members, "count": len(members), "physical_aliases": alias_count,
                               "verification": verification})
        return sorted(groups, key=lambda group: (-group["count"], _normalise(group["artist"]), _normalise(group["title"]), group["group_id"]))

    def _verification(self, members, hashes):
        values = [(entry, self._cached_hash(entry, hashes)) for entry in members]
        known = [(entry, row) for entry, row in values if row]
        if not known:
            return {"status": "unchecked", "checked_at": None, "groups": [],
                    "message": "Metadata suggests possible copies. Use Compare file bytes to check identical files."}
        by_digest = defaultdict(list)
        for entry, row in known:
            by_digest[row["sha256"]].append(entry["path"])
        identical = [{"sha256": digest, "paths": paths, "count": len(paths)} for digest, paths in by_digest.items() if len(paths) > 1]
        partial = len(known) < len(members)
        all_same = not partial and len(by_digest) == 1
        status = "partial" if partial else "byte_identical" if all_same else "different"
        message = ("Some files have not been compared in their current state." if partial else
                   "All these files are byte-identical. Nothing was deleted or changed." if all_same else
                   "These files are not all byte-identical. Different tags, formats or audio can cause this; compare the recording.")
        if identical and not all_same:
            message += " %d set(s) of byte-identical files were found." % len(identical)
        return {"status": status, "checked_at": max(row["checked_at"] for _, row in known),
                "groups": identical, "message": message}

    @staticmethod
    def _public_member(entry):
        return {key: entry.get(key, "") for key in ("entry_id", "path", "title", "artist", "album", "genre", "bpm", "key", "source", "identity_source", "duration_ms", "version_notes", "available", "mime_type", "cue_evidence", "metadata_review_hint", "metadata_override", "metadata_override_stale")} | {
            "status": entry["_location"]["status"], "reason": entry["_location"]["reason"],
            "size": (entry["_location"].get("fingerprint") or {}).get("size")}

    def _issues(self, entries, tracks, hashes):
        linked = defaultdict(list)
        for track in tracks:
            if track.get("local_file"):
                linked[track["local_file"]].append(track)
        issues, seen = [], set()
        for entry in entries:
            seen.add(entry["path"])
            location = entry["_location"]
            if location["status"] == "available":
                continue
            issues.append({"id": "file-" + entry["entry_id"], "entry_id": entry["entry_id"],
                           "kind": location["status"], "title": entry["title"], "artist": entry["artist"],
                           "path": entry["path"], "source": entry.get("source", "scan"),
                           "identity_source": entry.get("identity_source", "filename"),
                           "track_ids": [track["id"] for track in linked.get(entry["path"], [])],
                           "reason": location["reason"], "duration_ms": entry.get("duration_ms"),
                           "version_notes": entry.get("version_notes", ""), "candidates": []})
        return issues, linked, seen

    def _build(self):
        settings, contexts, entries, tracks, hashes, warnings, truncated = self._load()
        issues, linked, seen = self._issues(entries, tracks, hashes)
        for path, rows in linked.items():
            if path in seen:
                continue
            location = _location_status(path, contexts)
            if location["status"] == "available":
                continue
            track = rows[0]
            issues.append({"id": "link-" + _identity(path), "entry_id": None, "kind": location["status"],
                           "title": track.get("title_and_version", ""), "artist": track.get("artists", ""),
                           "path": path, "source": "djlist_link", "identity_source": "spotify",
                           "track_ids": [row["id"] for row in rows], "reason": location["reason"],
                           "duration_ms": track.get("duration_ms"), "version_notes": "", "candidates": []})
        groups = self._groups(entries, hashes)
        for group in groups:
            issues.append({"id": "duplicate-" + group["group_id"], "kind": "duplicate",
                           "group_id": group["group_id"], "title": group["title"], "artist": group["artist"],
                           "path": "", "source": "library_metadata", "identity_source": "metadata_comparison",
                           "track_ids": sorted({track["id"] for entry in group["members"] for track in linked.get(entry["path"], [])}),
                           "reason": group["reason"], "count": group["count"],
                           "members": [self._public_member(entry) for entry in group["members"][:MAX_HASH_FILES]],
                           "members_truncated": group["count"] > MAX_HASH_FILES,
                           "physical_aliases": group["physical_aliases"], "verification": group["verification"]})
        order = {"missing": 0, "unsafe": 1, "outside_root": 2, "offline": 3, "duplicate": 4}
        issues.sort(key=lambda row: (order[row["kind"]], _normalise(row["artist"]), _normalise(row["title"]), row["id"]))
        summary = {"inventory_files": len(entries), "available_files": sum(row["available"] for row in entries),
                   "missing_files": sum(row["kind"] == "missing" for row in issues),
                   "offline_files": sum(row["kind"] == "offline" for row in issues),
                   "outside_root_files": sum(row["kind"] == "outside_root" for row in issues),
                   "unsafe_files": sum(row["kind"] == "unsafe" for row in issues),
                   "duplicate_groups": len(groups),
                   "duplicate_files": sum(group["count"] for group in groups),
                   "possible_extra_copies": sum(group["count"] - 1 for group in groups),
                   "byte_identical_groups": sum(len(group["verification"]["groups"]) for group in groups),
                   "linked_missing_tracks": sum(len(row["track_ids"]) for row in issues if row["kind"] == "missing")}
        if not contexts[0]["available"]:
            warnings.append("The selected music folder is unavailable. Offline files are not proof that audio was deleted.")
        return {"checked_at": _now(), "music_root": settings["music_root"], "root_available": contexts[0]["available"],
                "summary": summary, "issues": issues, "warnings": warnings, "truncated": truncated,
                "_entries": entries, "_tracks": tracks, "_groups": groups, "_contexts": contexts, "_settings": settings}

    @staticmethod
    def _filter(issues, query, kind):
        if kind not in KINDS:
            raise AppError("Choose a valid library health filter.")
        query = _normalise(_query(query))
        return [row for row in issues if (kind == "all" or row["kind"] == ("duplicate" if kind == "duplicates" else kind))
                and (not query or query in _normalise(" ".join(str(row.get(key, "")) for key in ("title", "artist", "path", "reason"))
                                                      + " " + " ".join(member["path"] for member in row.get("members", []))))]

    def _candidates(self, reference, entries, limit=MAX_CANDIDATES):
        candidates = []
        for entry in entries:
            if not entry["available"] or not entry["within_root"] or entry["path"] == reference.get("local_file"):
                continue
            value = self.library._candidate(reference, entry)
            if value is not None:
                candidates.append({**value, "entry_id": entry["entry_id"]})
        candidates.sort(key=lambda row: (-row["score"], row["path"]))
        return candidates[:limit], len(candidates) > limit

    def review(self, query="", kind="all", page=1, page_size=25):
        query = _query(query)
        if isinstance(page, bool) or not isinstance(page, int) or page < 1:
            raise AppError("Choose a valid library health page.")
        if isinstance(page_size, bool) or not isinstance(page_size, int) or not 1 <= page_size <= 100:
            raise AppError("Choose a page size from 1 to 100.")
        data = self._build()
        rows = self._filter(data["issues"], query, kind)
        pages = max(1, math.ceil(len(rows) / page_size))
        page = min(page, pages)
        selected = rows[(page - 1) * page_size:page * page_size]
        for row in selected:
            if row["kind"] in {"missing", "outside_root"}:
                reference = {"title_and_version": row["title"], "artists": row["artist"],
                             "duration_ms": row.get("duration_ms"), "local_file": row["path"]}
                row["candidates"], row["candidates_truncated"] = self._candidates(reference, data["_entries"], 5)
                row["can_relink"] = bool(row["track_ids"] and row["kind"] == "missing")
                if row["source"] == "rekordbox_xml":
                    row["reason"] += " Rekordbox locations must be updated in Rekordbox; this app only changes explicitly selected DJList links."
        return {key: value for key, value in data.items() if not key.startswith("_") and key != "issues"} | {
            "issues": selected, "total": len(rows), "page": page, "pages": pages,
            "page_size": page_size, "query": query, "kind": kind}

    def preview_relink(self, track_id):
        if isinstance(track_id, bool) or not isinstance(track_id, int) or track_id < 1:
            raise AppError("Choose a saved DJList track to review.")
        with self.lock:
            self.plan = None
            data = self._build()
            row = self.store.get(track_id, "real")
            try:
                metadata, annotations = json.loads(row["metadata"]), json.loads(row["annotations"])
                if (not isinstance(metadata, dict) or not isinstance(annotations, dict)
                        or any(not isinstance(metadata.get(key, ""), str) for key in ("title_and_version", "artists"))
                        or not isinstance(annotations.get("local_file", ""), str)):
                    raise ValueError()
            except (ValueError, TypeError):
                raise AppError("This saved track record needs review before relinking. Its data was left unchanged.", 409) from None
            previous = annotations.get("local_file", "")
            if not previous:
                raise AppError("This track has no previous file link. Use the download inbox or track details to select a file.", 409)
            old = _location_status(previous, data["_contexts"])
            if old["status"] != "missing":
                raise AppError("Only a missing file inside an available selected folder can be relinked here. Reconnect the drive or use Find moved files for an old folder location.", 409)
            candidates, truncated = self._candidates({**metadata, "local_file": previous}, data["_entries"])
            by_path = {entry["path"]: entry for entry in data["_entries"]}
            selected = {}
            for candidate in candidates:
                fingerprint = file_fingerprint(candidate["path"], data["_settings"]["music_root"])
                if fingerprint is not None:
                    selected[candidate["entry_id"]] = {**candidate, "_fingerprint": fingerprint,
                                                       "_metadata": _metadata_identity(by_path[candidate["path"]])}
            settings = data["_settings"]
            _, root, identity = _root(settings["music_root"])
            token = secrets.token_urlsafe(32)
            self.plan = {"token": token, "deadline": self.clock() + PLAN_SECONDS, "track_id": track_id,
                         "metadata": row["metadata"], "old_path": previous,
                         "old_root": (old["context"]["root"], old["context"]["identity"]),
                         "config": _configuration(settings), "root": root, "root_identity": identity,
                         "candidates": selected}
            return {"token": token, "expires_at": (datetime.now(timezone.utc) + timedelta(seconds=PLAN_SECONDS)).isoformat(timespec="seconds"),
                    "track_id": track_id, "title": metadata.get("title_and_version", ""), "artists": metadata.get("artists", ""),
                    "old_path": previous, "music_root": str(root), "candidates": [
                        {key: value for key, value in candidate.items() if not key.startswith("_")} for candidate in selected.values()],
                    "limit": MAX_CANDIDATES, "truncated": truncated}

    def apply_relink(self, token, entry_id):
        if not isinstance(token, str) or not token.isascii() or len(token) > 128:
            raise AppError("Make a fresh missing-file preview before relinking.", 409)
        with self.lock:
            plan = self.plan
            if plan is None or not secrets.compare_digest(token, plan["token"]):
                raise AppError("This missing-file preview is no longer available. Make a fresh preview.", 409)
            self.plan = None
            if self.clock() >= plan["deadline"]:
                raise AppError("This missing-file preview expired. Make a fresh preview.", 409)
            candidate = plan["candidates"].get(entry_id) if isinstance(entry_id, str) else None
            if candidate is None:
                raise AppError("Select one available file from this reviewed preview.")
            with self.store.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                settings = self._settings(db)
                self._real(settings)
                if _configuration(settings) != plan["config"]:
                    raise AppError("Folder settings changed. Make a fresh missing-file preview.", 409)
                _, root, identity = _root(settings["music_root"])
                if (root, identity) != (plan["root"], plan["root_identity"]):
                    raise AppError("The music folder changed. Make a fresh missing-file preview.", 409)
                row = db.execute("SELECT metadata,annotations FROM tracks WHERE id=? AND namespace='real'", (plan["track_id"],)).fetchone()
                try:
                    annotations = json.loads(row["annotations"]) if row else {}
                    if not isinstance(annotations, dict):
                        raise ValueError()
                except (ValueError, TypeError):
                    raise AppError("The saved track record changed. Nothing was relinked; review its data first.", 409) from None
                if row is None or row["metadata"] != plan["metadata"] or annotations.get("local_file", "") != plan["old_path"]:
                    raise AppError("The track or its saved link changed. Nothing was relinked; make a fresh preview.", 409)
                contexts = [_context(settings.get("music_root", ""), "music"), _context(settings.get("download_root", ""), "downloads")]
                old = _location_status(plan["old_path"], contexts)
                if old["status"] != "missing":
                    raise AppError("The original location is available again or cannot be checked. Nothing was relinked.", 409)
                if (old["context"]["root"], old["context"]["identity"]) != plan["old_root"]:
                    raise AppError("The original file's selected folder changed. Make a fresh preview.", 409)
                self.library.invalidate()
                current_entry = next((entry for entry in self.library._entries(settings["music_root"], limit=MAX_ENTRIES + 1)
                                      if entry["path"] == candidate["path"]), None)
                if current_entry is None or _metadata_identity(current_entry) != candidate["_metadata"]:
                    raise AppError("The proposed file's library metadata changed. Make a fresh preview.", 409)
                fingerprint = file_fingerprint(candidate["path"], settings["music_root"])
                if fingerprint is None or fingerprint != candidate["_fingerprint"]:
                    raise AppError("The proposed file changed or became unavailable. Nothing was relinked.", 409)
                if _root(settings["music_root"])[1:] != (root, identity):
                    raise AppError("The music folder changed. Nothing was relinked.", 409)
                db.execute("UPDATE tracks SET annotations=? WHERE id=? AND namespace='real'",
                           (json.dumps({**annotations, "local_file": candidate["path"]}), plan["track_id"]))
            return {"updated": 1, "track_id": plan["track_id"], "local_file": candidate["path"],
                    "message": "The selected DJList file link was updated. Ownership, notes, tags and preparation state were preserved. Rekordbox was not changed."}

    def _hash(self, path, root, root_identity, expected, budget):
        if expected["size"] > budget["remaining"] or self.clock() >= budget["deadline"]:
            raise AppError("The file-comparison budget was reached. Compare a smaller group.", 413)
        hasher = hashlib.sha256()
        with _selected_file(path, str(root), open_content=True) as (stream, before, canonical):
            if _fingerprint(before) != expected or _root(str(root))[2] != root_identity:
                raise AppError("The file or music folder changed before comparison.", 409)
            while True:
                if self.clock() >= budget["deadline"]:
                    raise AppError("The comparison time limit was reached. Try a smaller group.", 413)
                data = stream.read(min(HASH_CHUNK, budget["remaining"] + 1))
                budget["read"] += len(data)
                budget["remaining"] -= len(data)
                if budget["remaining"] < 0:
                    raise AppError("The file-comparison byte limit was reached.", 413)
                if not data:
                    break
                hasher.update(data)
            if _fingerprint(os.fstat(stream.fileno())) != expected or file_fingerprint(canonical, str(root)) != expected:
                raise AppError("The file changed during comparison; its checksum was discarded.", 409)
        if _root(str(root))[2] != root_identity:
            raise AppError("The music folder changed during comparison.", 409)
        return hasher.hexdigest()

    def verify_duplicates(self, group_id):
        if not isinstance(group_id, str) or len(group_id) != 24 or not all(char in "0123456789abcdef" for char in group_id):
            raise AppError("Choose a duplicate group from the current library health review.")
        with self.lock:
            settings, contexts, entries, tracks, hashes, warnings, truncated = self._load()
            groups = self._groups(entries, hashes)
            group = next((row for row in groups if row["group_id"] == group_id), None)
            if group is None:
                raise AppError("This duplicate group changed or is no longer available. Refresh library health.", 409)
            if not contexts[0]["available"]:
                raise AppError("Reconnect the selected music folder before comparing file bytes.", 409)
            root, root_identity = contexts[0]["root"], contexts[0]["identity"]
            budget = {"remaining": MAX_HASH_BYTES, "read": 0, "deadline": self.clock() + MAX_HASH_SECONDS}
            results, new_hashes, cached = [], {}, 0
            members = group["members"][:MAX_HASH_FILES]
            for entry in members:
                result = {"entry_id": entry["entry_id"], "path": entry["path"], "status": "unavailable", "reason": entry["_location"]["reason"]}
                if not entry["available"] or not entry["within_root"]:
                    results.append(result)
                    continue
                expected = entry["_location"]["fingerprint"]
                record = self._cached_hash(entry, hashes)
                try:
                    if record:
                        digest = record["sha256"]
                        cached += 1
                    else:
                        digest = self._hash(entry["path"], root, root_identity, expected, budget)
                        record = {"path": entry["path"], "root": str(root), "fingerprint": json.dumps(expected, sort_keys=True),
                                  "sha256": digest, "checked_at": _now()}
                        new_hashes[entry["path"]] = record
                    result.update(status="verified", sha256=digest, reason="SHA-256 compared the complete file bytes.")
                except (OSError, ValueError, RuntimeError, AppError) as error:
                    result.update(status="limited" if isinstance(error, AppError) and error.status == 413 else "unavailable",
                                  reason=str(error) if isinstance(error, AppError) else "This file could not be read safely. Refresh media and check its drive.")
                results.append(result)
            if _configuration(self.store.settings()) != _configuration(settings) or _root(settings["music_root"])[1:] != (root, root_identity):
                raise AppError("Folder settings changed during comparison. Refresh library health; no comparison was accepted.", 409)
            for entry, result in zip(members, results):
                if result["status"] == "verified" and file_fingerprint(entry["path"], str(root)) != entry["_location"]["fingerprint"]:
                    result.update(status="unavailable", reason="The file changed after comparison; its checksum was discarded.")
                    result.pop("sha256", None)
                    new_hashes.pop(entry["path"], None)
                    hashes.pop(entry["path"], None)
            with self.store.connect() as db:
                for record in new_hashes.values():
                    db.execute("INSERT OR REPLACE INTO library_audit_hashes VALUES (?,?,?,?,?)",
                               tuple(record[key] for key in ("path", "root", "fingerprint", "sha256", "checked_at")))
            hashes.update(new_hashes)
            verification = self._verification(group["members"], hashes)
            partial = len(members) < group["count"] or any(row["status"] != "verified" for row in results)
            status = "partial" if partial else verification["status"]
            if not any(row["status"] == "verified" for row in results):
                status = "unavailable"
            return {"group_id": group_id, "status": status, "checked_at": _now(), "groups": verification["groups"],
                    "members": results, "bytes_read": budget["read"], "cached_files": cached, "partial": partial,
                    "limit": MAX_HASH_FILES, "truncated": len(members) < group["count"],
                    "message": verification["message"] + (" The comparison was incomplete; no files were changed." if partial else "")}

    def export_report(self, query="", kind="all"):
        data = self._build()
        issues = self._filter(data["issues"], query, kind)
        if len(issues) > MAX_REPORT_ROWS:
            raise AppError("Filter the library health report to 10,000 issues or fewer before exporting.", 413)
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(["Kind", "Title", "Artist", "Saved file path", "DJList track IDs", "Source", "Reason", "Duplicate group", "File comparison", "Group file paths"])
        for warning in data["warnings"]:
            writer.writerow(["review_warning", "", "", "", "", "DJList review", _safe_cell(warning), "", "", ""])
        for row in issues:
            writer.writerow([_safe_cell(value) for value in (row["kind"], row["title"], row["artist"], row["path"],
                "; ".join(map(str, row["track_ids"])), row["source"], row["reason"], row.get("group_id", ""),
                row.get("verification", {}).get("status", ""), "; ".join(member["path"] for member in row.get("members", [])) +
                (" [Additional group paths omitted; review this group in DJList.]" if row.get("members_truncated") else ""))])
            if output.tell() > MAX_REPORT_BYTES:
                raise AppError("Filter the library health report further before exporting; it exceeds the 8 MB limit.", 413)
        payload = output.getvalue()
        if len(payload.encode("utf-8")) > MAX_REPORT_BYTES:
            raise AppError("Filter the library health report further before exporting; it exceeds the 8 MB limit.", 413)
        return payload
