# DJList roadmap

Status for **0.6.0, API 6 — 31 October 2026**. Implemented does not mean verified on the
user's Mac. [VERIFICATION.md](VERIFICATION.md) records the checks and limitations;
[AGENTS.md](AGENTS.md) defines the project's data and operation boundaries.

## Implemented

| Area | Current behavior |
| --- | --- |
| Track workflow | One Spotify playlist or Liked Songs; repeat refresh preserves ownership, tags, notes and confirmed links. Artwork with a coloured music-icon fallback. |
| Track table | Pagination, search, filters, sorting and four direct action icons with keyboard/hover labels. Responsive rows fit the viewport. |
| iTunes | Automatic paced lookup after Spotify refresh; listed prices, broad genre, matching reasons and user-initiated promotional previews. Suggestions stay separate from saved fields. |
| More shop links | Manual MusicBrainz ISRC lookup can return community-listed Bandcamp/Beatport track links. Version notes and incomplete evidence require review. Live API operation was not verified from this development environment. |
| Folder setup | Native Mac Browse controls fill Settings drafts. Save settings commits them. Check setup reviews saved paths, current XML freshness, inventory and automatic-refresh status. |
| Library review | Read-only media scanning and persistent XML inventory; refresh a saved XML path, inspect offline entries and review possible duplicates. A new Rekordbox export is still needed after its library changes. |
| Automatic refresh | Five-second checks while the app runs. Changed XML must remain stable for five seconds; media scans run at startup, after relevant settings changes and on reconnection. This does not export XML or continuously scan nested music folders. |
| Embedded metadata | Bundled TinyTag reads existing tags and duration locally, with no extra installation. Cache by file identity and selected root, retain filename fallback and give XML metadata precedence. No audio analysis or file changes. |
| Download inbox | Scan an explicitly selected folder, including outside the music root. Review matches and confirm up to 500 links in place within ten minutes. Record ownership, fill only empty BPM/key and preserve personal tags, notes and existing links. |
| Moved-file links | Review exact old-folder/current-folder mappings and explicitly update selected DJList links. Preserve all other annotations. Audio files and Rekordbox remain unchanged. |
| Library health | Searchable, filtered, paginated findings and CSV export. Distinguish files missing on an available drive from offline storage, paths outside selected folders and unsafe paths. Retain missing XML entries. |
| Missing-file replacement | Review metadata-based candidates for a saved DJList link confirmed missing inside an available selected folder. This can find renamed or reorganized files. Select one candidate, recheck the source and target, and change only the DJList file address. Reviews expire after ten minutes. |
| Exact-file comparison | On request, compare full-file SHA-256 checksums within an eligible suspected duplicate group. Cache results against current file identity. Distinct bytes remain inconclusive about acoustic identity; nothing is deleted or merged. |
| Database snapshots | Copy the selected, closed `master.db` as raw read-only bytes, plus its optional companion playlist XML. Use a separate destination, defaulting to DJList's own data folder; verify copied bytes/checksums and retain history. Daily checks run while DJList is open; unchanged sources reuse a checked snapshot. No database connection, query, write, restore or automatic deletion. |
| Reviewed cue XML | Calculate hotcue suggestions from the saved XML's 4/4 grid, including tempo changes; preserve original cues, loops, grid and metadata. Export only selected markers in unused A–H slots as a separate one-track XML. If no grid exists, explicitly checked BPM 20–400 and a first downbeat can define a constant 4/4 grid. No audio analysis or live import. |

Synthetic audio fixtures exercise metadata reads for MP3, FLAC, WAV, AIFF, M4A
and Ogg/Opus. Raw AAC uses filename fallback. These checks do not establish
every encoding or tag layout, real SSD behavior or recording identity. Provider
metadata and preview-address checks do not establish audible playback or
checkout success. See the verification record for current browser and
automated-test evidence.

## Verification requiring the user's Mac or actual library

| Check | What remains to establish |
| --- | --- |
| Spotify | v0.6 sign-in, real playlist refresh and artwork in the user's installed app. |
| Audio and shops | Audible iTunes preview playback, opening a correct vendor page, current price, download format and region availability. |
| Mac setup | Native chooser selection and cancellation, saved-path checks and actual macOS permission prompts. Browsing must leave a draft until Save settings. |
| SSD and XML | Actual permissions, automatic reconnect refresh, changed XML detection, real duplicate-match quality and a fresh Rekordbox export. |
| Audio-file tags | Purchased files from the user's providers, unusual tags/encodings, external-drive performance and cache refresh after file changes. Synthetic fixtures do not verify the real collection. |
| Download inbox | Review a small real batch, check the exact mix, confirm links in their intended locations and verify preserved annotations and later preparation states. |
| Moved-file review | Confirm a real old/current folder mapping and the selected recording before relying on changed links. Simulated file tests cannot verify the user's audio identity. |
| Library health and repair | Check real missing/offline distinctions, duplicate candidates and selected replacements on the external SSD. A checksum comparison establishes file-byte identity, not the correct mix when bytes differ. |
| Database snapshots | Confirm the actual active master.db, Mac process and locking checks, destination permissions, daily operation and interruption behavior. A checked raw copy is not a full library backup or proof of a working restore. Keep Rekordbox's native Backup Library workflow. |
| Cue XML import | Establish the installed Rekordbox version and manually import one reviewed track after a full library backup. Verify existing cues, loops, selected additions, file location and tempo transitions. Check grids and manually entered downbeats by listening. |
| Accessibility | Safari, VoiceOver, keyboard behavior, zoom and a full WCAG 2.2 AA audit. Existing browser checks cover a defined subset. |

