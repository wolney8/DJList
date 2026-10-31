"""Small, thread-safe activity ledger for the local UI; no filesystem work."""
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import wraps
import threading
import time


class ActivityRegistry:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.lock = threading.RLock()
        self.active = {}
        self.recent = []
        self.revision = 0

    @contextmanager
    def track(self, key, label, blocks_settings=True):
        owner = threading.get_ident()
        with self.lock:
            nested = owner in self.active
            if nested:
                previous = self.active[owner]["phase"]
                self.active[owner]["phase"] = label
            else:
                self.revision += 1
                self.active[owner] = {"id": "%s-%d" % (key, self.revision), "key": key,
                    "label": label, "phase": label, "running": True,
                    "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "_started": self.clock(), "blocks_settings": bool(blocks_settings),
                    "completed": None, "total": None, "unit": "", "message": ""}
        error = None
        try:
            yield
        except Exception as caught:
            error = str(caught) if hasattr(caught, "status") else "This action could not finish. Review the action and try again."
            raise
        finally:
            with self.lock:
                if nested:
                    self.active[owner]["phase"] = previous
                else:
                    item = self.active.pop(owner)
                    item.update(running=False, status="error" if error else "finished",
                                message=error or "Finished", elapsed_seconds=max(0, round(self.clock() - item.pop("_started"))))
                    self.recent.insert(0, item)
                    del self.recent[12:]
                    self.revision += 1

    def update(self, phase=None, completed=None, total=None, unit=None, message=None):
        with self.lock:
            item = self.active.get(threading.get_ident())
            if item is None:
                return
            for key, value in (("phase", phase), ("completed", completed), ("total", total), ("unit", unit), ("message", message)):
                if value is not None:
                    item[key] = value

    def running(self, key):
        with self.lock:
            return any(item["key"] == key for item in self.active.values())

    def snapshot(self):
        with self.lock:
            active = []
            for item in self.active.values():
                active.append({**{key: value for key, value in item.items() if key != "_started"},
                               "elapsed_seconds": max(0, round(self.clock() - item["_started"]))})
            return {"active": active, "recent": [dict(item) for item in self.recent], "revision": self.revision}


def reported(key, label, blocks_settings=True):
    def decorate(method):
        @wraps(method)
        def wrapped(self, *args, **kwargs):
            with self.activity.track(key, label, blocks_settings):
                return method(self, *args, **kwargs)
        return wrapped
    return decorate
