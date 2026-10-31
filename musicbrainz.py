"""Read-only discovery of direct shop links from MusicBrainz recording metadata.

This is community link evidence, not a live shop catalogue. No shop pages,
artwork or audio are fetched. Only core CC0 data is used; tags are excluded.
https://musicbrainz.org/doc/MusicBrainz_API
https://musicbrainz.org/doc/MusicBrainz_API/Rate_Limiting
"""
from collections import OrderedDict
import copy
import hashlib
import json
import math
import re
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from catalog import checked_at, compare_offer, normalise, title_parts, validate_reference
from spotify_api import AppError


API_ROOT = "https://musicbrainz.org/ws/2/isrc/"
USER_AGENT = "DJList/0.5.0 (+https://github.com/wolney8/DJList)"
SUPPORTED_PROVIDERS = ("Bandcamp", "Beatport")
NOTICE = ("Bandcamp and Beatport track links listed by MusicBrainz contributors. "
          "Shop availability, price, BPM and key have not been checked. Confirm the exact mix before buying.")
PURCHASE_RELATION = "92777657-504c-4acb-bd33-51a201bd57e1"
CACHE_SECONDS = 7 * 24 * 60 * 60
MAX_CACHE_ENTRIES = 64
MIN_REQUEST_INTERVAL = 1.05
MAX_RESPONSE_BYTES = 1024 * 1024
MAX_RECORDINGS = 20
MAX_LINKS = 40
REQUEST_TIMEOUT = 10
ISRC_PATTERN = re.compile(r"[A-Z]{2}[A-Z0-9]{3}[0-9]{7}\Z")
MBID_PATTERN = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z")


class MusicBrainzError(Exception):
    def __init__(self, code, message, retry_after=0):
        super().__init__(message)
        self.code = code
        self.retry_after = retry_after


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


OPENER = build_opener(NoRedirect())


def normalise_isrc(value):
    if value is None or value == "":
        return ""
    if not isinstance(value, str):
        return None
    value = value.strip().upper()
    return value if ISRC_PATTERN.fullmatch(value) else None


def _text(value, maximum=1000):
    if not isinstance(value, str) or len(value) > maximum or any(ord(c) < 32 or ord(c) == 127 for c in value):
        return ""
    return value.strip()


def shop_link(value):
    """Allow only recognised, direct track pages, with no affiliate parameters."""
    if not isinstance(value, str) or len(value) > 4096 or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value):
        return None
    try:
        parsed = urlsplit(value)
        if (parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in (None, 443)
                or parsed.query or parsed.fragment):
            return None
        host = parsed.hostname or ""
        if (re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.bandcamp\.com", host)
                and re.fullmatch(r"/track/[a-z0-9]+(?:-[a-z0-9]+)*/?", parsed.path)):
            return {"provider": "bandcamp", "provider_name": "Bandcamp", "purchase_url": value}
        if host in {"beatport.com", "www.beatport.com"} and re.fullmatch(r"/track/[a-z0-9]+(?:-[a-z0-9]+)*/[1-9][0-9]*/?", parsed.path):
            return {"provider": "beatport", "provider_name": "Beatport", "purchase_url": value}
    except ValueError:
        pass
    return None


def _retry_after(headers):
    try:
        seconds = int(headers.get("Retry-After", "60"))
    except (TypeError, ValueError, AttributeError):
        seconds = 60
    return min(3600, max(60, seconds))


def fetch_recordings(isrc):
    """Fetch one fixed MusicBrainz metadata endpoint; never follow redirects."""
    if not isrc or normalise_isrc(isrc) != isrc:
        raise MusicBrainzError("invalid_isrc", "This track does not have a usable ISRC recording code.")
    query = urlencode({"inc": "artist-credits+url-rels", "fmt": "json"})
    request = Request(API_ROOT + isrc + "?" + query,
                      headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with OPENER.open(request, timeout=REQUEST_TIMEOUT) as response:
            content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            if content_type != "application/json" and not content_type.endswith("+json"):
                raise MusicBrainzError("invalid_response", "MusicBrainz did not return readable metadata. Try again later.")
            try:
                length = int(response.headers.get("Content-Length", "0"))
            except (TypeError, ValueError):
                length = 0
            if length > MAX_RESPONSE_BYTES:
                raise MusicBrainzError("response_too_large", "MusicBrainz returned more metadata than this lookup can safely read.")
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise MusicBrainzError("response_too_large", "MusicBrainz returned more metadata than this lookup can safely read.")
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeError, RecursionError):
                raise MusicBrainzError("invalid_response", "MusicBrainz did not return readable metadata. Try again later.") from None
    except HTTPError as error:
        if error.code == 404:
            return {"isrc": isrc, "recordings": []}
        if error.code in (429, 503):
            raise MusicBrainzError("rate_limited", "MusicBrainz is busy. Wait before trying another manual refresh.", _retry_after(error.headers)) from None
        raise MusicBrainzError("unavailable", "MusicBrainz could not supply this lookup. Try again later.") from None
    except (URLError, TimeoutError, OSError):
        raise MusicBrainzError("unavailable", "MusicBrainz could not be reached. Try again later.") from None
    if (not isinstance(payload, dict) or normalise_isrc(payload.get("isrc")) != isrc
            or not isinstance(payload.get("recordings"), list)):
        raise MusicBrainzError("invalid_response", "MusicBrainz returned unexpected recording metadata. Try again later.")
    return payload


