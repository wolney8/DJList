"""Explicit local macOS file/folder selection; never saves settings itself."""
import os
from pathlib import Path
import subprocess
import sys
import threading

from spotify_api import AppError


SCRIPTS = {
    "music": 'activate\nreturn POSIX path of (choose folder with prompt "Choose your DJ music folder")',
    "downloads": 'activate\nreturn POSIX path of (choose folder with prompt "Choose the folder containing your purchased downloads")',
    "xml": 'activate\nreturn POSIX path of (choose file with prompt "Choose your exported Rekordbox XML file" of type {"xml"})',
    "db": 'activate\nreturn POSIX path of (choose file with prompt "Choose the active Rekordbox master.db to back up" of type {"db"})',
    "backups": 'activate\nreturn POSIX path of (choose folder with prompt "Choose where to keep your Rekordbox database backups")',
    "cuegen": 'activate\nreturn POSIX path of (choose file with prompt "Choose the installed CueGen.Console executable")',
    "analysis": 'activate\nreturn POSIX path of (choose folder with prompt "Choose Rekordbox’s share folder containing analysis files")',
}


class NativePicker:
    def __init__(self, runner=None, platform=None):
        self.runner = runner or subprocess.run
        self.platform = sys.platform if platform is None else platform
        self._lock = threading.Lock()

    @property
    def supported(self):
        return self.platform == "darwin"

    def choose(self, kind):
        if not isinstance(kind, str) or kind not in SCRIPTS:
            raise AppError("Choose one of the displayed folders or Rekordbox files.")
        if not self.supported:
            raise AppError("Browse is available in the Mac app. You can paste a full path into Settings on this computer.", 409)
        if not self._lock.acquire(blocking=False):
            raise AppError("A file picker is already open on your Mac. Finish or cancel that selection first.", 409)
        try:
            # No user path, title or shell fragment is interpolated into this
            # fixed script. The standard native chooser returns the chosen path.
            result = self.runner(["/usr/bin/osascript", "-e", SCRIPTS[kind]],
                                 capture_output=True, text=True, timeout=120, check=False)
            if result.returncode:
                if "(-128)" in (result.stderr or ""):
                    return {"cancelled": True, "path": ""}
                raise AppError("The Mac file picker could not open. You can copy the path in Finder and paste it into Settings.", 409)
            path = result.stdout.rstrip("\r\n")
            if (not path or len(path) > 4096 or any(ord(c) < 32 or ord(c) == 127 for c in path)
                    or not Path(path).is_absolute()):
                raise AppError("The selected path could not be read. Copy its full path from Finder instead.", 409)
            if kind == "xml" and Path(path).suffix.lower() != ".xml":
                raise AppError("Choose the exported .xml file, rather than the live Rekordbox database.")
            if kind == "db" and Path(path).name != "master.db":
                raise AppError("Choose the active master.db file used by Rekordbox.")
            return {"cancelled": False, "path": os.path.normpath(path)}
        except subprocess.TimeoutExpired:
            raise AppError("The file picker timed out. Choose Browse again, or paste the full path from Finder.", 409) from None
        except (OSError, UnicodeError):
            raise AppError("The Mac file picker could not be read. Paste the full path from Finder instead.", 409) from None
        finally:
            self._lock.release()
