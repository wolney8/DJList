"""Small, read-only Spotify client. Tokens are held in memory by DJList."""
import base64
import hashlib
import json
import re
import secrets
import time
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class AppError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


OPENER = build_opener(NoRedirect())
API = "https://api.spotify.com/v1/"
TOKEN_URL = "https://accounts.spotify.com/api/token"
SCOPES = "playlist-read-private user-library-read"


def spotify_artwork(album):
    """Return a provider image URL only; never fetch or cache image bytes."""
    images = album.get("images") if isinstance(album, dict) else None
    if not isinstance(images, list):
        return ""
    candidates = []
    for image in images[:20]:
        if not isinstance(image, dict):
            continue
        value = image.get("url")
        if not isinstance(value, str) or len(value) > 4096 or any(ord(c) < 32 for c in value):
            continue
        try:
            parsed = urlsplit(value)
            if (parsed.scheme != "https" or parsed.hostname != "i.scdn.co"
                    or parsed.port not in (None, 443) or parsed.username or parsed.password
                    or not parsed.path.startswith("/image/")):
                continue
        except ValueError:
            continue
        width = image.get("width")
        width = width if isinstance(width, int) and not isinstance(width, bool) and width > 0 else 0
        candidates.append((width, value))
    if not candidates:
        return ""
    suitable = sorted((item for item in candidates if item[0] >= 128), key=lambda item: item[0])
    return suitable[0][1] if suitable else max(candidates, key=lambda item: item[0])[1]


def request_json(url, token=None, form=None):
    headers = {"Accept": "application/json", "User-Agent": "DJList-local/0.3"}
    if token:
        headers["Authorization"] = "Bearer " + token
    body = urlencode(form).encode("ascii") if form is not None else None
    if body is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    try:
        with OPENER.open(Request(url, data=body, headers=headers), timeout=30) as response:
            result = json.load(response)
        if not isinstance(result, dict):
            raise AppError("Spotify returned an unexpected response. Please try again.", 502)
        return result
    except HTTPError:
        raise
    except (URLError, TimeoutError, ValueError, OSError):
        raise AppError("Spotify could not be reached. Check your internet connection and retry.", 502) from None


def parse_source(value):
    value = value.strip()
    if value.lower() in ("liked", "liked songs", "spotify:collection:tracks"):
        return "liked", "liked"
    if value.startswith("spotify:playlist:"):
        identity = value[len("spotify:playlist:"):]
    elif value.startswith("https://"):
        parsed = urlsplit(value)
        match = re.fullmatch(r"/(?:intl-[A-Za-z-]+/)?playlist/([A-Za-z0-9]{22})/?", parsed.path)
        if parsed.netloc != "open.spotify.com" or not match:
            raise AppError("Paste the full Spotify playlist link, or enter 'liked' for Liked Songs.")
        identity = match.group(1)
    else:
        identity = value
    if not re.fullmatch(r"[A-Za-z0-9]{22}", identity):
        raise AppError("Paste a Spotify playlist link, or enter 'liked' for Liked Songs.")
    return identity, "https://open.spotify.com/playlist/" + identity


def begin_authorization(client_id, redirect_uri):
    if not re.fullmatch(r"[A-Fa-f0-9]{32}", client_id):
        raise AppError("Enter the Client ID shown in your Spotify app settings. The client secret is not needed.")
    verifier, state = secrets.token_urlsafe(64), secrets.token_urlsafe(32)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).decode("ascii").rstrip("=")
    pending = {"state": state, "verifier": verifier, "client_id": client_id,
               "redirect_uri": redirect_uri, "expires": time.monotonic() + 600}
    url = "https://accounts.spotify.com/authorize?" + urlencode({
        "client_id": client_id, "response_type": "code", "redirect_uri": redirect_uri,
        "scope": SCOPES, "state": state, "code_challenge_method": "S256", "code_challenge": challenge,
    })
    return pending, url


def callback_code(query, pending):
    values = parse_qs(query)
    states = values.get("state", [])
    if (not pending or time.monotonic() > pending["expires"] or len(states) != 1
            or not secrets.compare_digest(states[0].encode("utf-8"), pending["state"].encode("ascii"))):
        raise AppError("This sign-in link has expired or is invalid. Return to DJList and connect again.")
    codes, errors = values.get("code", []), values.get("error", [])
    if errors:
        raise AppError("Spotify sign-in was declined or could not finish. You can try again.")
    if len(codes) != 1:
        raise AppError("Spotify did not return a valid sign-in response. Please reconnect.")
    return codes[0]


