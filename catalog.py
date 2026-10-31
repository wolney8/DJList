"""Read-only iTunes Store search for purchase candidates and promotional previews.

Only catalogue JSON is fetched here. Audio is never fetched, saved or cached.
The UI must follow Apple's promotional-content terms when presenting previews:
https://performance-partners.apple.com/search-api
"""
from collections import deque
import copy
from datetime import datetime, timezone
import difflib
import json
import math
import re
import threading
import time
import unicodedata
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from spotify_api import AppError


API_URL = "https://itunes.apple.com/search"
STOREFRONT = "GB"
CACHE_SECONDS = 300
MAX_CACHE_ENTRIES = 64
MAX_CALLS_PER_MINUTE = 16
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
NOTICE = ("iTunes Store UK candidates. Check the exact mix, duration, download format "
          "and checkout price before buying. A match is never confirmed automatically.")
PREVIEW_ATTRIBUTION = "provided courtesy of iTunes"


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


OPENER = build_opener(NoRedirect())


def checked_at():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def clean_text(value, label, maximum=500, required=False):
    if not isinstance(value, str) or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise AppError("Enter a valid " + label + ".")
    value = value.strip()
    if len(value) > maximum or (required and not value):
        raise AppError("Use 1–%d characters for %s." % (maximum, label))
    return value


def normalise(value):
    value = unicodedata.normalize("NFKD", value).casefold()
    value = "".join(c for c in value if not unicodedata.combining(c))
    return " ".join(re.findall(r"[^\W_]+", value))


def title_parts(value):
    """Keep qualifiers separate for comparison; never drop them from the result."""
    qualifiers = re.findall(r"\(([^()]*)\)|\[([^\[\]]*)\]", value)
    version = " ".join(a or b for a, b in qualifiers)
    base = re.sub(r"\([^()]*\)|\[[^\[\]]*\]", " ", value).strip()
    suffix = re.split(r"\s+[–—-]\s+", base, maxsplit=1)
    if len(suffix) == 2 and re.search(r"\b(mix|remix|dub|edit|version|remaster|live|acoustic|instrumental|radio|extended|slowed|sped|vip|rework)\b", suffix[1], re.I):
        base, version = suffix[0], (version + " " + suffix[1]).strip()
    return " ".join(base.split()), " ".join(version.split())


def validate_reference(reference):
    if reference is None:
        return None
    if not isinstance(reference, dict) or set(reference) - {"artists", "title", "duration_ms", "isrc"}:
        raise AppError("The reference track could not be read.")
    result = {key: clean_text(reference.get(key, ""), "reference " + key,
                              maximum=1000 if key != "isrc" else 32)
              for key in ("artists", "title", "isrc")}
    duration = reference.get("duration_ms")
    if duration is not None and (isinstance(duration, bool) or not isinstance(duration, (int, float))
                                 or not math.isfinite(duration) or not 0 <= duration <= 36_000_000):
        raise AppError("The reference duration could not be read.")
    result["duration_ms"] = int(duration) if duration else None
    return result


def query_variants(query, reference):
    if not reference or not reference["artists"] or not reference["title"]:
        return [query]
    expected = reference["artists"] + " " + reference["title"]
    if normalise(query) != normalise(expected):
        return [query]  # Honour a search the user has edited.
    primary = reference["artists"].split(";", 1)[0].strip()
    base, _ = title_parts(reference["title"])
    variants = [primary + " " + base, primary + " " + reference["title"]]
    return list(dict.fromkeys(value.strip()[:500] for value in variants if value.strip()))


def safe_store_url(value):
    if not isinstance(value, str) or len(value) > 4096:
        return ""
    try:
        parsed = urlsplit(value)
        allowed = parsed.hostname in {"music.apple.com", "itunes.apple.com"}
        if parsed.scheme == "https" and allowed and not parsed.username and not parsed.password and parsed.port in (None, 443):
            return value
    except ValueError:
        pass
    return ""


def safe_preview_url(value):
    if not isinstance(value, str) or len(value) > 4096:
        return ""
    try:
        parsed = urlsplit(value)
        if (parsed.scheme == "https" and parsed.hostname == "audio-ssl.itunes.apple.com"
                and parsed.port in (None, 443) and not parsed.username and not parsed.password
                and parsed.path.startswith("/itunes-assets/AudioPreview")):
            return value
    except ValueError:
        pass
    return ""


