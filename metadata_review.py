"""Explain and review metadata cleanup without rewriting audio or Spotify data.

Proposals are deliberately conservative text parsing. Confirmed values live in
DJList's own database and are bound to the exact file fingerprint. They are not
verified artist credits, and refreshing tags never makes a proposal automatic.
"""
from datetime import datetime, timedelta, timezone
import json
import re
import secrets
import threading
import time

from audio_metadata import file_fingerprint
from spotify_api import AppError

PLAN_SECONDS = 600
BATCH_LIMIT = 100
FIELDS = ("artist", "title", "version_notes", "featured_artists")


def _text(value):
    return " ".join(str(value or "").split())[:1000]


def fields(entry):
    return {key: _text(entry.get(key, "")) for key in FIELDS}


def suggest_metadata(entry):
    if entry.get("metadata_override"):
        return None
    before = fields(entry)
    original = entry.get("original_metadata") or before
    artist, title = _text(original.get("artist")), _text(original.get("title"))
    if not title:
        return None
    after = {**before, "artist": artist, "title": title}
    reasons = []
    # A spaced separator is intentional. AC-DC, Jay-Z and numeric titles are
    # not split on a bare hyphen, and track numbers are not erased here.
    parts = re.split(r"\s+[—–-]\s+", title, maxsplit=1)
    if len(parts) == 2 and all(part.strip() for part in parts):
        candidate, remainder = parts[0].strip(), parts[1].strip()
        if not artist:
            after.update(artist=candidate, title=remainder)
            reasons.append("The artist field is empty and the title starts with an artist/title separator. Check the proposed artist credit.")
        elif re.sub(r"\W+", "", candidate).casefold() == re.sub(r"\W+", "", artist).casefold():
            after["title"] = remainder
            reasons.append("The title repeats the existing artist before a spaced separator.")
    title = after["title"]
    featured = re.search(r"\s*[\[(]\s*(?:feat\.?|ft\.?|featuring)\s+([^\]\)]+)[\])]", title, re.I)
    if featured is None:
        featured = re.search(r"\s+(?:feat\.|ft\.|featuring)\s+([^\[\]()]+)$", title, re.I)
    if featured and not before["featured_artists"]:
        after["featured_artists"] = _text(featured[1])
        after["title"] = _text(title[:featured.start()] + " " + title[featured.end():])
        reasons.append("An explicit featuring credit was separated from the title; verify the spelling and artist roles.")
    versions = [a or b for a, b in re.findall(r"\(([^()]*)\)|\[([^\[\]]*)\]", after["title"])
                if re.search(r"\b(mix|remix|edit|dub|version|vip|rework|remaster|instrumental|extended|radio|live)\b", a or b, re.I)]
    if versions and reasons and not before["version_notes"]:
        after["version_notes"] = "; ".join(versions)
        reasons.append("Mix/version wording is retained in the title and also recorded separately.")
    if not reasons or not after["title"] or after == before:
        return None
    return {"before": before, "after": after, "confidence": "review",
            "reasons": reasons, "message": "Text-based suggestion. Confirm the recording and artist credits; audio tags stay unchanged."}


def _configuration(settings):
    return tuple(settings.get(key, "") for key in ("mode", "music_root", "download_root"))


def _source(db, path):
    result = {}
    for table in ("library_entries", "media_metadata", "files", "library_metadata_overrides"):
        row = db.execute("SELECT * FROM " + table + " WHERE path=?", (path,)).fetchone()
        result[table] = dict(row) if row else None
    return result


