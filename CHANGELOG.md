# Changelog

## 0.7.0 — 31 October 2026

- Advance the local interface/API contract to **API 7**.
- Show background activity, phases, elapsed time, available counts and recent
  outcomes. Allow Spotify-only connection changes during unrelated disk work;
  retain serialization for folder changes and name the task causing a wait.
- Add a wishlist Price column and sorting, with candidate source/check time,
  recorded price/currency overrides and explicit unknown prices. Add a purchase
  panel with currency-separated totals, store links, Mark bought and Check
  Incoming. No automatic payment or full-track download is performed.
- Replace the main library-review modal with a dedicated workspace for files,
  duplicate groups, missing/offline issues, metadata review and file history.
  Show per-copy paths/details and sourced XML cue, loop and grid evidence;
  distinguish unknown preparation from a snapshot containing zero cues.
- Add local audio playback, scrubbing, optional browser-decoded waveform and
  live spectrum. Bound waveform requests to 32 MiB/15 minutes and local streams
  to 512 MiB; use indexed identifiers and guarded read-only range responses.
- Add reviewed individual/batch rename and recoverable Move to Removed actions.
  Keep Removed files on the same drive, retain history and support reviewed
  undo without overwrite. File changes require closed library-writing tools,
  fresh file/folder evidence and explicit selections. Live Rekordbox locations
  remain unchanged and require relocation or record cleanup there.
- Add text-based artist/title/featuring cleanup with retained mix wording,
  before/after review and reset. Store corrections only in DJList, tied to the
  current audio identity; preserve embedded tags, Spotify data and annotations.
- Add an optional adapter for an external **CueGen 1.0.37** macOS executable.
  Recheck a database snapshot, prepare an isolated working copy and require the
  macOS sandbox. Deny network and source writes; validate one-track dry-run
  output before presenting selectable phrase/Mixed In Key cue suggestions.
- Expose supported CueGen hot/memory, spacing, timing, colour, name and loop
  options. Preserve original XML cues, loops, metadata and grid during selected
  export. Keep MyTag/track-colour writes, cue replacement/removal, automatic
  import and live database writes unavailable.
- Attribute the MIT-licensed CueGen colour palette and retain its upstream
  licence. No CueGen executable or additional analysis engine is bundled.
- Keep actual Mac/SSD file operations, external CueGen execution, native
  playback/codec behaviour and Rekordbox import as explicit verification work
  in [VERIFICATION.md](VERIFICATION.md).

## 0.6.0 — 31 October 2026

- Advance the local interface/API contract to **API 6** and keep the startup
  asset snapshot and version-mismatch restart checks.
- Add **Library health** with search, issue filters, pagination and CSV export.
  Distinguish a missing file from an unavailable selected drive, and report
  outside-folder or unsafe paths without discarding the saved inventory.
- Add reviewed replacement candidates for missing DJList file links, including
  renamed or reorganized files with matching metadata. Require an available
  selected source folder, explicit candidate selection and a fresh ten-minute
  review. Revalidate metadata, file and root identity before changing only the
  selected address; preserve ownership, preparation state and all annotations.
- Add **Check file contents** for suspected duplicate groups. Compare complete
  files with SHA-256 under bounded read-only access and retain results only
  while file identity matches. Different tags or encodings can produce different
  bytes without proving a different recording. No duplicate is deleted or merged.
- Add manual and daily **Rekordbox database snapshots**, using the explicitly
  selected `master.db` while Rekordbox and library-writing tools are closed.
  Open the source as raw `O_RDONLY` bytes with locking and source checks; do not
  open a SQLite/SQLCipher connection, query records, recover journals or write to
  the database. Unavailable or busy sources wait without selecting another file.
- Default snapshots to `rekordbox-backups` inside DJList's own data folder, with
  an optional separate destination. Copy the optional `masterPlaylists6.xml`
  companion, record a checksum manifest and compare copied bytes. Recheck an
  existing snapshot when the source is unchanged. Keep previous and incomplete
  copies; provide status/history checks without automatic deletion or restore.
- Run due backup checks only while DJList is open and in the real collection.
  Clearly distinguish a checked database snapshot from a full Rekordbox library
  backup: analysis files and music are excluded, and actual restoration remains
  unverified. Rekordbox's native **Backup Library** remains necessary.
- Add reviewed, separate one-track **cue XML export**. Calculate suggestions
  from an existing 4/4 XML grid, including tempo changes, while preserving all
  original track metadata, tempo markers, cues and loops. Add only selected
  suggestions in unused A–H slots and skip nearby existing cue/loop positions.
