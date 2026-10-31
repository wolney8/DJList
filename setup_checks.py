"""Read-only checks of the paths explicitly saved in DJList Settings."""
from datetime import datetime, timezone
import os
from pathlib import Path
import stat

from library_inventory import MAX_XML_BYTES, xml_fingerprint


def _folder(path, identity, label):
    result = {"id": identity, "label": label, "path": path, "status": "not_set",
              "message": "Choose a folder in Settings."}
    if not path:
        return result
    try:
        folder = Path(path).expanduser()
        if not folder.is_absolute() or folder == Path(folder.anchor) or ".." in folder.parts or folder.is_symlink() or not folder.is_dir():
            raise OSError()
        with os.scandir(folder) as entries:
            next(entries, None)
        result.update(status="ok", message="Folder is available and can be read. Subfolders are included when scanning.")
    except (OSError, ValueError, RuntimeError):
        result.update(status="error", message="Folder is unavailable or unreadable. Reconnect its drive and check the saved path. Choose the actual folder, rather than an alias.")
    return result


def check_setup(settings, library, automatic):
    checked = datetime.now(timezone.utc)
    summary = library.status(settings["music_root"])
    checks = [_folder(settings["music_root"], "music", "Music folder")]
    xml_path = settings.get("rekordbox_xml", "")
    xml = {"id": "xml", "label": "Rekordbox XML export", "path": xml_path,
           "status": "not_set", "message": "Choose a saved XML export in Settings to enable repeat refreshes. An uploaded snapshot remains available in Review library."}
    if xml_path:
        try:
            path = Path(xml_path).expanduser()
            if not path.is_absolute() or path.suffix.lower() != ".xml" or path.is_symlink():
                raise OSError()
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
            with os.fdopen(os.open(str(path), flags), "rb") as handle:
                info = os.fstat(handle.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_XML_BYTES or not info.st_size:
                    raise OSError()
                handle.read(1)
            modified = datetime.fromtimestamp(info.st_mtime, timezone.utc)
            xml.update(status="ok", modified_at=modified.isoformat(timespec="seconds"),
                       message="XML file is readable. Refresh XML imports its contents; this check does not create a Rekordbox export.")
            if summary.get("source_path") != str(path):
                xml.update(status="warning", message="This saved XML path has not supplied the current inventory. Choose Refresh XML to read it.")
            elif summary.get("source_fingerprint") != xml_fingerprint(info):
                xml.update(status="warning", message="This XML file has changed or has not been verified against the saved snapshot. Choose Refresh XML to validate and import the current file.")
            elif any(row.get("source") == "xml" for row in automatic.get("errors", [])):
                xml.update(status="warning", message="The last automatic XML refresh failed. The prior snapshot is kept; export again and choose Refresh XML.")
            elif (checked - modified).total_seconds() > 7 * 86400:
                xml.update(status="warning", message="The XML file was last changed more than 7 days ago. Export again if your Rekordbox collection has changed.")
        except (OSError, ValueError, OverflowError, RuntimeError):
            xml.update(status="error", message="The saved XML could not be read. Choose a nonempty .xml export under 20 MB and reconnect its drive if needed. The previous snapshot is kept.")
    checks.append(xml)
    checks.append(_folder(settings.get("download_root", ""), "downloads", "Purchased-download inbox"))
    checks.append({"id": "metadata", "label": "Audio-file metadata", "path": "", "status": "ok",
                   "message": "The bundled reader can read supported file tags and duration without changing audio. Missing tags stay unknown; DJList does not analyse BPM or key."})
    return {"checked_at": checked.isoformat(timespec="seconds"), "checks": checks, "library": summary,
            "summary": {"available": summary.get("available", 0), "missing": summary.get("missing", 0),
                        "outside_root": summary.get("outside_root", 0), "xml_entries": summary.get("entry_count", 0),
                        "media_files": summary.get("scan_count", 0)}, "automatic": automatic}