def safe_artwork_url(value):
    if not isinstance(value, str) or len(value) > 4096 or any(ord(c) < 32 for c in value):
        return ""
    try:
        parsed = urlsplit(value)
        if (parsed.scheme == "https" and parsed.hostname in
                {"is%d-ssl.mzstatic.com" % number for number in range(1, 6)}
                and parsed.port in (None, 443) and not parsed.username and not parsed.password):
            return value
    except ValueError:
        pass
    return ""


def price_for(item):
    amount, currency = item.get("trackPrice"), item.get("currency")
    if (isinstance(amount, bool) or not isinstance(amount, (int, float))
            or not math.isfinite(amount) or amount < 0 or amount > 10000
            or not isinstance(currency, str) or not re.fullmatch(r"[A-Z]{3}", currency)):
        return None
    amount = round(float(amount), 2)
    display = ("£%.2f" % amount) if currency == "GBP" else ("%s %.2f" % (currency, amount))
    return {"amount": amount, "currency": currency, "display": display}


def make_offer(item, timestamp):
    if not isinstance(item, dict) or item.get("kind") != "song":
        return None
    identity = item.get("trackId")
    url = safe_store_url(item.get("trackViewUrl"))
    if isinstance(identity, bool) or not isinstance(identity, int) or identity <= 0 or not url:
        return None
    title, artist = item.get("trackName"), item.get("artistName")
    if not isinstance(title, str) or not title or not isinstance(artist, str) or not artist:
        return None
    duration = item.get("trackTimeMillis")
    if isinstance(duration, bool) or not isinstance(duration, int) or not 0 < duration <= 36_000_000:
        duration = None
    explicit = item.get("trackExplicitness")
    explicit = {"explicit": True, "notExplicit": False, "cleaned": False}.get(explicit) if isinstance(explicit, str) else None
    return {"id": "itunes:%d" % identity, "provider": "itunes", "provider_name": "iTunes Store",
            "title": title[:1000], "artist": artist[:1000],
            "album": str(item.get("collectionName") or "")[:1000],
            "duration_ms": duration, "genre": str(item.get("primaryGenreName") or "")[:200],
            "artwork_url": safe_artwork_url(item.get("artworkUrl100")) or safe_artwork_url(item.get("artworkUrl60")),
            "release_date": str(item.get("releaseDate") or "")[:40],
            "explicit": explicit,
            "bpm": None, "musical_key": "",  # Not supplied by this catalogue API.
            "price": price_for(item), "purchase_url": url,
            "preview_url": safe_preview_url(item.get("previewUrl")),
            "preview_attribution": PREVIEW_ATTRIBUTION, "checked_at": timestamp}


def compare_offer(offer, reference):
    if not reference or not reference["title"]:
        return {"status": "uncompared", "score": 0, "reasons": ["Search result; no reference track selected."]}
    title, artist = normalise(offer["title"]), normalise(offer["artist"])
    expected_title = normalise(reference["title"])
    primary = normalise(reference["artists"].split(";", 1)[0])
    base, version = title_parts(offer["title"])
    expected_base, expected_version = title_parts(reference["title"])
    base_equal = normalise(base) == normalise(expected_base)
    version_equal = normalise(version) == normalise(expected_version)
    # A substring can identify a tribute/cover act rather than the requested
    # performer. Ambiguous combined credits remain a candidate for inspection.
    artist_equal = bool(primary and primary == artist)
    title_equal = title == expected_title
    similarity = difflib.SequenceMatcher(None, normalise(expected_base), normalise(base)).ratio()
    score = round(similarity * 45) + (25 if artist_equal else 0) + (20 if version_equal else 0)
    reasons = []
    if title_equal:
        reasons.append("Title and version wording match.")
    elif base_equal and not version_equal:
        reasons.append("Song title matches, but this appears to be a different mix/version.")
    else:
        reasons.append("Compare the full song title and mix/version.")
    reasons.append("Primary artist matches." if artist_equal else "Artist credit needs checking.")
    duration_difference = None
    if reference["duration_ms"] and offer["duration_ms"]:
        duration_difference = abs(reference["duration_ms"] - offer["duration_ms"])
        if duration_difference <= 2000:
            score += 10
            reasons.append("Duration is within 2 seconds.")
        else:
            score -= 10 if duration_difference > 8000 else 3
            reasons.append("Duration differs by about %d seconds." % round(duration_difference / 1000))
    else:
        reasons.append("Reference duration is unavailable or unverified.")
    if base_equal and not version_equal:
        status, score = "different_version", min(score, 59)
    elif (title_equal or (base_equal and version_equal)) and artist_equal:
        status = "likely_match" if duration_difference is None or duration_difference <= 2000 else "check_duration"
    elif not artist_equal:
        status = "check_artist"
    else:
        status = "possible_match"
    reasons.append("Listen and confirm before saving this purchase link.")
    return {"status": status, "score": max(0, min(100, score)), "reasons": reasons}