def exchange_code(code, pending):
    try:
        tokens = request_json(TOKEN_URL, form={
            "grant_type": "authorization_code", "code": code,
            "redirect_uri": pending["redirect_uri"], "client_id": pending["client_id"],
            "code_verifier": pending["verifier"],
        })
    except HTTPError:
        raise AppError("Spotify sign-in could not finish. Check the Client ID and exact redirect address in your Spotify app.", 502) from None
    if not isinstance(tokens.get("access_token"), str) or not tokens["access_token"]:
        raise AppError("Spotify did not provide access. Please reconnect.", 502)
    if not set(SCOPES.split()).issubset(set(tokens.get("scope", "").split())):
        raise AppError("Spotify did not grant the requested read permissions. Please reconnect.", 403)
    return tokens


class SpotifyClient:
    def __init__(self, client_id, tokens):
        self.client_id, self.tokens = client_id, tokens

    def get(self, url):
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.netloc != "api.spotify.com" or not parsed.path.startswith("/v1/") or parsed.fragment:
            raise AppError("Spotify returned an unexpected pagination address. Sync was stopped.", 502)
        refreshed = False
        for attempt in range(4):
            try:
                return request_json(url, token=self.tokens["access_token"])
            except HTTPError as error:
                if error.code == 401 and not refreshed and self.tokens.get("refresh_token"):
                    refreshed = True
                    try:
                        new = request_json(TOKEN_URL, form={"grant_type": "refresh_token",
                            "refresh_token": self.tokens["refresh_token"], "client_id": self.client_id})
                    except HTTPError:
                        raise AppError("Spotify sign-in expired. Connect Spotify again.", 401) from None
                    if not new.get("access_token"):
                        raise AppError("Spotify sign-in expired. Connect Spotify again.", 401)
                    self.tokens.update(new)
                    continue
                if error.code == 429:
                    try:
                        wait = max(0, float(error.headers.get("Retry-After", "2")))
                    except ValueError:
                        wait = 2
                    if wait > 10 or attempt == 3:
                        raise AppError("Spotify has temporarily limited requests. Wait a while, then refresh again.", 429) from None
                    time.sleep(wait)
                    continue
                if error.code == 403:
                    raise AppError("Spotify denied access. Check Premium, the app's allowed users, and that you own or collaborate on this playlist.", 403) from None
                if error.code == 404:
                    raise AppError("Spotify could not find or expose this playlist. Check its link and access.", 404) from None
                if error.code == 401:
                    raise AppError("Connect Spotify again to refresh this playlist.", 401) from None
                raise AppError("Spotify could not complete the request. Your collection was not changed.", 502) from None
        raise AppError("Spotify could not complete this refresh. Please try later.", 502)

    def tracks(self, source_id):
        endpoint = "me/tracks?limit=50" if source_id == "liked" else "playlists/%s/items?limit=50" % source_id
        url, visited, received, expected = API + endpoint, set(), 0, None
        tracks, unknown = [], 0
        while url:
            if url in visited:
                raise AppError("Spotify repeated a page. Refresh stopped without changing your collection.", 502)
            visited.add(url)
            page = self.get(url)
            if not isinstance(page.get("items"), list) or "next" not in page or not isinstance(page.get("total"), int):
                raise AppError("Spotify returned an incomplete page. Your collection was not changed.", 502)
            if expected is not None and page["total"] != expected:
                raise AppError("The playlist changed during refresh. Please refresh again.", 409)
            expected = page["total"]
            received += len(page["items"])
            for wrapper in page["items"]:
                wrapper = wrapper if isinstance(wrapper, dict) else {}
                track = wrapper.get("item") if "item" in wrapper else wrapper.get("track")
                if not isinstance(track, dict):
                    unknown += 1
                    continue
                if wrapper.get("is_local") or track.get("is_local") or track.get("type") == "episode":
                    continue
                if track.get("type") != "track" or not track.get("id") or not track.get("name"):
                    unknown += 1
                    continue
                album = track.get("album") if isinstance(track.get("album"), dict) else {}
                seconds = round(track.get("duration_ms", 0) / 1000)
                tracks.append({
                    "spotify_id": (track.get("linked_from") or {}).get("id") or track["id"],
                    "spotify_current_id": track["id"], "artists": "; ".join(a.get("name", "") for a in track.get("artists", [])),
                    "title_and_version": track["name"], "album": album.get("name", ""),
                    "artwork_url": spotify_artwork(album), "release_date": album.get("release_date", ""),
                    "explicit": track.get("explicit") if isinstance(track.get("explicit"), bool) else None,
                    "duration": "%d:%02d" % divmod(seconds, 60), "duration_ms": track.get("duration_ms", 0),
                    "isrc": (track.get("external_ids") or {}).get("isrc", ""),
                    "spotify_url": "https://open.spotify.com/track/" + track["id"],
                    "added_at": wrapper.get("added_at", ""),
                })
            url = page["next"]
            if url is not None and not isinstance(url, str):
                raise AppError("Spotify returned invalid pagination. Your collection was not changed.", 502)
        if received != expected:
            raise AppError("Spotify returned an incomplete playlist. Your collection was not changed.", 502)
        return tracks, unknown