def _result(status, isrc, message, **values):
    return {"provider": "musicbrainz", "status": status, "isrc": isrc or "",
            "checked_at": None, "cached": False, "message": message, "notice": NOTICE,
            "supported_providers": list(SUPPORTED_PROVIDERS), "links": [], "recordings_checked": 0,
            "incomplete": False,
            **values}


def _recording(item):
    if not isinstance(item, dict):
        return None
    identity, title = _text(item.get("id"), 36), _text(item.get("title"))
    credits = item.get("artist-credit")
    if not MBID_PATTERN.fullmatch(identity) or not title or not isinstance(credits, list) or not credits or len(credits) > 20:
        return None
    names = []
    for credit in credits:
        if not isinstance(credit, dict):
            return None
        artist = credit.get("artist")
        name = _text(credit.get("name")) or _text(artist.get("name") if isinstance(artist, dict) else None)
        if not name:
            return None
        names.append(name)
    disambiguation = item.get("disambiguation")
    if disambiguation is None:
        disambiguation = ""
    if not isinstance(disambiguation, str) or (disambiguation and not _text(disambiguation)):
        return None
    duration = item.get("length")
    if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not 0 < duration <= 36_000_000:
        duration = None
    relations = item.get("relations", [])
    if not isinstance(relations, list):
        raise MusicBrainzError("invalid_response", "MusicBrainz returned unexpected recording relationships. Try again later.")
    return {"id": identity, "title": title, "artist": "; ".join(names), "primary_artist": names[0],
            "duration_ms": int(duration) if duration is not None else None,
            "disambiguation": disambiguation.strip(), "relations": relations}


def make_result(payload, reference, timestamp):
    """Compare recording identity before exposing community-supplied shop links."""
    isrc = reference["isrc"]
    if (not isinstance(payload, dict) or normalise_isrc(payload.get("isrc")) != isrc
            or not isinstance(payload.get("recordings"), list)):
        raise MusicBrainzError("invalid_response", "MusicBrainz returned unexpected recording metadata. Try again later.")
    raw = payload["recordings"]
    truncated = len(raw) > MAX_RECORDINGS
    incomplete = truncated
    skipped_recordings = skipped_relations = 0
    records = {}
    for item in raw[:MAX_RECORDINGS]:
        record = _recording(item)
        if record is None or record["id"] in records:
            incomplete = True
            skipped_recordings += 1
            continue
        records[record["id"]] = record
        if len(record["relations"]) > 200:
            truncated = incomplete = True
    expected_base = normalise(title_parts(reference["title"])[0])
    primary_artist = normalise(reference["artists"].split(";", 1)[0])
    links, seen_urls = [], set()
    ambiguous = len(records) > 1 or truncated
    for record in records.values():
        if (normalise(record["primary_artist"]) != primary_artist
                or normalise(title_parts(record["title"])[0]) != expected_base):
            continue
        match = compare_offer({"title": record["title"], "artist": record["primary_artist"], "duration_ms": record["duration_ms"]}, reference)
        match["reasons"].insert(0, "MusicBrainz lists this recording under the same ISRC; the audio has not been checked.")
        if record["disambiguation"]:
            match["reasons"].append("MusicBrainz recording note: " + record["disambiguation"] + ". Confirm this version.")
            if match["status"] == "likely_match":
                match["status"], match["score"] = "possible_match", min(match["score"], 79)
        if not reference["duration_ms"] or not record["duration_ms"]:
            if match["status"] == "likely_match":
                match["status"], match["score"] = "possible_match", min(match["score"], 79)
        if ambiguous:
            match["reasons"].append("This ISRC has multiple MusicBrainz recordings; check which version the link represents.")
            if match["status"] == "likely_match":
                match["status"], match["score"] = "possible_match", min(match["score"], 79)
        for relation in record["relations"][:200]:
            if (not isinstance(relation, dict)
                    or not isinstance(relation.get("type-id"), str) or not relation["type-id"]
                    or not isinstance(relation.get("target-type"), str) or not relation["target-type"]
                    or relation.get("direction") not in ("forward", "backward")):
                incomplete = True
                skipped_relations += 1
                continue
            if (relation.get("type-id") != PURCHASE_RELATION
                    or relation.get("target-type") != "url" or relation.get("direction") != "forward"
                    or relation.get("ended") not in (None, False) or relation.get("end")):
                continue
            target = relation.get("url")
            if not isinstance(target, dict) or not isinstance(target.get("resource"), str):
                incomplete = True
                skipped_relations += 1
                continue
            shop = shop_link(target["resource"])
            if not shop or shop["purchase_url"] in seen_urls:
                continue
            seen_urls.add(shop["purchase_url"])
            identity = hashlib.sha256((record["id"] + "\0" + shop["purchase_url"]).encode("utf-8")).hexdigest()[:24]
            links.append({"id": "musicbrainz:" + identity, **shop,
                          "title": record["title"], "artist": record["artist"], "duration_ms": record["duration_ms"],
                          "disambiguation": record["disambiguation"],
                          "recording_id": record["id"], "source_url": "https://musicbrainz.org/recording/" + record["id"],
                          "checked_at": timestamp, "match": copy.deepcopy(match)})
            if len(links) >= MAX_LINKS:
                truncated = incomplete = True
                break
        if len(links) >= MAX_LINKS:
            break
    if incomplete:
        for link in links:
            link["match"]["reasons"].append("Some MusicBrainz metadata was skipped or limited; this evidence is incomplete.")
            if link["match"]["status"] == "likely_match":
                link["match"]["status"], link["match"]["score"] = "possible_match", min(link["match"]["score"], 79)
    links.sort(key=lambda link: (-link["match"]["score"], link["provider_name"], link["purchase_url"]))
    if not links and incomplete:
        status = "unavailable"
        message = "No shop links could be shown from this incomplete MusicBrainz response. Try a manual refresh later."
    elif not links:
        status = "not_found"
        message = "No matching direct Bandcamp or Beatport purchase links are listed in MusicBrainz for this recording."
    elif ambiguous or incomplete or any(link["match"]["status"] != "likely_match" for link in links):
        status = "review"
        message = "Possible shop links found. Review the recording, mix and duration before using them."
    else:
        status = "found"
        message = "Direct shop link suggestions found. Check the recording and current offer on the shop page."
    if truncated:
        message += " The metadata result was limited; this is not an exhaustive search."
    elif incomplete and links:
        message += " Some metadata could not be used; this evidence is incomplete."
    return _result(status, isrc, message, checked_at=timestamp, links=links,
                   recordings_checked=len(records), truncated=truncated, incomplete=incomplete,
                   skipped_recordings=skipped_recordings, skipped_relations=skipped_relations)


