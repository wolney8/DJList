#!/usr/bin/env python3
"""DJList local app. Python 3.9+. No extra Python packages are required.

Run: python3 app.py
Test data location: python3 app.py --data-dir /tmp/djlist-test --no-browser
The app binds to 127.0.0.1 only. Spotify credentials stay in memory for this run.
The selected music folder is scanned read-only. Rekordbox's database is never
opened or changed. Cue generation and audio-file moves are not implemented.
"""
import argparse
from contextlib import contextmanager
import csv
from datetime import datetime, timezone
import hashlib
import html
from http.client import HTTPConnection, HTTPException
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import mimetypes
import os
from pathlib import Path
import re
import secrets
from socketserver import TCPServer
import sqlite3
import sys
import threading
from urllib.parse import parse_qs, unquote, urlencode, urlsplit
import webbrowser

from spotify_api import AppError, SpotifyClient, begin_authorization, callback_code, exchange_code, parse_source
from catalog import CatalogClient
from enrichment import StoreLookupQueue
from library_inventory import LibraryInventory

VERSION = "0.3.0"
API_VERSION = 3
DEFAULTS = {
    "spotify_client_id": "",
    "playlist_url": "", "music_root": "~/Music/DJ Music",
    "rekordbox_db": "", "rekordbox_xml": "", "auto_store_lookup": True,
}
MANUAL = {"owned": False, "tags": "", "musical_key": "", "bpm": "", "energy": "",
          "purchase_url": "", "local_file": "", "cue_notes": "", "notes": "", "prep_status": "wishlist"}
STATUSES = {"wishlist", "bought", "downloaded", "imported", "reviewed"}
AUDIO_EXTENSIONS = {".mp3", ".aiff", ".aif", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".alac"}
MAX_BODY = 20 * 1024 * 1024
SEARCH_PROVIDERS = (
    ("bandcamp", "Bandcamp", "bandcamp.com", "https://bandcamp.com/search"),
    ("beatport", "Beatport", "beatport.com", "https://www.beatport.com/search"),
    ("traxsource", "Traxsource", "traxsource.com", None),
)


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def default_data_dir():
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/DJList"
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "DJList"
    return Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share"))) / "djlist"


def safe_cell(value):
    value = "" if value is None else str(value)
    return "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r", "\n")) else value


def normalise(value):
    return " ".join(re.findall(r"[^\W_]+", value.casefold(), flags=re.UNICODE))


def search_plan(query):
    """Build external search links only. No provider is queried by this function."""
    providers = []
    for identity, name, domain, native_url in SEARCH_PROVIDERS:
        url = native_url + "?" + urlencode({"q": query}) if native_url else \
            "https://www.google.com/search?" + urlencode({"q": "site:%s %s" % (domain, query)})
        providers.append({"id": identity, "name": name, "url": url,
                          "method": "native_search" if native_url else "web_search"})
    sites = " OR ".join("site:" + domain for _, _, domain, _ in SEARCH_PROVIDERS)
    return {"query": query, "providers": providers,
            "combined_url": "https://www.google.com/search?" + urlencode({"q": query + " (" + sites + ")"})}


def provider_search(query):
    if not isinstance(query, str) or any(ord(character) < 32 or ord(character) == 127 for character in query):
        raise AppError("Enter an artist and song title without control characters.")
    query = query.strip()
    if not query:
        raise AppError("Enter an artist, song title or mix name to search.")
    if len(query) > 500:
        raise AppError("Keep the search to 500 characters or fewer.")
    return search_plan(query)


def search_links(track):
    query = (track.get("artists", "") + " " + track.get("title_and_version", "")).strip()
    return {provider["id"]: provider["url"] for provider in search_plan(query)["providers"]}


def snapshot_web_assets(directory):
    """Keep one process's UI and API together when its folder is updated."""
    root = Path(directory).resolve()
    assets = {}
    digest = hashlib.sha256((VERSION + ":" + str(API_VERSION)).encode("ascii"))
    for target in sorted(root.rglob("*")):
        if not target.is_file() or target.is_symlink():
            continue
        try:
            target.resolve().relative_to(root)
        except ValueError:
            continue
        relative = target.relative_to(root).as_posix()
        payload = target.read_bytes()
        mime = mimetypes.guess_type(relative)[0] or "application/octet-stream"
        if mime.startswith("text/") or mime == "application/javascript":
            mime += "; charset=utf-8"
        assets[relative] = (payload, mime)
        digest.update(relative.encode("utf-8") + b"\0" + payload)
    if "index.html" not in assets:
        raise AppError("DJList's interface files are missing. Unzip the complete app folder and open its launcher.")
    return assets, digest.hexdigest()[:16]


