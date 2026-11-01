"""Durable relocation handoff with evidence from a fresh Rekordbox XML export.

This is deliberately not a master.db writer. Rekordbox owns the actual Relocate
operation. A new XML path alone does not prove that an existing record survived:
we also check its original TrackID, exported cues/grid and playlist membership.
Every file-operation journal event is ingested in the same DJList transaction,
including undo. No API lets a user tick an unverified relocation as completed.
"""
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import threading

from spotify_api import AppError


MAX_QUEUE = 50000
MAX_HISTORY = 10000
INSTRUCTIONS = [
    "In Rekordbox, open File > Display All Missing Files, select the existing track and choose Relocate.",
    "Use the exact new file path shown here. Do not delete and re-import the existing track.",
    "Export the collection XML to the saved path, then choose Verify from XML in DJList.",
]


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def ensure_tables(db):
    db.execute("""CREATE TABLE IF NOT EXISTS rekordbox_relocations (
        id TEXT PRIMARY KEY, metadata TEXT NOT NULL)""")
    db.execute("""CREATE TABLE IF NOT EXISTS rekordbox_relocation_events (
        operation_id TEXT PRIMARY KEY)""")


def _identity(fingerprint):
    if not isinstance(fingerprint, dict):
        return None
    values = tuple(fingerprint.get(key) for key in ("device", "inode", "size", "mtime_ns"))
    return values if all(isinstance(value, int) for value in values) else None


def record_file_changes(db, operation):
    """Called inside the successful file-operation transaction; never probe files."""
    ensure_tables(db)
    if db.execute("SELECT 1 FROM rekordbox_relocation_events WHERE operation_id=?", (operation["id"],)).fetchone():
        return
    if operation.get("status") not in {"completed", "undone"}:
        return
    saved = [json.loads(row[0]) for row in db.execute("SELECT metadata FROM rekordbox_relocations LIMIT ?", (MAX_QUEUE + 1,))]
    if len(saved) > MAX_QUEUE:
        raise AppError("The relocation history is too large to review safely. File changes are paused.", 413)
    for item in operation.get("items", []):
        matches = [row for row in saved if row["current_path"] == item["before"]
                   and _identity(row.get("fingerprint")) is not None
                   and _identity(row["fingerprint"]) == _identity(item.get("_fingerprint"))]
        if len(matches) > 1:
            raise AppError("More than one relocation record claims this file. Inspect its history before changing it.", 409)
        row = matches[0] if matches else None
        if row is None and operation.get("action") != "rename":
            continue
        evidence = item.get("_rekordbox_relocation", {})
        if row is None:
            if len(saved) >= MAX_QUEUE:
                raise AppError("The relocation queue is full. File changes are paused.", 413)
            baseline = evidence.get("evidence", {})
            row = {"id": hashlib.sha256((operation["id"] + ":" + item["entry_id"]).encode()).hexdigest()[:24],
                   "title": item.get("title", ""), "artist": item.get("artist", ""),
                   "original_path": item["before"], "current_path": item["before"],
                   "track_id": baseline.get("track_id", ""), "baseline": baseline,
                   "baseline_bound": evidence.get("bound_to_audio") is True,
                   "xml_source_path": evidence.get("snapshot", {}).get("source_path", ""),
                   "operation_ids": [], "configuration": operation.get("configuration", {}),
                   "root": item.get("_root", {}), "created_at": operation.get("created_at", "")}
            saved.append(row)
        row.update(current_path=item["after"], fingerprint=item.get("_after_fingerprint"),
                   last_changed_at=operation.get("completed_at", operation.get("created_at", "")),
                   removed=".djlist-removed" in Path(item["after"]).parts,
                   last_snapshot=evidence.get("snapshot", {}), verification=None)
        row["operation_ids"].append(operation["id"])
        db.execute("INSERT OR REPLACE INTO rekordbox_relocations VALUES (?,?)", (row["id"], json.dumps(row)))
    db.execute("INSERT INTO rekordbox_relocation_events VALUES (?)", (operation["id"],))


def capture_evidence(entry, fingerprint, snapshot):
    """Freeze pre-rename evidence in the durable operation journal."""
    imported = entry.get("xml_audio_fingerprint")
    expected = {key: value for key, value in fingerprint.items() if key != "mode"}
    return {"evidence": entry.get("xml_relocation_evidence", {}),
            "bound_to_audio": isinstance(imported, dict) and imported == expected,
            "snapshot": {key: snapshot.get(key) for key in
                         ("source_path", "source_fingerprint", "content_sha256", "imported_at")}}


def _retained(expected, actual):
    return not (Counter(expected or []) - Counter(actual or []))