class MusicBrainzClient:
    """A bounded manual lookup client. Do not put it on a daily metadata poll."""
    def __init__(self, fetcher=None, clock=None, sleeper=None):
        self.fetcher = fetcher or fetch_recordings
        self.clock = clock or time.monotonic
        self.sleeper = sleeper or time.sleep
        self._lock = threading.RLock()
        self._cache = OrderedDict()
        self._last_started = None
        self._cooldown_until = 0.0

    def lookup(self, reference, force=False):
        if not isinstance(force, bool):
            raise AppError("Use a valid refresh option.")
        if not isinstance(reference, dict):
            raise AppError("The reference track could not be read.")
        reference = dict(reference)
        raw_isrc = reference.get("isrc")
        if raw_isrc is None:
            reference["isrc"] = ""
        reference = validate_reference(reference)
        isrc = normalise_isrc(raw_isrc)
        if isrc == "":
            return _result("missing_isrc", "", "Spotify has not supplied an ISRC recording code for this track, so this lookup cannot run.")
        if isrc is None:
            return _result("invalid_isrc", "", "This track's ISRC recording code is not valid for a MusicBrainz lookup.")
        reference["isrc"] = isrc
        if not reference["artists"] or not reference["title"]:
            return _result("not_found", isrc, "Artist and full title are needed to check a MusicBrainz recording match.")
        key = (isrc, reference["artists"], reference["title"], reference["duration_ms"])
        with self._lock:
            now = self.clock()
            previous = self._cache.get(key)
            if previous and not force and previous[0] > now:
                self._cache.move_to_end(key)
                return dict(copy.deepcopy(previous[1]), cached=True)
            if self._cooldown_until > now:
                return _result("unavailable", isrc, "MusicBrainz is busy. Wait before trying another manual refresh.",
                               error_code="rate_limited", retry_after=math.ceil(self._cooldown_until - now))
            if self._last_started is not None:
                delay = MIN_REQUEST_INTERVAL - (now - self._last_started)
                if delay > 0:
                    self.sleeper(delay)
            self._last_started = self.clock()
            try:
                result = make_result(self.fetcher(isrc), reference, checked_at())
            except MusicBrainzError as error:
                if error.retry_after:
                    self._cooldown_until = self.clock() + error.retry_after
                return _result("unavailable", isrc, str(error), error_code=error.code,
                               **({"retry_after": error.retry_after} if error.retry_after else {}))
            except (AppError, URLError, TimeoutError, OSError, ValueError, UnicodeError):
                return _result("unavailable", isrc, "MusicBrainz could not supply readable metadata. Try again later.", error_code="unavailable")
            if not result["incomplete"]:
                self._cache[key] = (self.clock() + CACHE_SECONDS, copy.deepcopy(result))
                self._cache.move_to_end(key)
                while len(self._cache) > MAX_CACHE_ENTRIES:
                    self._cache.popitem(last=False)
            return result