class MetadataReview:
    def __init__(self, store, library, clock=time.monotonic):
        self.store, self.library, self.clock = store, library, clock
        self.lock, self.plan = threading.RLock(), None
        with store.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS library_metadata_overrides (
                path TEXT PRIMARY KEY, metadata TEXT NOT NULL, fingerprint TEXT NOT NULL, created_at TEXT NOT NULL)""")

    def clear(self):
        with self.lock:
            self.plan = None

    def _preview(self, entry_ids, reset=False):
        if (not isinstance(entry_ids, list) or not 1 <= len(entry_ids) <= BATCH_LIMIT
                or any(not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{24}", value) for value in entry_ids)
                or len(set(entry_ids)) != len(entry_ids)):
            raise AppError("Choose between 1 and 100 distinct library entries.")
        settings = self.store.settings()
        if settings.get("mode") != "real":
            raise AppError("Leave the demo before reviewing local metadata.", 409)
        self.library.invalidate()
        entries = self.library._entries(settings["music_root"], limit=50001)
        selected = {row["id"]: row for row in entries if row["id"] in entry_ids}
        if len(selected) != len(entry_ids):
            raise AppError("An entry is no longer in the indexed library. Refresh and review again.", 409)
        items, evidence = [], {}
        with self.store.connect() as db:
            for identity in entry_ids:
                entry = selected[identity]
                fingerprint = file_fingerprint(entry["path"], settings["music_root"])
                if fingerprint is None or ".djlist-removed" in entry["path"].split("/"):
                    raise AppError("Metadata review requires available files inside the selected music folder.", 409)
                if reset:
                    if not entry.get("metadata_override") and not entry.get("metadata_override_stale"):
                        continue
                    proposal = {"before": fields(entry), "after": fields(entry.get("original_metadata", entry)),
                                "confidence": "review", "reasons": ["Remove the DJList-only correction and show source metadata again."]}
                else:
                    proposal = suggest_metadata(entry)
                    if proposal is None:
                        continue
                items.append({"entry_id": identity, "path": entry["path"], **proposal, "can_apply": True})
                evidence[identity] = {"path": entry["path"], "fingerprint": fingerprint, "source": _source(db, entry["path"]),
                                      "after": proposal["after"]}
        if _configuration(self.store.settings()) != _configuration(settings):
            raise AppError("Folder settings changed. Review metadata again.", 409)
        token = secrets.token_urlsafe(32)
        self.plan = {"token": token, "deadline": self.clock() + PLAN_SECONDS,
                     "configuration": _configuration(settings), "root": settings["music_root"],
                     "entries": evidence, "reset": reset}
        return {"token": token, "expires_at": (datetime.now(timezone.utc) + timedelta(seconds=PLAN_SECONDS)).isoformat(),
                "items": items, "summary": {"selected": len(entry_ids), "proposed": len(items)},
                "message": "Review each correction. Changes stay in DJList; audio tags and Rekordbox are unchanged."}

    def preview(self, entry_ids):
        with self.lock:
            return self._preview(entry_ids)

    def reset_preview(self, entry_ids):
        with self.lock:
            return self._preview(entry_ids, reset=True)

    def _apply(self, token, selected_ids, reset=False):
        if (not isinstance(token, str) or not isinstance(selected_ids, list) or not selected_ids
                or len(selected_ids) > BATCH_LIMIT or any(not isinstance(value, str) for value in selected_ids)
                or len(set(selected_ids)) != len(selected_ids)):
            raise AppError("Select the reviewed metadata changes to apply.")
        plan = self.plan
        self.plan = None
        if (not plan or not secrets.compare_digest(token, plan["token"]) or plan["deadline"] < self.clock()
                or plan["reset"] != reset or any(value not in plan["entries"] for value in selected_ids)):
            raise AppError("This metadata review expired or changed. Make a fresh review.", 409)
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            settings = {row["key"]: json.loads(row["value"]) for row in db.execute("SELECT key,value FROM settings")}
            if _configuration(settings) != plan["configuration"] or settings.get("mode") != "real":
                raise AppError("Folder settings changed. Make a fresh metadata review.", 409)
            for identity in selected_ids:
                evidence = plan["entries"][identity]
                if (_source(db, evidence["path"]) != evidence["source"]
                        or file_fingerprint(evidence["path"], plan["root"]) != evidence["fingerprint"]):
                    raise AppError("A file or its metadata changed. No corrections were applied; review again.", 409)
            for identity in selected_ids:
                evidence = plan["entries"][identity]
                if reset:
                    db.execute("DELETE FROM library_metadata_overrides WHERE path=?", (evidence["path"],))
                else:
                    db.execute("INSERT OR REPLACE INTO library_metadata_overrides VALUES (?,?,?,?)",
                               (evidence["path"], json.dumps(evidence["after"]), json.dumps(evidence["fingerprint"]),
                                datetime.now(timezone.utc).isoformat(timespec="seconds")))
            for identity in selected_ids:
                evidence = plan["entries"][identity]
                if file_fingerprint(evidence["path"], plan["root"]) != evidence["fingerprint"]:
                    raise AppError("A file changed before metadata could be saved. No corrections were applied.", 409)
        self.library.invalidate()
        return {"updated": selected_ids, "count": len(selected_ids),
                "message": "Source metadata restored in DJList." if reset else "Metadata corrections saved in DJList. Audio tags and Rekordbox are unchanged."}

    def apply(self, token, selected_ids):
        with self.lock:
            return self._apply(token, selected_ids)

    def reset(self, token, selected_ids):
        with self.lock:
            return self._apply(token, selected_ids, reset=True)