class RekordboxRelocation:
    def __init__(self, store, library):
        self.store, self.library = store, library
        self.lock = threading.RLock()
        with self.store.connect() as db:
            ensure_tables(db)

    def _real(self):
        settings = self.store.settings()
        if settings.get("mode", "real") != "real":
            raise AppError("Leave the demo before reviewing Rekordbox relocations.", 409)
        return settings

    def _ingest(self):
        # Backfill earlier v0.7 journals. They do not contain a TrackID baseline,
        # so those rows remain review-required instead of inventing evidence.
        with self.store.connect() as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='file_operations'").fetchone() is None:
                return
            rows = list(db.execute("""SELECT f.metadata FROM file_operations f
                LEFT JOIN rekordbox_relocation_events e ON e.operation_id=f.id
                WHERE e.operation_id IS NULL ORDER BY f.created_at,f.id LIMIT ?""", (MAX_HISTORY + 1,)))
            if len(rows) > MAX_HISTORY:
                raise AppError("The file-operation history is too large to backfill in one review.", 413)
            for row in rows:
                record_file_changes(db, json.loads(row[0]))

    @staticmethod
    def _file_state(row, settings):
        # Reuse the same no-follow, single-link and root-identity checks as the
        # original operation. Do not traverse arbitrary paths from XML/history.
        from file_operations import FileOperations
        config = row.get("configuration", {})
        if any(config.get(key, "") != settings.get(key, "") for key in
               ("music_root", "download_root", "rekordbox_db")):
            return "settings_changed", "Folder or database settings changed. Restore the original selection before checking this relocation."
        if row.get("xml_source_path") and row["xml_source_path"] != settings.get("rekordbox_xml", ""):
            return "settings_changed", "The selected XML source changed. Use the original collection export to verify this relocation."
        if row.get("removed"):
            return "removed", "This audio is in recoverable Removed storage. Restore it to continue relocation; do not point Rekordbox into Removed storage."
        try:
            root = row.get("root", {})
            if root.get("setting") not in {"music_root", "download_root"} or root.get("path") != settings.get(root.get("setting")):
                return "unavailable", "The original drive or selected folder identity changed. Reconnect it before checking this relocation."
            FileOperations._context(row["current_path"], [root])
            fingerprint, _ = FileOperations._inspect(row["current_path"], root)
        except (AppError, OSError, ValueError, KeyError):
            return "unavailable", "The renamed file or its original drive is unavailable. Reconnect it and refresh before checking relocation."
        if fingerprint != row.get("fingerprint"):
            return "file_changed", "The renamed file changed after the move. Inspect it before accepting a relocation."
        return "available", ""

    @staticmethod
    def _public(row, settings, snapshot):
        state, message = RekordboxRelocation._file_state(row, settings)
        verification = row.get("verification") or {}
        evidence = {key: verification.get(key) for key in
                    ("same_track_id", "cues_preserved", "grid_preserved", "playlists_preserved")}
        status = verification.get("status", "pending")
        if state != "available":
            status = "removed" if state == "removed" else "blocked"
        elif verification and (verification.get("xml_content_sha256") != snapshot.get("content_sha256")
                               or verification.get("xml_source_fingerprint") != snapshot.get("source_fingerprint")):
            status, message = "pending", "The XML snapshot changed. Verify again to check the current export."
        else:
            message = verification.get("message", "Relocate this existing record in Rekordbox, export fresh XML, then verify here.")
        if state == "available" and (not row.get("baseline_bound") or not row.get("track_id")
                                     or not row.get("baseline", {}).get("unique_track_id")
                                     or not row.get("baseline", {}).get("unique_location")):
            status, message = "needs_review", "No unique TrackID tied to the original audio was recorded before this rename. Its original record needs manual verification in Rekordbox."
        return {"id": row["id"], "title": row.get("title", ""), "artist": row.get("artist", ""),
                "original_path": row["original_path"], "current_path": row["current_path"],
                "track_id": row.get("track_id", ""), "status": status, "message": message,
                "can_relocate": state == "available", "verified_at": verification.get("checked_at") if status == "verified" else None,
                "evidence": evidence, "operation_ids": row.get("operation_ids", [])}

    def status(self):
        with self.lock:
            settings = self._real()
            self._ingest()
            snapshot = self.library._snapshot()
            with self.store.connect() as db:
                rows = [json.loads(row[0]) for row in db.execute("SELECT metadata FROM rekordbox_relocations LIMIT ?", (MAX_QUEUE + 1,))]
            if len(rows) > MAX_QUEUE:
                raise AppError("The relocation queue is too large to review safely.", 413)
            entries = [self._public(row, settings, snapshot) for row in rows]
            entries.sort(key=lambda row: (row["status"] == "verified", row["title"].casefold(), row["id"]))
            counts = Counter(row["status"] for row in entries)
            return {"entries": entries,
                    "counts": {"total": len(entries), "pending": counts["pending"], "verified": counts["verified"],
                               "blocked": counts["blocked"] + counts["needs_review"], "removed": counts["removed"]},
                    "instructions": list(INSTRUCTIONS), "direct_database_write": False, "xml_required": True,
                    "message": "Verification checks an exported XML snapshot. DJList does not write the live Rekordbox database or claim to have inspected its current contents."}

    def verify(self):
        with self.lock:
            settings = self._real()
            self._ingest()
            xml_path = settings.get("rekordbox_xml", "")
            if not xml_path:
                raise AppError("Save your Rekordbox XML export path in Settings, export the collection, then verify again.", 409)
            self.library.refresh_xml(xml_path, settings.get("music_root", ""))
            snapshot = self.library._snapshot()
            with self.store.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                rows = [json.loads(row[0]) for row in db.execute("SELECT metadata FROM rekordbox_relocations LIMIT ?", (MAX_QUEUE + 1,))]
                entries = [json.loads(row[0]) for row in db.execute("SELECT metadata FROM library_entries LIMIT ?", (MAX_QUEUE + 1,))]
                if len(rows) > MAX_QUEUE or len(entries) > MAX_QUEUE:
                    raise AppError("The relocation review exceeds the supported collection size.", 413)
                by_id = {}
                for entry in entries:
                    track_id = entry.get("xml_relocation_evidence", {}).get("track_id", "")
                    by_id.setdefault(track_id, []).append(entry)
                for row in rows:
                    result = {"status": "pending", "checked_at": _now(),
                              "xml_content_sha256": snapshot.get("content_sha256"),
                              "xml_source_fingerprint": snapshot.get("source_fingerprint"),
                              "same_track_id": False, "cues_preserved": None, "grid_preserved": None, "playlists_preserved": None}
                    before = row.get("last_snapshot", {})
                    baseline = row.get("baseline", {})
                    candidates = by_id.get(row.get("track_id", ""), [])
                    available, file_message = self._file_state(row, settings)
                    fresh = (snapshot.get("source_path") == xml_path and snapshot.get("source_fingerprint") is not None
                             and snapshot.get("source_fingerprint") != before.get("source_fingerprint"))
                    if available != "available":
                        result.update(status="blocked", message=file_message)
                    elif (not row.get("baseline_bound") or not baseline.get("unique_track_id") or not baseline.get("unique_location")
                          or not baseline.get("complete")):
                        result.update(status="needs_review", message="Original TrackID and preparation evidence are incomplete. Check the existing record in Rekordbox manually.")
                    elif not fresh:
                        result["message"] = "This is the XML file from before the last rename. Export the collection again in Rekordbox, then verify."
                    elif (len(candidates) != 1 or not candidates[0].get("xml_relocation_evidence", {}).get("unique_track_id")
                          or not candidates[0].get("xml_relocation_evidence", {}).get("unique_location")):
                        result.update(status="needs_review", message="The original TrackID is missing or duplicated in the new XML. A new import is not proof that the original record was relocated.")
                    elif candidates[0]["path"] != row["current_path"]:
                        result["message"] = "The original record still points to a different location in this XML. Use Relocate in Rekordbox, then export again."
                    else:
                        current = candidates[0].get("xml_relocation_evidence", {})
                        result["same_track_id"] = True
                        result["cues_preserved"] = current.get("complete") is True and _retained(baseline.get("markers"), current.get("markers"))
                        result["grid_preserved"] = current.get("complete") is True and _retained(baseline.get("grid"), current.get("grid"))
                        result["playlists_preserved"] = (baseline.get("playlists_complete") is True and current.get("playlists_complete") is True
                                                         and _retained(baseline.get("playlists"), current.get("playlists")))
                        if all(result[key] is True for key in ("cues_preserved", "grid_preserved", "playlists_preserved")):
                            result.update(status="verified", message="Verified in XML: the same TrackID points to the renamed file, with the previously exported cues, grid and playlist memberships retained.")
                        else:
                            result.update(status="needs_review", message="The original TrackID has the new path, but cue, grid or playlist evidence changed or is incomplete. Review that track in Rekordbox.")
                    row["verification"] = result
                    db.execute("UPDATE rekordbox_relocations SET metadata=? WHERE id=?", (json.dumps(row), row["id"]))
                if self.store.settings() != settings or self.library._snapshot() != snapshot:
                    raise AppError("Settings or the XML snapshot changed during verification. Verify again.", 409)
                from library_inventory import xml_fingerprint
                try:
                    current_xml = xml_fingerprint(Path(xml_path).lstat())
                except OSError:
                    current_xml = None
                if current_xml != snapshot.get("source_fingerprint"):
                    raise AppError("The XML file changed during verification. Finish exporting and verify again.", 409)
            return self.status()
