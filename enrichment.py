"""Persistent store suggestions and a paced, cancellable local lookup queue.

Only catalogue metadata is stored here. Manual annotations and ownership are
never edited. Constructing the queue does not start a thread or a network call.
"""
from collections import deque
from datetime import datetime, timezone
import hashlib
import json
import threading
import time

from spotify_api import AppError


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def duration_ms(metadata):
    value = metadata.get("duration_ms")
    if isinstance(value, (int, float)) and not isinstance(value, bool) and 0 < value <= 86400000:
        return int(value)
    value = metadata.get("duration", "")
    if isinstance(value, str):
        parts = value.split(":")
        if len(parts) in (2, 3) and all(part.isdigit() for part in parts):
            seconds = 0
            for part in parts:
                seconds = seconds * 60 + int(part)
            if 0 < seconds <= 86400:
                return seconds * 1000
    return None


def reference_for(metadata):
    return {"artists": str(metadata.get("artists") or ""),
            "title": str(metadata.get("title_and_version") or ""),
            "duration_ms": duration_ms(metadata), "isrc": str(metadata.get("isrc") or "")}


def fingerprint(row):
    metadata = json.loads(row["metadata"]) if isinstance(row["metadata"], str) else row["metadata"]
    identity = {"source": row["source"], "spotify_id": row["spotify_id"], **reference_for(metadata)}
    return hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def empty_lookup():
    return {"status": "not_checked", "checked_at": None, "last_attempt_at": None,
            "offer": None, "offers": [], "message": ""}


def public_lookup(row):
    if row is None:
        return empty_lookup()
    return {"status": row["status"], "checked_at": row["checked_at"],
            "last_attempt_at": row["updated_at"],
            "offer": json.loads(row["offer"]) if row["offer"] else None,
            "offers": json.loads(row["offers"]), "message": row["message"]}


