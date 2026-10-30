# Changelog

## 0.3.0 — 30 October 2026

- Queue iTunes Store lookups after Spotify refresh for new or changed tracks,
  results at least 24 hours old, and failed or unfinished lookups. Add a Settings
  switch, progress, cancellation and paced background requests.
- Keep shop candidates, listed prices and sourced genre suggestions across
  restarts, separate from manual annotations and ownership. The iTunes catalogue
  used here does not supply BPM or musical key.
- Show Spotify album artwork with a consistent coloured fallback.
- Add page-size choices, shop/library filters, sorting and per-track actions.
- Save an XML export path for one-click refresh. A fresh export from Rekordbox
  is still needed when its collection changes.
- Retain XML inventory while media is offline, preserve v0.2 XML metadata on
  upgrade and keep the previous snapshot when XML refresh fails.
- Add library search and conservative duplicate review. File linking and
  ownership still require the user's confirmation.
- Warn about partial media scans and skipped XML entries, and retain previous
  inventory entries when a media scan cannot read every folder or file.

## 0.2.0 — 30 October 2026

- Fix mixed UI/API builds by snapshotting assets at server startup.
- Report running version and explain port conflicts and required restarts.
- Avoid a macOS startup stall caused by an unnecessary hostname lookup.
- Add automatic UK iTunes catalogue candidates, listed prices and comparison
  reasons for title, artist, mix and duration.
- Add user-initiated promotional previews with the approved Apple badge.
- Let users select a shop link and optionally append its sourced genre to a
  draft without overwriting annotations or marking ownership.
- Keep other shop links clearly separate from live catalogue results.
- Move source to GitHub, add automated checks and remove personal defaults.

## 0.1.1

- Add editable provider search links and preserve unsaved track details.

## 0.1.0

- Introduce local Spotify tracking, persistent annotations, read-only file/XML
  matching, CSV export and isolated demonstration records.