def fetch_catalogue(query):
    params = {"term": query, "country": STOREFRONT, "media": "music", "entity": "song", "limit": 40}
    request = Request(API_URL + "?" + urlencode(params),
                      headers={"Accept": "application/json", "User-Agent": "DJList-local/0.5"})
    with OPENER.open(request, timeout=8) as response:
        body = response.read(MAX_RESPONSE_BYTES + 1)
    if len(body) > MAX_RESPONSE_BYTES:
        raise ValueError("Catalogue response too large")
    payload = json.loads(body)
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        raise ValueError("Unexpected catalogue response")
    return payload["results"][:40]


class CatalogClient:
    def __init__(self, fetcher=None, clock=None):
        self.fetcher = fetcher or fetch_catalogue
        self.clock = clock or time.monotonic
        self.cache = {}
        self.calls = deque()
        self.lock = threading.Lock()
        self.cooldown_until = 0

    def _lookup(self, query):
        current = self.clock()
        cached = self.cache.get(query)
        if cached and current - cached["time"] < CACHE_SECONDS:
            return copy.deepcopy(cached["offers"]), cached["checked_at"], True
        while self.calls and current - self.calls[0] >= 60:
            self.calls.popleft()
        if current < self.cooldown_until or len(self.calls) >= MAX_CALLS_PER_MINUTE:
            raise AppError("The catalogue request limit has been reached. Try again in a minute.", 429)
        self.calls.append(current)
        results = self.fetcher(query)
        timestamp = checked_at()
        offers = [offer for item in results if (offer := make_offer(item, timestamp)) is not None]
        if len(self.cache) >= MAX_CACHE_ENTRIES:
            self.cache.pop(min(self.cache, key=lambda key: self.cache[key]["time"]))
        self.cache[query] = {"time": current, "offers": copy.deepcopy(offers), "checked_at": timestamp}
        return offers, timestamp, False

    def search(self, query, reference=None, force=False):
        query = clean_text(query, "search", required=True)
        reference = validate_reference(reference)
        if not isinstance(force, bool):
            raise AppError("Choose whether to refresh this catalogue search.")
        response = {"provider": "itunes", "storefront": STOREFRONT, "query": query,
                    "checked_at": None, "cached": False, "queries": [], "offers": [],
                    "notice": NOTICE, "errors": []}
        if not self.lock.acquire(blocking=False):
            response["errors"].append({"provider": "itunes", "code": "busy", "message": "Another catalogue search is running. Try again shortly."})
            return response
        try:
            candidates, cache_flags = {}, []
            variants = query_variants(query, reference)[:2]
            if force:
                for variant in variants:
                    self.cache.pop(variant, None)
            for variant in variants:
                response["queries"].append(variant)
                try:
                    offers, timestamp, cached = self._lookup(variant)
                    cache_flags.append(cached)
                    if response["checked_at"] is None or timestamp < response["checked_at"]:
                        response["checked_at"] = timestamp
                    for offer in offers:
                        offer["match"] = compare_offer(offer, reference)
                        candidates[offer["id"]] = offer
                    if offers and (not reference or any(o["match"]["status"] == "likely_match" for o in offers)):
                        break
                except HTTPError as error:
                    if error.code == 429:
                        self.cooldown_until = self.clock() + 60
                    response["errors"].append({"provider": "itunes", "code": "rate_limited" if error.code == 429 else "unavailable",
                                               "message": "iTunes search is temporarily unavailable. Try again later or use a shop search below."})
                    break
                except AppError as error:
                    response["errors"].append({"provider": "itunes", "code": "rate_limited", "message": str(error)})
                    break
                except (URLError, TimeoutError, OSError, ValueError, TypeError):
                    response["errors"].append({"provider": "itunes", "code": "unavailable",
                                               "message": "iTunes search could not be reached. Use a shop search below or try again."})
                    break
            response["cached"] = bool(cache_flags) and all(cache_flags)
            response["offers"] = sorted(candidates.values(), key=lambda offer: offer["match"]["score"], reverse=True)[:12]
            return response
        finally:
            self.lock.release()