- Allow an explicitly checked BPM between 20 and 400 plus a first downbeat to
  define a constant grid only when the XML track has no grid. Use single-use
  ten-minute reviews and revalidate source files and settings before export.
  No audio analysis, phrase detection, automatic import or CueGen execution is
  enabled; actual Rekordbox import behavior remains unverified.
- Document potential open-source integrations and their boundaries in
  [OPEN_SOURCE_DJ_TOOLS.md](docs/OPEN_SOURCE_DJ_TOOLS.md). No candidate analysis
  engine, database adapter, executable or model was added to the runtime.
- Retain the verification gaps in [VERIFICATION.md](VERIFICATION.md): the actual
  Mac, external SSD, database restoration, cue import and real collection still
  require checks separate from development fixtures.

## 0.5.0 — 31 October 2026

- Add native Mac **Browse** controls for the music folder, Rekordbox XML and
  optional download inbox. Selections fill the Settings draft; **Save settings**
  is still required. **Check setup** reports saved paths, XML freshness,
  inventory and automatic-refresh status.
- Add automatic library refresh, enabled by default while DJList is running.
  Check selected paths every five seconds, wait for a stable XML export before
  importing, and scan media at startup, after relevant settings changes and
  on drive reconnection. Keep manual refresh controls. This does not generate
  XML or continuously scan all nested music-folder changes.
- Bundle unmodified, MIT-licensed TinyTag 2.3.2 with its licence and checksums;
  no extra Python package installation is needed. Read existing embedded tags
  and file duration locally without analyzing or changing audio. Cache results
  by file identity and selected folder; retain filename fallback, including
  raw AAC, and stronger existing Rekordbox XML metadata.
- Label metadata provenance and read warnings. Preserve separate version notes
  in matching so a hidden mix/subtitle does not become an unchecked match.
  Cover supported metadata paths with synthetic fixtures; actual purchased
  files and Mac external-drive behavior remain unverified.
- Retry transient metadata read errors on refresh. Compare setup status with
  the actual imported XML fingerprint, clear resolved watcher warnings after
  manual refresh, and serialize file-link changes against folder changes.
- Add a **Download inbox** for an explicitly selected folder, including one
  outside the music root. Review suggested Spotify matches, select files and
  confirm a batch of links in place. Nothing is preselected or moved, and
  existing file links are never overwritten.
- Expire inbox reviews after ten minutes and limit a preview to 500 files.
  Recheck selected files, folder identity, tracks and links before an atomic
  batch update. Confirmation records ownership and downloaded preparation
  where appropriate, fills only empty BPM/key fields, and preserves personal
  tags, notes and later preparation states.
- Keep `master.db` access and all Rekordbox writes disabled. Document a future
  version-specific read-only adapter and the separate compatibility, backup
  and reviewed-batch requirements for any future writes.
- Make the scrollable setup results keyboard focusable with a named region
  and visible focus. Verify the new dialogs at narrow widths and in both themes.
- Keep verification gaps explicit: real Mac native pickers, SSD permissions,
  actual audio tags, live MusicBrainz lookup, audible previews and a full
  accessibility audit still require the checks in [VERIFICATION.md](VERIFICATION.md).

## 0.4.0 — 31 October 2026

- Replace the row Actions chooser with four direct icon buttons, labelled on
  hover and keyboard focus. Keep editing, downloads, refresh and library review
  accessible within responsive rows.
- Add **Find moved files**: preview exact relative-path matches from an old music
  folder to the current one, select checked candidates and update saved links.
  Preserve ownership, preparation state and all other annotations. Never move
  audio files or change Rekordbox.
- Recheck file identity, availability and saved links before applying an entire
  selected batch. Reject stale previews, changed files, size mismatches and
  symlinks; previews expire after ten minutes.
- Add manual MusicBrainz ISRC lookup for community-listed direct Bandcamp and
  Beatport purchase links. These links carry no verified shop prices, previews,
  BPM or key; automatic iTunes catalogue lookup remains available.
- Surface recording disambiguation and partial evidence for review. Keep
  malformed or limited provider responses distinct from a complete negative
  lookup, and preserve the existing draft when a shop link is selected.
- Add a roadmap separating implemented work, Mac verification, provider-access
  requirements and deferred development. Live MusicBrainz API access remains
  unverified from the development environment.

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