Ordinary updates keep DJList's records in its separate application-data folder.
They do not require starting a new collection or importing saved notes again.

## Provider-access requirements

**Direct store catalogues:** MusicBrainz link discovery does not provide live
Bandcamp or Beatport inventory, prices, previews, BPM or key. Broader store
integration requires a supported provider interface, permission to use its data
and any required credentials. No unverified private endpoint or borrowed
credential is part of the app. Cross-store price comparison also needs region
and format information that can be compared fairly.

**Spotify BPM/key:** the current Spotify application cannot use the restricted
audio-feature/analysis endpoints for these fields. Existing Rekordbox XML
values, embedded file tags or manually entered values are the available
sources. Spotify artist genres would be broad suggestions, not track-specific
DJ mood or energy tags.

**MusicBrainz availability:** the manual lookup implementation has response and
failure-handling checks, but live API retrieval still needs verification on a
reachable connection. An unavailable response is shown as unavailable; it is
not proof that the song has no shop links.

## Deferred development

- **Local audio analysis:** choose and package a supported analysis engine;
  measure BPM/key, beat/downbeat and phrase accuracy on representative music
  and keep uncertainty visible. The XML cue planner performs calculations from
  supplied grid data; it does not analyze the recording.
- **Continuous nested-folder monitoring:** reliably detect additions or changes
  inside an already connected music folder without expensive repeated scans.
  Current automatic refresh checks root availability and the selected XML;
  Refresh media and Scan inbox remain the controls for new files.
- **Persistent Spotify sign-in:** store refresh credentials using an appropriate
  local credential store, with reconnection and expiry handling. Current tokens
  remain in memory for one run.
- **Multiple Spotify sources:** design source membership and removal behavior
  before allowing additional playlists to share the same saved track records.
- **Richer tags and exports:** sourced artist genres, additional metadata fields
  and exports of separate suggestions. Current CSV is a readable saved-track
  copy, not a complete database backup.
- **Installer/updater:** simplify launch and upgrades while keeping stored data
  separate and avoiding mixed interface/server versions.
- **Broader file identification:** acoustic fingerprints for the same recording
  across different formats or encodings, and stronger evidence for difficult
  replacement matches. Renamed-file candidates based on available metadata are
  now reviewable; uncertain matches still require a human decision. No automatic
  merge, deletion or relink is planned without a separate explicit design.
- **Purchases/downloads:** checkout, receipt handling and full-track downloads.
  The current inbox reviews files the user has already downloaded; it does not
  buy, download or move music. Permitted previews are not DJ audio downloads.
- **Read-only Rekordbox database adapter:** support the chosen Rekordbox version,
  SQLCipher and its schema, with enforced read-only access and a consistent view
  of database state, preferably from a verified snapshot. Assess packaging and
  licence requirements before adding a dependency. The selected database is
  currently read only as raw backup bytes; its records are not queried.
- **Full-library backup and restore:** database snapshots exclude analysis files
  and music. Continue Rekordbox's native full-library backup; any DJList restore
  workflow requires separate validation. Running backups while DJList is closed
  would also need an explicitly configured OS scheduling mechanism.
- **Automatic Rekordbox import and phrase cues:** validate XML import behavior
  on the installed version, then assess controlled integration with existing
  analysis or CueGen. Reviewed standalone cue XML export is implemented; live
  database writes, CueGen execution and audio-based phrase detection are not.

The [open-source tools assessment](docs/OPEN_SOURCE_DJ_TOOLS.md) records primary
sources, licenses and proposed roles for CueGen, pyrekordbox, librosa,
Chromaprint and other candidates. These projects were researched; no additional
analysis engine, database adapter, binary or model was incorporated in v0.6.

### Gates before Rekordbox writes

Before enabling future track, tag or cue writes, verify version compatibility,
a restorable library backup, a closed Rekordbox session and an explicitly
selected small new batch on the user's Mac. Test the full library state that
Rekordbox expects, not only an individual SQL row. Existing audio and library
records must be preserved. A missing external drive must never cause a fallback
to a different Rekordbox database. A byte-verified database snapshot alone does
not satisfy the full-library restore requirement. Preparation statuses and
cue-note fields remain manual records; the separate cue planner creates a
reviewed XML download without changing the live collection.

For the technical constraints, see the unofficial
[pyrekordbox master-database documentation](https://pyrekordbox.readthedocs.io/en/stable/quickstart.html)
and SQLite's [live WAL/read-only behavior](https://www.sqlite.org/wal.html) and
[read-only connection options](https://www.sqlite.org/uri.html). Those libraries
and capabilities are not bundled or enabled by this build.