def running_instance(port):
    """Inspect only this machine's occupied port, without proxies or redirects."""
    connection = HTTPConnection("127.0.0.1", port, timeout=2)
    try:
        connection.request("GET", "/api/health", headers={"Host": "127.0.0.1:%d" % port})
        response = connection.getresponse()
        server = response.getheader("Server", "")
        match = re.search(r"(?:^|\s)DJList/([0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?)", server)
        payload = response.read(32769)
        if response.status == 200 and len(payload) <= 32768:
            try:
                health = json.loads(payload)
            except (ValueError, UnicodeError):
                health = None
            if isinstance(health, dict) and health.get("app") == "DJList" and match:
                directory = health.get("app_dir")
                if not isinstance(directory, str) or len(directory) > 4096 or any(ord(char) < 32 for char in directory):
                    directory = None
                return {"version": match.group(1), "app_dir": directory, "legacy": False}
        if match:
            return {"version": match.group(1), "app_dir": None, "legacy": True}
    except (OSError, ValueError, HTTPException):
        pass
    finally:
        connection.close()
    return None


def occupied_port_message(port, instance):
    if instance:
        lines = ["DJList %s is already running on port %d." % (instance["version"], port)]
        if instance.get("app_dir"):
            lines.append("Running app folder: " + instance["app_dir"])
        lines.extend(["This launcher is DJList %s." % VERSION,
                      "In the earlier DJList Terminal window, press Control+C to stop it.",
                      "Then open this updated launcher and reload the browser page.",
                      "Closing only the browser tab does not stop DJList."])
        return "\n".join(lines)
    return ("Port %d is already in use or unavailable. DJList could not start.\n"
            "If an earlier DJList Terminal window is open, press Control+C there, then open this launcher again.\n"
            "Otherwise check which application is using that port before changing it.") % port