class StoreLookupQueue:
    """One worker per running app; a lookup never implies a confirmed recording."""
    def __init__(self, store, search, interval=8.0, retry_delay=60.0, max_attempts=3):
        self.store = store
        self.search = search
        self.interval = interval
        self.retry_delay = retry_delay
        self.max_attempts = max_attempts
        self.lock = threading.RLock()
        self.queue_items = deque()
        self.pending = {}
        self.thread = None
        self.cancel_event = threading.Event()
        self.next_lookup_at = 0.0
        self.closed = False
        self.progress = self._idle()
        with store.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS store_lookups (
                    track_id INTEGER PRIMARY KEY REFERENCES tracks(id) ON DELETE CASCADE,
                    fingerprint TEXT NOT NULL, status TEXT NOT NULL,
                    checked_at TEXT, offer TEXT, offers TEXT NOT NULL DEFAULT '[]',
                    message TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL);
            """)
            db.execute("UPDATE store_lookups SET status='interrupted', message=? WHERE status IN ('queued','searching')",
                       ("The app stopped before this lookup finished. Refresh stores to try again.",))

    @staticmethod
    def _idle():
        return {"running": False, "total": 0, "completed": 0, "matched": 0, "review": 0,
                "not_found": 0, "failed": 0, "cancelled": 0, "queued": 0,
                "current_track": "", "message": "", "cancel_requested": False}

    def status(self):
        with self.lock:
            return dict(self.progress, queued=len(self.queue_items))

    def attach(self, tracks, namespace):
        """Attach metadata suggestions without changing Store.rows' manual values."""
        with self.store.connect() as db:
            rows = {row["track_id"]: row for row in db.execute(
                "SELECT store_lookups.* FROM store_lookups JOIN tracks ON tracks.id=store_lookups.track_id WHERE tracks.namespace=?",
                (namespace,))}
        for track in tracks:
            row = rows.get(track["id"])
            identity = {"metadata": track, "source": track["source"], "spotify_id": track["spotify_id"]}
            if row is not None and row["fingerprint"] != fingerprint(identity):
                track["store_lookup"] = dict(empty_lookup(), message="Track details changed. Refresh its store lookup.")
            else:
                track["store_lookup"] = public_lookup(row)
        return tracks

    def _due(self, row, current_fingerprint, force):
        if force or row is None or row["fingerprint"] != current_fingerprint:
            return True
        if row["status"] in ("error", "interrupted", "not_checked") or not row["checked_at"]:
            return True
        try:
            checked = datetime.fromisoformat(row["checked_at"].replace("Z", "+00:00"))
            return (datetime.now(timezone.utc) - checked).total_seconds() >= 86400
        except (ValueError, TypeError):
            return True

    def enqueue(self, identities=None, force=False):
        if not isinstance(force, bool):
            raise AppError("Choose whether to refresh existing store results.")
        with self.lock:
            if self.closed:
                raise AppError("DJList is stopping. Open it again before refreshing stores.", 409)
            if self.progress["running"] and self.progress["cancel_requested"]:
                raise AppError("The previous lookup is stopping. Try again shortly.", 409)
            with self.store.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                mode = json.loads(db.execute("SELECT value FROM settings WHERE key='mode'").fetchone()[0])
                if mode != "real":
                    raise AppError("Leave the demo before refreshing real store results.", 409)
                if identities is None:
                    tracks = list(db.execute("SELECT * FROM tracks WHERE namespace='real' AND in_source IS NOT 0 ORDER BY id"))
                else:
                    tracks = []
                    for identity in identities:
                        row = db.execute("SELECT * FROM tracks WHERE id=? AND namespace='real'", (identity,)).fetchone()
                        if row is None:
                            raise AppError("This track is not in the real collection.", 404)
                        tracks.append(row)
                accepted = []
                for track in tracks:
                    signature = fingerprint(track)
                    if self.pending.get(track["id"]) == signature:
                        continue
                    previous = db.execute("SELECT * FROM store_lookups WHERE track_id=?", (track["id"],)).fetchone()
                    if not self._due(previous, signature, force):
                        continue
                    keep = public_lookup(previous) if previous is not None and previous["fingerprint"] == signature else empty_lookup()
                    db.execute("""INSERT INTO store_lookups(track_id,fingerprint,status,checked_at,offer,offers,message,updated_at)
                        VALUES (?,?,'queued',?,?,?,?,?) ON CONFLICT(track_id) DO UPDATE SET
                        fingerprint=excluded.fingerprint,status=excluded.status,checked_at=excluded.checked_at,
                        offer=excluded.offer,offers=excluded.offers,message=excluded.message,updated_at=excluded.updated_at""",
                        (track["id"], signature, keep["checked_at"], json.dumps(keep["offer"]) if keep["offer"] else None,
                         json.dumps(keep["offers"]), "Waiting for a store lookup.", timestamp()))
                    accepted.append({"id": track["id"], "fingerprint": signature,
                                     "metadata": json.loads(track["metadata"]), "attempt": 0, "force": force})
            if not accepted:
                return {"queued": 0, "job": self.status()}
            if not self.progress["running"]:
                self.progress = self._idle()
                self.progress["running"] = True
                self.cancel_event = threading.Event()
            for task in accepted:
                self.pending[task["id"]] = task["fingerprint"]
                self.queue_items.append(task)
            self.progress["total"] += len(accepted)
            self.progress["message"] = "Looking up store candidates. Your saved track details stay editable."
            if self.thread is None or not self.thread.is_alive():
                self.thread = threading.Thread(target=self._run, args=(self.cancel_event,), name="DJList-store-lookup", daemon=True)
                self.thread.start()
            return {"queued": len(accepted), "job": self.status()}

    def _mark(self, task, status, message):
        with self.store.connect() as db:
            db.execute("UPDATE store_lookups SET status=?,message=?,updated_at=? WHERE track_id=? AND fingerprint=?",
                       (status, message, timestamp(), task["id"], task["fingerprint"]))

    def cancel(self):
        with self.lock:
            if not self.progress["running"]:
                return self.status()
            self.cancel_event.set()
            self.progress["cancel_requested"] = True
            self.progress["message"] = "Stopping store lookups after the current request returns."
            waiting = list(self.queue_items)
            self.queue_items.clear()
            for task in waiting:
                self._mark(task, "interrupted", "Store lookup cancelled. Previous results were kept.")
                if self.pending.get(task["id"]) == task["fingerprint"]:
                    self.pending.pop(task["id"], None)
            self.progress["cancelled"] += len(waiting)
            self.progress["completed"] += len(waiting)
            return self.status()

    def close(self):
        with self.lock:
            self.closed = True
        self.cancel()
        thread = self.thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1)

    @staticmethod
    def _outcome(result, previous):
        offers = [offer for offer in result.get("offers", []) if isinstance(offer, dict)][:5]
        errors = result.get("errors") or []
        if errors:
            messages = [error.get("message", "") if isinstance(error, dict) else str(error) for error in errors]
            message = " ".join(value for value in messages if value)[:2000] or "The store lookup could not finish."
            if previous["offer"] or previous["offers"]:
                return dict(previous, status="error", message=message + " Previous candidates were kept.")
            return dict(previous, status="review" if offers else "error", offers=offers,
                        message=message, offer=None)
        likely = [offer for offer in offers if offer.get("match", {}).get("status") == "likely_match"]
        if len(likely) == 1:
            status, offer = "matched", likely[0]
            message = "One likely store match. Check the version and preview before buying."
        elif offers:
            status, offer = "review", None
            message = "Several likely matches need your review." if len(likely) > 1 else "Check these candidates; the recording has not been confirmed."
        else:
            status, offer = "not_found", None
            message = "No candidate found in the supported store. Other shop searches remain available."
        return {"status": status, "checked_at": result.get("checked_at") or timestamp(),
                "offer": offer, "offers": offers, "message": message}

    def _save_result(self, task, result, event):
        with self.lock:
            if event.is_set():
                return "cancelled"
            with self.store.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute("SELECT * FROM tracks WHERE id=? AND namespace='real'", (task["id"],)).fetchone()
                mode = json.loads(db.execute("SELECT value FROM settings WHERE key='mode'").fetchone()[0])
                saved = db.execute("SELECT * FROM store_lookups WHERE track_id=?", (task["id"],)).fetchone()
                if (mode != "real" or row is None or fingerprint(row) != task["fingerprint"] or
                        saved is None or saved["fingerprint"] != task["fingerprint"]):
                    return "stale"
                outcome = self._outcome(result, public_lookup(saved))
                db.execute("UPDATE store_lookups SET status=?,checked_at=?,offer=?,offers=?,message=?,updated_at=? WHERE track_id=? AND fingerprint=?",
                           (outcome["status"], outcome["checked_at"], json.dumps(outcome["offer"]) if outcome["offer"] else None,
                            json.dumps(outcome["offers"]), outcome["message"], timestamp(), task["id"], task["fingerprint"]))
                return outcome["status"]

    def _finish(self, task, outcome):
        with self.lock:
            if self.pending.get(task["id"]) == task["fingerprint"]:
                self.pending.pop(task["id"], None)
            self.progress["completed"] += 1
            counter = outcome if outcome in ("matched", "review", "not_found", "cancelled") else "failed"
            self.progress[counter] += 1

    def _finish_job(self, event):
        # Called under the same lock as enqueue so a newly appended task cannot
        # be stranded between an empty-queue check and worker shutdown.
        if self.cancel_event is not event:
            return
        self.progress["running"] = False
        self.progress["current_track"] = ""
        if event.is_set():
            self.progress["message"] = "Store lookups cancelled. Previous saved results were kept."
        elif not self.progress["message"].startswith("Store lookups stopped"):
            self.progress["message"] = "Store lookup finished. Review candidates before buying."
        self.thread = None

    def _run(self, event):
        current = None
        try:
            while True:
                with self.lock:
                    if event.is_set() or not self.queue_items:
                        self._finish_job(event)
                        return
                    current = self.queue_items.popleft()
                    self.progress["current_track"] = current["metadata"].get("title_and_version", "Track")
                if event.wait(max(0.0, self.next_lookup_at - time.monotonic())):
                    self._mark(current, "interrupted", "Store lookup cancelled. Previous results were kept.")
                    self._finish(current, "cancelled")
                    current = None
                    break
                reference = reference_for(current["metadata"])
                query = (reference["artists"] + " " + reference["title"]).strip()
                current["attempt"] += 1
                self._mark(current, "searching", "Checking the store catalogue.")
                self.next_lookup_at = time.monotonic() + self.interval
                try:
                    result = self.search(query, reference, force=current["force"])
                except AppError as error:
                    result = {"errors": [{"code": "rate_limited" if error.status == 429 else "unavailable" if error.status >= 500 else "invalid_input",
                                          "message": str(error)}], "offers": []}
                except Exception:
                    result = {"errors": [{"code": "unavailable", "message": "The store lookup could not finish. Try refreshing this track again."}], "offers": []}
                errors = result.get("errors") or []
                transient = errors and not result.get("offers") and all(
                    isinstance(error, dict) and error.get("code") in ("busy", "rate_limited", "unavailable") for error in errors)
                if transient and current["attempt"] < self.max_attempts and not event.is_set():
                    delay = self.retry_delay if any(error.get("code") == "rate_limited" for error in errors) else max(self.interval, min(self.retry_delay, 15.0))
                    self.next_lookup_at = max(self.next_lookup_at, time.monotonic() + delay)
                    with self.lock:
                        if event.is_set():
                            self._mark(current, "interrupted", "Store lookup cancelled. Previous results were kept.")
                            self._finish(current, "cancelled")
                            current = None
                            break
                        self._mark(current, "queued", "The store is busy or unavailable. Retrying shortly.")
                        self.queue_items.appendleft(current)
                        self.progress["message"] = "Waiting before retrying the store. You can cancel these lookups."
                    current = None
                    continue
                outcome = self._save_result(current, result, event)
                if outcome in ("cancelled", "stale"):
                    self._mark(current, "interrupted", "Track details changed or the lookup stopped. Refresh this track again.")
                self._finish(current, outcome)
                current = None
        except Exception:
            # Keep failures visible without logging track data, paths or credentials.
            with self.lock:
                self.progress["message"] = "Store lookups stopped unexpectedly. Refresh stores to try again."
                if current is not None:
                    try:
                        self._mark(current, "error", self.progress["message"])
                    except Exception:
                        pass
                    self._finish(current, "error")
                for task in self.queue_items:
                    try:
                        self._mark(task, "interrupted", self.progress["message"])
                    except Exception:
                        pass
                self.progress["failed"] += len(self.queue_items)
                self.progress["completed"] += len(self.queue_items)
                self.queue_items.clear()
                self.pending.clear()
        finally:
            with self.lock:
                self._finish_job(event)
