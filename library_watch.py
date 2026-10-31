"""Paced local checks for a changed XML export and an SSD reconnect.

Only configured paths are inspected. The callback owns refresh transactions.
No recursive scan is performed merely to check whether the drive is connected.
"""
from datetime import datetime, timezone
import os
from pathlib import Path
import stat
import threading
import time


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def configuration(settings):
    return (settings.get("mode", "real"), bool(settings.get("auto_library_refresh", False)),
            settings.get("music_root", ""), settings.get("rekordbox_xml", ""))


def _signature(value, directory=False):
    if not value:
        return ("not_set",)
    try:
        path = Path(value).expanduser()
        if not path.is_absolute():
            return ("unavailable",)
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode):
            return ("unavailable",)
        if directory:
            return ("available", info.st_dev, info.st_ino) if stat.S_ISDIR(info.st_mode) else ("unavailable",)
        if not stat.S_ISREG(info.st_mode):
            return ("unavailable",)
        return ("available", info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
    except (OSError, ValueError, TypeError, RuntimeError):
        return ("unavailable",)


class LibraryWatcher:
    def __init__(self, settings, refresh, invalidate, interval=5, clock=None):
        self.settings = settings
        self.refresh = refresh
        self.invalidate = invalidate
        self.interval = max(1, interval)
        self.clock = clock or time.monotonic
        self._lock = threading.RLock()
        self._tick_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._config = None
        self._music = self._xml = None
        self._pending_media = False
        self._pending_xml = None
        self._xml_since = 0
        self._retry_at = 0
        self._attempts = 0
        self._state = {"enabled": False, "running": False, "last_checked_at": None,
                       "last_refresh_at": None, "message": "Automatic library refresh has not started.",
                       "errors": [], "revision": 0}

    def status(self):
        with self._lock:
            return {**self._state, "errors": [dict(row) for row in self._state["errors"]]}

    def start(self):
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="djlist-library-watch", daemon=True)
            self._thread.start()

    def close(self):
        self._stop.set()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=3)

    def changed(self):
        """Let another browser view notice a manual inventory/settings change."""
        with self._lock:
            self._state["revision"] += 1

    def acknowledge(self, media=None, xml=None, errors=None):
        """Recover status after an explicit refresh, without waiting for remounts.

        XML acknowledgements use the fingerprint actually imported, so an
        atomic replacement just after a read is still noticed on the next tick.
        """
        settings = self.settings()
        errors = errors or []
        with self._lock:
            if media is not None:
                identity = media.get("root_identity")
                self._music = ("available", *identity) if identity else _signature(settings.get("music_root", ""), directory=True)
                self._pending_media = False
            if xml is not None:
                fingerprint = xml.get("source_fingerprint")
                if fingerprint and xml.get("source_path") == settings.get("rekordbox_xml"):
                    self._xml = ("available", *(fingerprint[key] for key in ("device", "inode", "size", "mtime_ns", "ctime_ns")))
                else:
                    self._xml = _signature(settings.get("rekordbox_xml", ""))
                self._pending_xml = None
            attempted = {row.get("source") for row in errors}
            if media is not None:
                attempted.add("media")
            if xml is not None:
                attempted.add("xml")
            self._state["errors"] = [row for row in self._state["errors"] if row["source"] not in attempted and row["source"] != "automatic"] + [dict(row) for row in errors]
            if media is not None or xml is not None:
                self._attempts = 0
                self._retry_at = 0
                self._state["last_refresh_at"] = _now()
            self._state["message"] = ("Some library data could not refresh. The previous usable inventory is kept."
                                      if self._state["errors"] else "Library refreshed. Watching for the next XML export or drive reconnection.")
            self._state["revision"] += 1

    def _run(self):
        while not self._stop.wait(self.interval):
            try:
                self.tick()
            except Exception:
                # Keep the loop alive without logging private paths or data.
                with self._lock:
                    self._state.update(running=False, message="Automatic refresh could not finish. Use Check setup to review your folders.")
                    self._state["revision"] += 1

    def tick(self):
        if self._stop.is_set() or not self._tick_lock.acquire(blocking=False):
            return
        try:
            self._tick()
        finally:
            self._tick_lock.release()

    def _tick(self):
        settings = self.settings()
        config = configuration(settings)
        enabled = config[0] == "real" and config[1]
        now = self.clock()
        with self._lock:
            if config != self._config:
                self._config = config
                self._music = self._xml = None
                self._pending_media = False
                self._pending_xml = None
                self._attempts = 0
                self._retry_at = 0
                self._state["errors"] = []
                self._state["revision"] += 1
            self._state.update(enabled=enabled, last_checked_at=_now())
            if not enabled:
                self._state.update(running=False, message="Automatic library refresh is off." if config[0] == "real" else "Automatic library refresh is paused in the demo.")
                return

        music = _signature(config[2], directory=True)
        xml = _signature(config[3])
        invalidate = False
        with self._lock:
            if music != self._music:
                self._music = music
                self._pending_media = music[0] == "available"
                self._attempts = 0
                self._retry_at = 0
                self._state["revision"] += 1
                invalidate = True
                self._state["errors"] = [row for row in self._state["errors"] if row["source"] != "media"]
            if xml != self._xml:
                self._xml = xml
                self._pending_xml = xml if xml[0] == "available" else None
                self._xml_since = now
                self._attempts = 0
                self._retry_at = 0
                self._state["revision"] += 1
                self._state["errors"] = [row for row in self._state["errors"] if row["source"] != "xml"]
            media = self._pending_media
            read_xml = self._pending_xml is not None and now - self._xml_since >= self.interval
            if music[0] != "available":
                self._state["message"] = "Music folder unavailable. Saved links and XML records are kept; reconnect the drive or check Settings."
            elif xml[0] == "unavailable":
                self._state["message"] = "The saved XML file is unavailable. The last imported snapshot is kept."
            elif self._pending_xml and not read_xml:
                self._state["message"] = "Waiting for the XML export to finish changing…"
            elif not self._state["errors"]:
                self._state["message"] = "Watching for a new XML export and music-drive reconnection." if config[3] else "Watching for music-drive reconnection. Save an XML path to watch exports too."
            should_refresh = (media or read_xml) and now >= self._retry_at and self._attempts < 3
            if should_refresh:
                self._state.update(running=True, message="Refreshing library files and metadata…")
                self._state["revision"] += 1
        if invalidate:
            self.invalidate()
        if not should_refresh:
            return
        try:
            result = self.refresh(config, media, read_xml)
        except Exception:
            result = {"errors": [{"source": "automatic", "message": "Automatic refresh could not finish. Use Check setup and try a manual refresh."}]}
        with self._lock:
            self._state["running"] = False
            if result.get("deferred"):
                self._retry_at = self.clock() + self.interval
                self._state["message"] = "Another action is running. Automatic refresh will try again shortly."
                return
            errors = result.get("errors") or []
            sources = {row.get("source") for row in errors}
            if media and "media" not in sources and "automatic" not in sources:
                self._pending_media = False
            if read_xml and "xml" not in sources and "automatic" not in sources:
                self._pending_xml = None
            self._attempts = self._attempts + 1 if errors else 0
            self._retry_at = self.clock() + 30 if errors else 0
            kept = [row for row in self._state["errors"] if not ((media and row["source"] == "media") or (read_xml and row["source"] == "xml") or row["source"] == "automatic")]
            self._state["errors"] = kept + [dict(row) for row in errors]
            if result.get("media") is not None or result.get("xml") is not None:
                self._state["last_refresh_at"] = _now()
            self._state["message"] = ("Some library data could not refresh. The last usable inventory is kept; check setup or refresh again."
                                      if errors else "Library refreshed. Watching for the next XML export or drive reconnection.")
            self._state["revision"] += 1