class Store:
    def __init__(self, directory):
        self.directory = Path(directory).expanduser().resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "djlist.sqlite3"
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS tracks (
                    id INTEGER PRIMARY KEY, namespace TEXT NOT NULL, spotify_id TEXT NOT NULL,
                    source TEXT NOT NULL, metadata TEXT NOT NULL, annotations TEXT NOT NULL,
                    first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, in_source INTEGER,
                    UNIQUE(namespace, spotify_id));
                CREATE TABLE IF NOT EXISTS files (
                    path TEXT PRIMARY KEY, root TEXT NOT NULL, size INTEGER NOT NULL,
                    mtime REAL NOT NULL, title TEXT NOT NULL DEFAULT '', artist TEXT NOT NULL DEFAULT '',
                    musical_key TEXT NOT NULL DEFAULT '', bpm TEXT NOT NULL DEFAULT '',
                    cue_count INTEGER NOT NULL DEFAULT 0, origin TEXT NOT NULL DEFAULT 'scan');
            """)
            for key, value in {**DEFAULTS, "mode": "real", "source_id": "", "last_media_refresh": None,
                               "last_media_skipped": 0}.items():
                db.execute("INSERT OR IGNORE INTO settings VALUES (?,?)", (key, json.dumps(value)))
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    @contextmanager
    def connect(self):
        db = sqlite3.connect(str(self.path), timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def settings(self):
        with self.connect() as db:
            return {r["key"]: json.loads(r["value"]) for r in db.execute("SELECT * FROM settings")}

    def put_settings(self, updates):
        with self.connect() as db:
            for key, value in updates.items():
                db.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (key, json.dumps(value)))

    def rows(self, namespace):
        with self.connect() as db:
            result = list(db.execute("SELECT * FROM tracks WHERE namespace=? ORDER BY id", (namespace,)))
        tracks = []
        for row in result:
            track = {**json.loads(row["metadata"]), **MANUAL, **json.loads(row["annotations"]),
                     "id": row["id"], "spotify_id": row["spotify_id"], "source": row["source"],
                     "first_seen": row["first_seen"], "last_seen": row["last_seen"],
                     "in_source": None if row["in_source"] is None else bool(row["in_source"])}
            track["search_links"] = search_links(track)
            track["file_available"] = bool(namespace == "real" and track["local_file"] and Path(track["local_file"]).is_file())
            tracks.append(track)
        return tracks

    def get(self, identity, namespace):
        with self.connect() as db:
            row = db.execute("SELECT * FROM tracks WHERE id=? AND namespace=?", (identity, namespace)).fetchone()
        if row is None:
            raise AppError("This track is not in the current collection.", 404)
        return row

    def patch(self, identity, namespace, patch, confirmed_file=False):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT annotations FROM tracks WHERE id=? AND namespace=?", (identity, namespace)).fetchone()
            if row is None:
                raise AppError("This track is not in the current collection.", 404)
            current = {**MANUAL, **json.loads(row["annotations"])}
            changes = dict(patch)
            if confirmed_file:
                inventory = db.execute("SELECT * FROM files WHERE path=?", (changes["local_file"],)).fetchone()
                changes.setdefault("owned", True)
                imported = inventory is not None and inventory["origin"] == "rekordbox_xml"
                if changes["local_file"] != current["local_file"]:
                    changes.setdefault("prep_status", "imported" if imported else "downloaded")
                if imported:
                    for key in ("musical_key", "bpm"):
                        if not current[key]:
                            changes.setdefault(key, inventory[key])
            annotations = {**current, **changes}
            db.execute("UPDATE tracks SET annotations=? WHERE id=? AND namespace=?", (json.dumps(annotations), identity, namespace))
        return annotations

    def upsert(self, tracks, source, namespace="real", unknown=0):
        unique = {track["spotify_id"]: track for track in tracks}
        timestamp, added = now(), 0
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if namespace == "real":
                bound = json.loads(db.execute("SELECT value FROM settings WHERE key='source_id'").fetchone()[0])
                if bound and bound != source:
                    raise AppError("This collection is already linked to another playlist. Keep the original playlist link.", 409)
                db.execute("UPDATE settings SET value=? WHERE key='source_id'", (json.dumps(source),))
            db.execute("UPDATE tracks SET in_source=? WHERE namespace=? AND source=?", (None if unknown else 0, namespace, source))
            for identity, track in unique.items():
                row = db.execute("SELECT id FROM tracks WHERE namespace=? AND spotify_id=?", (namespace, identity)).fetchone()
                if row:
                    db.execute("UPDATE tracks SET metadata=?,last_seen=?,in_source=1 WHERE id=?", (json.dumps(track), timestamp, row["id"]))
                else:
                    db.execute("INSERT INTO tracks(namespace,spotify_id,source,metadata,annotations,first_seen,last_seen,in_source) VALUES (?,?,?,?,?,?,?,1)",
                               (namespace, identity, source, json.dumps(track), json.dumps(MANUAL), timestamp, timestamp))
                    added += 1
        return {"added": added, "updated": len(unique) - added, "total": len(unique), "unavailable": unknown}


class Application:
    def __init__(self, data_dir, port=8888, web_dir=None):
        self.store = Store(data_dir)
        self.port = port
        self.origin = "http://127.0.0.1:%d" % port
        self.csrf_token = secrets.token_urlsafe(32)
        self.tokens = None
        self.token_client_id = None
        self.pending = None
        self.last_auth_error = ""
        self.auth_lock = threading.RLock()
        self.work_lock = threading.RLock()
        self.catalog = CatalogClient()
        self.app_dir = Path(__file__).resolve().parent
        self.web = Path(web_dir) if web_dir is not None else self.app_dir / "web"
        self.assets, self.build_id = snapshot_web_assets(self.web)
        self.started_at = now()
        self.library = LibraryInventory(self.store)
        self.lookup = StoreLookupQueue(self.store, lambda query, reference, force=False: self.catalog.search(query, reference, force=force))

    def close(self):
        self.lookup.close()

    def runtime(self):
        return {"version": VERSION, "api_version": API_VERSION, "build_id": self.build_id,
                "started_at": self.started_at, "app_dir": str(self.app_dir), "static_snapshot": True}

    def health(self):
        return {"app": "DJList", **self.runtime(), "pid": os.getpid()}

    def static_asset(self, path):
        relative = "index.html" if path == "/" else unquote(path.lstrip("/"))
        if relative.startswith("/") or "\\" in relative or any(part in (".", "..") for part in relative.split("/")):
            raise AppError("File not found.", 404)
        asset = self.assets.get(relative)
        if asset is None:
            raise AppError("File not found.", 404)
        return asset

    def namespace(self):
        return self.store.settings()["mode"]

    def state(self):
        settings = self.store.settings()
        namespace = settings["mode"]
        tracks = self.lookup.attach(self.store.rows(namespace), namespace)
        root = Path(settings["music_root"]).expanduser()
        library = self.library.status(settings["music_root"]) if namespace == "real" else {"entry_count": 0, "demo": True}
        library["media_refreshed_at"] = settings.get("last_media_refresh") if namespace == "real" else None
        library["media_skipped"] = settings.get("last_media_skipped", 0) if namespace == "real" else 0
        suggestions = self.library.matches(tracks, settings["music_root"]) if namespace == "real" else []
        with self.auth_lock:
            connected = bool(self.tokens and self.tokens.get("access_token"))
        return {"settings": {key: settings[key] for key in DEFAULTS}, "tracks": tracks,
                "stats": {"total": len(tracks), "owned": sum(t["owned"] for t in tracks),
                          "ready": sum(t["prep_status"] == "reviewed" for t in tracks),
                          "missing": sum(bool(t["local_file"]) and not t["file_available"] for t in tracks)},
                "connection": {"connected": connected, "needs_sign_in": not connected,
                               "callback_url": self.origin + "/callback", "session_only": True,
                               "last_error": self.last_auth_error},
                "drive": {"available": root.is_dir(), "music_root": str(root),
                          "rekordbox_db_exists": bool(settings["rekordbox_db"] and Path(settings["rekordbox_db"]).expanduser().is_file())},
                "library": library, "suggestions": suggestions,
                "store_job": self.lookup.status() if namespace == "real" else StoreLookupQueue._idle(),
                "mode": namespace, "csrf_token": self.csrf_token, "version": VERSION,
                "api_version": API_VERSION, "runtime": self.runtime(),
                "capabilities": {"spotify_sync": True, "file_scan": True, "rekordbox_xml_import": True,
                                 "provider_search": True,
                                 "catalog_search": True,
                                 "automatic_store_lookup": True, "library_review": True, "library_refresh": True,
                                 "preview_audio": "assets/download-on-itunes.svg" in self.assets,
                                 "cue_generation": False, "rekordbox_xml_export": False, "file_moves": False}}

    def change_settings(self, updates):
        if not self.work_lock.acquire(blocking=False):
            raise AppError("A refresh or scan is running. Wait before changing settings.", 409)
        try:
            return self._change_settings(updates)
        finally:
            self.work_lock.release()

    def _change_settings(self, updates):
        if not isinstance(updates, dict) or set(updates) - set(DEFAULTS):
            raise AppError("Only the displayed settings can be changed.")
        clean = {}
        for key, value in updates.items():
            if key == "auto_store_lookup":
                if not isinstance(value, bool):
                    raise AppError("Automatic store lookup must be checked or unchecked.")
                clean[key] = value
                continue
            if not isinstance(value, str) or len(value) > 4096 or "\x00" in value:
                raise AppError("A setting contains an invalid value.")
            clean[key] = value.strip()
        if clean.get("spotify_client_id") and not re.fullmatch(r"[A-Fa-f0-9]{32}", clean["spotify_client_id"]):
            raise AppError("The Spotify Client ID should contain 32 letters/numbers. Do not enter the secret.")
        old = self.store.settings()
        if "playlist_url" in clean and clean["playlist_url"]:
            source, url = parse_source(clean["playlist_url"])
            if old["source_id"] and source != old["source_id"]:
                raise AppError("This collection is linked to another playlist. Keep its original playlist link.", 409)
            clean["playlist_url"] = url
        for key in ("music_root", "rekordbox_db", "rekordbox_xml"):
            if key in clean:
                if key in ("rekordbox_db", "rekordbox_xml") and not clean[key]:
                    continue
                if not clean[key] or not Path(clean[key]).expanduser().is_absolute():
                    raise AppError("Use an absolute folder/file path for " + key.replace("_", " ") + ".")
                clean[key] = str(Path(clean[key]).expanduser())
                if key == "rekordbox_xml" and Path(clean[key]).suffix.lower() != ".xml":
                    raise AppError("Choose the path to a Rekordbox .xml export.")
        self.store.put_settings(clean)
        if clean.get("music_root", old["music_root"]) != old["music_root"]:
            self.library.invalidate()
        if clean.get("auto_store_lookup") is False:
            self.lookup.cancel()
        if clean.get("spotify_client_id", old["spotify_client_id"]) != old["spotify_client_id"]:
            with self.auth_lock:
                self.tokens, self.pending, self.token_client_id = None, None, None
        return {"saved": True}

    def connect_spotify(self):
        if self.namespace() == "demo":
            raise AppError("Leave the demo before connecting your Spotify account.", 409)
        client_id = self.store.settings()["spotify_client_id"]
        with self.auth_lock:
            self.pending, url = begin_authorization(client_id, self.origin + "/callback")
            self.last_auth_error = ""
        return {"authorization_url": url}

    def oauth_callback(self, query):
        with self.auth_lock:
            try:
                code = callback_code(query, self.pending)
                pending = self.pending
                self.pending = None
                tokens = exchange_code(code, pending)
                if self.store.settings()["spotify_client_id"] != pending["client_id"]:
                    raise AppError("The Client ID changed during sign-in. Connect Spotify again.")
                self.tokens, self.token_client_id = tokens, pending["client_id"]
                self.last_auth_error = ""
            except AppError as error:
                self.last_auth_error = str(error)
                raise

    def sync(self):
        if self.namespace() != "real":
            raise AppError("Leave the demo to refresh your Spotify playlist.", 409)
        if not self.work_lock.acquire(blocking=False):
            raise AppError("Another refresh or scan is running. Please wait for it to finish.", 409)
        try:
            settings = self.store.settings()
            source, _ = parse_source(settings["playlist_url"])
            with self.auth_lock:
                if not self.tokens or self.token_client_id != settings["spotify_client_id"]:
                    raise AppError("Connect Spotify before refreshing this playlist.", 401)
                client = SpotifyClient(self.token_client_id, self.tokens)
                try:
                    tracks, unknown = client.tracks(source)
                except AppError as error:
                    if error.status == 401:
                        self.tokens = None
                    raise
            current = self.store.settings()
            if current["playlist_url"] != settings["playlist_url"] or current["spotify_client_id"] != settings["spotify_client_id"] or current["mode"] != "real":
                raise AppError("Settings changed during refresh. Please refresh again.", 409)
            result = self.store.upsert(tracks, source, unknown=unknown)
            if self.store.settings()["auto_store_lookup"]:
                try:
                    queued = self.lookup.enqueue()
                    result["store_lookup_queued"] = queued["queued"]
                    result["store_job"] = queued["job"]
                except AppError as error:
                    # Spotify's successful merge must not be reported as a failed
                    # sync merely because an earlier catalogue job is stopping.
                    result["store_lookup_queued"] = 0
                    result["store_lookup_error"] = str(error)
            return result
        finally:
            self.work_lock.release()

    def demo(self, enabled):
        if not isinstance(enabled, bool):
            raise AppError("Choose whether to enter or leave the demo.")
        if enabled:
            self.lookup.cancel()
        if enabled and not self.store.rows("demo"):
            examples = [
                ("Night Window", "Example Artist", "Original Mix"),
                ("Rain After Midnight", "Demo Collective", "Extended Mix"),
                ("Blue Platform", "Example Artist", "Example Remix"),
                ("Slow Tide", "Demo Collective", "Original Mix"),
            ]
            tracks = [{"spotify_id": "demo-%d" % i, "artists": artist, "title_and_version": "%s (%s)" % (title, mix),
                       "album": "Fictional demo collection", "duration": "5:12", "isrc": "", "spotify_url": ""}
                      for i, (title, artist, mix) in enumerate(examples, 1)]
            self.store.upsert(tracks, "demo", "demo")
            rows = self.store.rows("demo")
            self.store.patch(rows[1]["id"], "demo", {"owned": True, "tags": "future garage; warm-up", "musical_key": "C minor", "bpm": "132", "prep_status": "bought"})
            self.store.patch(rows[3]["id"], "demo", {"tags": "jungle; melancholic", "energy": "3", "notes": "Fictional example. Your real collection stays separate."})
        self.store.put_settings({"mode": "demo" if enabled else "real"})
        return {"mode": "demo" if enabled else "real"}

    def checked_file(self, value):
        if not isinstance(value, str) or not value or "\x00" in value:
            raise AppError("Choose an audio file in your configured music folder.")
        root = Path(self.store.settings()["music_root"]).expanduser()
        if not root.is_dir():
            raise AppError("Your music folder is unavailable. Connect your music drive and check the folder in Settings.", 409)
        root = root.resolve()
        candidate = Path(value).expanduser()
        if not candidate.is_absolute() or candidate.is_symlink():
            raise AppError("Choose an absolute audio-file path inside your music folder.")
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(root)
        except (OSError, ValueError):
            raise AppError("That file is missing or outside your configured music folder.") from None
        if not resolved.is_file() or resolved.suffix.lower() not in AUDIO_EXTENSIONS:
            raise AppError("Choose a supported audio file, such as MP3, WAV, AIFF or FLAC.")
        return resolved

    def edit_track(self, identity, updates):
        if not isinstance(updates, dict) or set(updates) - set(MANUAL):
            raise AppError("Only the editable track fields can be changed.")
        namespace = self.namespace()
        self.store.get(identity, namespace)
        clean = {}
        for key, value in updates.items():
            if key == "owned":
                if not isinstance(value, bool):
                    raise AppError("Owned must be checked or unchecked.")
            elif not isinstance(value, str) or len(value) > (20000 if key in ("notes", "cue_notes") else 4096) or "\x00" in value:
                raise AppError("An editable field contains an invalid value.")
            else:
                value = value.strip()
            if key == "prep_status" and value not in STATUSES:
                raise AppError("Choose a valid preparation status.")
            if key == "purchase_url" and value:
                parsed = urlsplit(value)
                if parsed.scheme not in ("https", "http") or not parsed.netloc or parsed.username or parsed.password:
                    raise AppError("Use an http or https purchase link.")
            if key == "local_file" and value and namespace == "real":
                value = str(self.checked_file(value))
                self.library.prepare_link(value, self.store.settings()["music_root"])
            clean[key] = value
        saved = self.store.patch(identity, namespace, clean,
                                 confirmed_file=namespace == "real" and bool(clean.get("local_file")))
        return {"saved": True, "prep_status": saved["prep_status"], "musical_key": saved["musical_key"],
                "bpm": saved["bpm"], "local_file": saved["local_file"], "owned": saved["owned"]}

    def link(self, identity, path):
        if self.namespace() != "real":
            raise AppError("Real files can only be linked in your real collection.", 409)
        file = self.checked_file(path)
        self.library.prepare_link(str(file), self.store.settings()["music_root"])
        saved = self.store.patch(identity, "real", {"local_file": str(file)}, confirmed_file=True)
        return {"linked": True, "path": str(file), "prep_status": saved["prep_status"],
                "musical_key": saved["musical_key"], "bpm": saved["bpm"]}

    def suggestions(self):
        return self.library.matches(self.store.rows("real"), self.store.settings()["music_root"])

    def scan(self):
        if self.namespace() != "real":
            raise AppError("Leave the demo before scanning your SSD.", 409)
        if not self.work_lock.acquire(blocking=False):
            raise AppError("Another refresh or scan is running. Please wait.", 409)
        try:
            root = Path(self.store.settings()["music_root"]).expanduser()
            if not root.is_dir():
                raise AppError("Your music folder is unavailable. Connect your music drive and check Settings.", 409)
            root = root.resolve()
            files, skipped, scan_errors = [], 0, []
            for folder, directories, names in os.walk(str(root), followlinks=False, onerror=scan_errors.append):
                directories[:] = [d for d in directories if not d.startswith(".") and not (Path(folder) / d).is_symlink()]
                for name in names:
                    file = Path(folder) / name
                    if file.suffix.lower() not in AUDIO_EXTENSIONS or file.is_symlink():
                        continue
                    try:
                        resolved = file.resolve(strict=True)
                        resolved.relative_to(root)
                        st = resolved.stat()
                    except (OSError, ValueError):
                        skipped += 1
                        continue
                    files.append((str(resolved), str(root), st.st_size, st.st_mtime, file.stem))
                    if len(files) > 50000:
                        raise AppError("This scan exceeded 50,000 audio files. Choose a smaller music folder.", 413)
            if not root.is_dir():
                raise AppError("The SSD disconnected during scanning. Please reconnect and retry.", 409)
            with self.store.connect() as db:
                existing = {r[0] for r in db.execute("SELECT path FROM files WHERE root=?", (str(root),))}
                seen = set()
                for path, root_name, size, mtime, title in files:
                    seen.add(path)
                    db.execute("INSERT INTO files(path,root,size,mtime,title) VALUES (?,?,?,?,?) ON CONFLICT(path) DO UPDATE SET root=excluded.root,size=excluded.size,mtime=excluded.mtime", (path, root_name, size, mtime, title))
                for missing in (existing - seen if not scan_errors and not skipped else set()):
                    db.execute("DELETE FROM files WHERE path=?", (missing,))
                db.execute("INSERT OR REPLACE INTO settings VALUES ('last_media_refresh', ?)", (json.dumps(now()),))
                db.execute("INSERT OR REPLACE INTO settings VALUES ('last_media_skipped', ?)", (json.dumps(skipped + len(scan_errors)),))
            self.library.invalidate()
            return {"indexed": len(files), "skipped": skipped + len(scan_errors), "suggestions": self.suggestions()}
        finally:
            self.library.invalidate()
            self.work_lock.release()

    def import_xml(self, xml, source_name="Uploaded XML"):
        if self.namespace() != "real":
            raise AppError("Leave the demo to import your Rekordbox inventory.", 409)
        if not self.work_lock.acquire(blocking=False):
            raise AppError("Another refresh or scan is running. Please wait.", 409)
        try:
            result = self.library.import_xml(xml, self.store.settings()["music_root"], source_name=source_name)
            return {**result, "suggestions": self.suggestions()}
        finally:
            self.work_lock.release()

    def refresh_libraries(self, media=True, xml=True):
        if not isinstance(media, bool) or not isinstance(xml, bool) or not (media or xml):
            raise AppError("Choose the media folder, Rekordbox XML, or both to refresh.")
        if self.namespace() != "real":
            raise AppError("Leave the demo before refreshing your libraries.", 409)
        if not self.work_lock.acquire(blocking=False):
            raise AppError("Another refresh or scan is running. Please wait.", 409)
        try:
            result = {"media": None, "xml": None, "errors": []}
            if media:
                try:
                    result["media"] = self.scan()
                    if result["media"]["skipped"]:
                        result["errors"].append({"source": "media", "message":
                            "Some folders or files could not be read. Previous inventory entries were kept; check the media scan before buying duplicates."})
                except (AppError, OSError) as error:
                    result["errors"].append({"source": "media", "message": str(error)})
            if xml:
                try:
                    settings = self.store.settings()
                    if not settings["rekordbox_xml"]:
                        raise AppError("Set your Rekordbox XML path in Settings for one-click refresh, or choose Import XML.")
                    result["xml"] = self.library.refresh_xml(settings["rekordbox_xml"], settings["music_root"])
                except (AppError, OSError) as error:
                    result["errors"].append({"source": "xml", "message": str(error)})
            settings = self.store.settings()
            self.library.invalidate()
            result["library"] = self.library.status(settings["music_root"])
            result["library"]["media_refreshed_at"] = settings.get("last_media_refresh")
            result["library"]["media_skipped"] = settings.get("last_media_skipped", 0)
            result["suggestions"] = self.suggestions()
            result["partial"] = bool(result["errors"])
            return result
        finally:
            self.work_lock.release()

    def library_review(self, parameters):
        if self.namespace() != "real":
            raise AppError("Leave the demo to review your real libraries.", 409)
        if set(parameters) - {"q", "page", "page_size", "filter"} or any(len(value) != 1 for value in parameters.values()):
            raise AppError("Choose one value for each library filter.")
        query = parameters.get("q", [""])[0].strip()
        if len(query) > 500 or any(ord(char) < 32 or ord(char) == 127 for char in query):
            raise AppError("Keep the library search to 500 characters without control characters.")
        try:
            page = int(parameters.get("page", ["1"])[0])
            size = int(parameters.get("page_size", ["25"])[0])
        except (ValueError, TypeError):
            raise AppError("Choose a valid library page and page size.") from None
        if page < 1 or page > 50000 or size < 1 or size > 100:
            raise AppError("Choose a positive page and a page size from 1 to 100.")
        filter_name = parameters.get("filter", ["all"])[0]
        if filter_name not in ("all", "missing", "duplicates"):
            raise AppError("Choose a valid library filter.")
        return self.library.review(self.store.rows("real"), self.store.settings()["music_root"],
                                   query=query, page=page, page_size=size, filter_name=filter_name)

    def refresh_stores(self, force=False, identity=None):
        return self.lookup.enqueue([identity] if identity is not None else None, force=force)

    def export_csv(self):
        fields = ["artists", "title_and_version", *MANUAL.keys(), "spotify_id", "spotify_url", "in_source", "first_seen", "last_seen"]
        stream = io.StringIO(newline="")
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for track in self.store.rows(self.namespace()):
            writer.writerow({key: safe_cell(track.get(key)) for key in fields})
        return ("\ufeff" + stream.getvalue()).encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    server_version = "DJList/" + VERSION

    def log_message(self, *args):
        pass  # OAuth codes and user paths must not appear in access logs.

    @property
    def app(self):
        return self.server.app

    def send_data(self, status, payload, mime="application/json; charset=utf-8", extra=None):
        if isinstance(payload, dict):
            payload = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        elif isinstance(payload, str):
            payload = payload.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("X-DJList-Version", VERSION)
        self.send_header("X-DJList-Build", self.app.build_id)
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: https://i.scdn.co https://is1-ssl.mzstatic.com https://is2-ssl.mzstatic.com https://is3-ssl.mzstatic.com https://is4-ssl.mzstatic.com https://is5-ssl.mzstatic.com; media-src https://audio-ssl.itunes.apple.com; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        for name, value in (extra or {}).items():
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)

    def verify_request(self, mutation=False):
        if self.headers.get("Host") != "127.0.0.1:%d" % self.app.port:
            raise AppError("Use the local DJList address shown when the app starts.", 403)
        origin = self.headers.get("Origin")
        if origin is not None and origin != self.app.origin:
            raise AppError("This request did not come from DJList.", 403)
        if urlsplit(self.path).path != "/callback" and self.headers.get("Sec-Fetch-Site") == "cross-site":
            raise AppError("Cross-site requests are not allowed.", 403)
        if mutation:
            supplied = self.headers.get("X-CSRF-Token", "")
            if not secrets.compare_digest(supplied.encode("utf-8"), self.app.csrf_token.encode("ascii")):
                raise AppError("Refresh the DJList page and try again.", 403)

    def body(self):
        if self.headers.get("Transfer-Encoding"):
            raise AppError("Unsupported request encoding.", 400)
        if self.headers.get_content_type() != "application/json":
            raise AppError("Send changes as JSON.", 415)
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise AppError("Invalid request length.") from None
        if length < 0 or length > MAX_BODY:
            raise AppError("This request is too large.", 413)
        try:
            value = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
        except (ValueError, UnicodeError):
            raise AppError("The request could not be read.") from None
        if not isinstance(value, dict):
            raise AppError("The request must contain an object.")
        return value

    def do_GET(self):
        self.dispatch(False)

    def do_HEAD(self):
        self.dispatch(False)

    def do_POST(self):
        self.dispatch(True)

    def do_PATCH(self):
        self.dispatch(True)

    def do_OPTIONS(self):
        self.send_data(403, {"error": "Cross-origin access is not enabled."})

    def dispatch(self, mutation):
        try:
            self.verify_request(mutation)
            path = urlsplit(self.path).path
            if not mutation:
                if path == "/api/health":
                    return self.send_data(200, self.app.health())
                if path == "/api/state":
                    return self.send_data(200, self.app.state())
                if path == "/api/store-job":
                    return self.send_data(200, self.app.lookup.status() if self.app.namespace() == "real" else StoreLookupQueue._idle())
                if path == "/api/library-review":
                    try:
                        parameters = parse_qs(urlsplit(self.path).query, keep_blank_values=True, max_num_fields=8)
                    except ValueError:
                        raise AppError("Choose valid library filters.") from None
                    return self.send_data(200, self.app.library_review(parameters))
                if path == "/api/provider-search":
                    try:
                        parameters = parse_qs(urlsplit(self.path).query, keep_blank_values=True, max_num_fields=5)
                    except ValueError:
                        raise AppError("Enter one search query.") from None
                    if set(parameters) != {"q"} or len(parameters["q"]) != 1:
                        raise AppError("Enter one search query.")
                    return self.send_data(200, provider_search(parameters["q"][0]))
                if path == "/api/export.csv":
                    return self.send_data(200, self.app.export_csv(), "text/csv; charset=utf-8", {"Content-Disposition": 'attachment; filename="DJList.csv"'})
                if path == "/callback":
                    try:
                        self.app.oauth_callback(urlsplit(self.path).query)
                        message = "Spotify connected. Return to DJList to refresh your playlist."
                    except AppError as error:
                        message = str(error)
                    document = '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>DJList — Spotify</title><body><main><h1>DJList</h1><p>%s</p><p><a href="/">Return to DJList</a></p></main></body></html>' % html.escape(message)
                    return self.send_data(200, document, "text/html; charset=utf-8")
                if path.startswith("/api/"):
                    raise AppError("This endpoint does not exist.", 404)
                payload, mime = self.app.static_asset(path)
                return self.send_data(200, payload, mime)
            data = self.body()
            if self.command == "PATCH" and path == "/api/settings":
                result = self.app.change_settings(data)
            elif self.command == "POST" and path == "/api/spotify/connect":
                result = self.app.connect_spotify()
            elif self.command == "POST" and path == "/api/sync":
                result = self.app.sync()
            elif self.command == "POST" and path == "/api/store-refresh":
                if set(data) - {"force"}:
                    raise AppError("Only the store refresh option is accepted.")
                result = self.app.refresh_stores(force=data.get("force", False))
            elif self.command == "POST" and path == "/api/store-cancel":
                if data:
                    raise AppError("Cancel does not accept additional settings.")
                result = self.app.lookup.cancel()
            elif self.command == "POST" and path == "/api/catalog-search":
                if set(data) - {"query", "reference"}:
                    raise AppError("Only a search query and reference track are accepted.")
                result = self.app.catalog.search(data.get("query"), data.get("reference"))
            elif self.command == "POST" and path == "/api/demo":
                result = self.app.demo(data.get("enabled", True))
            elif self.command == "POST" and path == "/api/scan":
                result = self.app.scan()
            elif self.command == "POST" and path == "/api/refresh-libraries":
                if set(data) - {"media", "xml"}:
                    raise AppError("Choose only which libraries to refresh.")
                result = self.app.refresh_libraries(media=data.get("media", True), xml=data.get("xml", True))
            elif self.command == "POST" and path == "/api/refresh-rekordbox-xml":
                if data:
                    raise AppError("XML refresh uses the file path saved in Settings.")
                result = self.app.refresh_libraries(media=False, xml=True)
            elif self.command == "POST" and path == "/api/import-rekordbox-xml":
                if set(data) - {"xml", "filename"}:
                    raise AppError("Choose one XML file to import.")
                result = self.app.import_xml(data.get("xml"), source_name=data.get("filename", "Uploaded XML"))
            elif re.fullmatch(r"/api/tracks/\d+/refresh-store", path) and self.command == "POST":
                if data:
                    raise AppError("This action refreshes the selected track's store result.")
                result = self.app.refresh_stores(force=True, identity=int(path.split("/")[3]))
            elif re.fullmatch(r"/api/tracks/\d+/link", path) and self.command == "POST":
                result = self.app.link(int(path.split("/")[3]), data.get("path"))
            elif re.fullmatch(r"/api/tracks/\d+", path) and self.command == "PATCH":
                result = self.app.edit_track(int(path.split("/")[3]), data)
            else:
                raise AppError("This action is not available.", 404)
            self.send_data(200, result)
        except AppError as error:
            self.send_data(error.status, {"error": str(error)})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            self.send_data(500, {"error": "DJList could not finish this action. Refresh the page and try again. Your audio files and Rekordbox database were not changed."})


class LocalServer(ThreadingHTTPServer):
    daemon_threads = True

    def server_bind(self):
        # HTTPServer's reverse DNS can stall on macOS; our host is numeric loopback.
        TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]

    def get_request(self):
        socket, address = super().get_request()
        socket.settimeout(35)
        return socket, address

    def handle_error(self, request, address):
        pass


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-dir", type=Path, default=default_data_dir(), help="DJList's own local records; separate from Rekordbox")
    parser.add_argument("--port", type=int, default=8888, help="Loopback port (default 8888); Spotify requires this exact callback port registered")
    parser.add_argument("--no-browser", action="store_true", help="Do not automatically open the browser")
    args = parser.parse_args(argv)
    if not 1024 <= args.port <= 65535:
        parser.error("Choose a port from 1024 to 65535.")
    os.umask(0o077)
    try:
        server = LocalServer(("127.0.0.1", args.port), Handler)
    except OSError:
        print(occupied_port_message(args.port, running_instance(args.port)), file=sys.stderr)
        return 1
    try:
        app = Application(args.data_dir, args.port)
    except (AppError, OSError, sqlite3.Error) as error:
        server.server_close()
        print("DJList could not load its local data or interface: %s" % error, file=sys.stderr)
        return 1
    server.app = app
    print("DJList %s — %s" % (VERSION, app.origin), flush=True)
    print("App folder: %s" % app.app_dir, flush=True)
    print("Your DJList records: %s" % app.store.directory, flush=True)
    print("Spotify sign-in lasts for this run. Audio files and Rekordbox's database are read-only to this app.", flush=True)
    if not args.no_browser:
        webbrowser.open(app.origin)
    try:
        server.serve_forever(poll_interval=.5)
    except KeyboardInterrupt:
        print("\nDJList stopped.")
    finally:
        app.close()
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
