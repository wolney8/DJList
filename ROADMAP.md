# DJList roadmap

Status for **0.7.0, API 7 — 31 October 2026**. Implemented does not mean verified on the
user's Mac. [VERIFICATION.md](VERIFICATION.md) records the checks and limitations;
[AGENTS.md](AGENTS.md) defines the project's data and operation boundaries.

## Unreleased work — no version change

The `work/internal-cuegen-relocation` branch keeps version 0.7.0/API 7. It adds
internal CueGen provisioning/startup checks and an evidence-based Rekordbox
relocation handoff. It is not a new release or proof of installation on the
user's Mac. See [VERIFICATION.md](VERIFICATION.md) for actual native-engine CI
results. A live database/ANLZ relocation writer remains unimplemented; the new
handoff must not be described as automatic database updates.

## Implemented

| Area | Current behavior |
| --- | --- |
| Track workflow | One Spotify playlist or Liked Songs; repeat refresh preserves ownership, tags, notes and confirmed links. Artwork with a coloured music-icon fallback. |
| Track table | Pagination, search, filters, sorting and labelled direct action icons. Price source/check time and manual price/currency; responsive rows fit the viewport. |
| Background activity | Running task/phase, elapsed time, available counts and recent outcomes. Spotify-only settings can save during disk scans; conflicting folder changes name the work causing a wait. |
| Purchase progress | Store links, per-currency wishlist totals, Mark bought and Check Incoming. Opening checkout never implies purchase or download; actual payments/downloads happen at the shop. |
| iTunes | Automatic paced lookup after Spotify refresh; listed prices, broad genre, matching reasons and user-initiated promotional previews. Suggestions stay separate from saved fields. |
| More shop links | Manual MusicBrainz ISRC lookup can return community-listed Bandcamp/Beatport track links. Version notes and incomplete evidence require review. Live API operation was not verified from this development environment. |
| Folder setup | Native Mac Browse controls fill Settings drafts. Save settings commits them. Check setup reviews saved paths, current XML freshness, inventory and automatic-refresh status. |
| Library workspace | Separate page with files, duplicates, missing/offline issues, metadata and history views. Per-copy paths/details, previews and XML cue/loop/grid evidence with unknown/stale states. A fresh Rekordbox export is still needed after its library changes. |
| Local listening | Indexed read-only audio ranges, native controls, position slider and cue jumps. Optional browser waveform up to 32 MiB/15 minutes and live spectrum. Native streaming cap 512 MiB; codec compatibility still depends on the browser. |
| File management | Reviewed rename and Move to Removed for selected indexed audio; up to 50 files per batch. Same-drive recovery storage and reviewed undo, no overwrite or permanent deletion. Updates DJList links; Rekordbox relocation/record cleanup remains separate. |
| Metadata review | Conservative text parsing for empty/repeated artist, title, explicit featuring credits and retained remix wording. Reviewed corrections/reset in DJList only; stale file identities invalidate them. No embedded-tag or Spotify writes. |
| Automatic refresh | Five-second checks while the app runs. Changed XML must remain stable for five seconds; media scans run at startup, after relevant settings changes and on reconnection. This does not export XML or continuously scan nested music folders. |
| Embedded metadata | Bundled TinyTag reads existing tags and duration locally, with no extra installation. Cache by file identity and selected root, retain filename fallback and give XML metadata precedence. No audio analysis or file changes. |
| Download inbox | Scan an explicitly selected folder, including outside the music root. Review matches and confirm up to 500 links in place within ten minutes. Record ownership, fill only empty BPM/key and preserve personal tags, notes and existing links. |
| Moved-file links | Review exact old-folder/current-folder mappings and explicitly update selected DJList links. Preserve all other annotations. Audio files and Rekordbox remain unchanged. |
| Library health | Searchable, filtered, paginated findings and CSV export. Distinguish files missing on an available drive from offline storage, paths outside selected folders and unsafe paths. Retain missing XML entries. |
| Missing-file replacement | Review metadata-based candidates for a saved DJList link confirmed missing inside an available selected folder. This can find renamed or reorganized files. Select one candidate, recheck the source and target, and change only the DJList file address. Reviews expire after ten minutes. |
| Exact-file comparison | On request, compare full-file SHA-256 checksums within an eligible suspected duplicate group. Cache results against current file identity. Distinct bytes remain inconclusive about acoustic identity; nothing is deleted or merged. |
| Database snapshots | Copy the selected, closed `master.db` as raw read-only bytes, plus its optional companion playlist XML. Use a separate destination, defaulting to DJList's own data folder; verify copied bytes/checksums and retain history. Daily checks run while DJList is open; unchanged sources reuse a checked snapshot. No database connection, query, write, restore or automatic deletion. |
| Reviewed cue XML | Calculate hotcue suggestions from the saved XML's 4/4 grid, including tempo changes; preserve original cues, loops, grid and metadata. Export only selected markers in unused A–H slots as a separate one-track XML. If no grid exists, explicitly checked BPM 20–400 and a first downbeat can define a constant 4/4 grid. No audio analysis or live import. |
| Optional CueGen adapter | External CueGen 1.0.37 on macOS, with a freshly checked snapshot, disposable database working copy and mandatory sandbox. Parse one selected track's phrase/Mixed In Key suggestions, review/listen, then export chosen XML additions. No live database writes or automatic import. |

Synthetic audio fixtures exercise metadata reads for MP3, FLAC, WAV, AIFF, M4A
and Ogg/Opus. Raw AAC uses filename fallback. These checks do not establish
every encoding or tag layout, real SSD behavior or recording identity. Provider
metadata and preview-address checks do not establish audible playback or
checkout success. See the verification record for current browser and
automated-test evidence.

## Verification requiring the user's Mac or actual library

| Check | What remains to establish |
| --- | --- |
| Spotify and activity | v0.7 sign-in during a real media scan, real playlist refresh, useful progress reporting and artwork in the installed app. |
| Audio and shops | Audible iTunes preview playback, opening a correct vendor page, current price, download format and region availability. |
| Mac setup | Native chooser selection and cancellation, saved-path checks and actual macOS permission prompts. Browsing must leave a draft until Save settings. |
| SSD and XML | Actual permissions, automatic reconnect refresh, changed XML detection, real duplicate-match quality and a fresh Rekordbox export. |
| Audio-file tags | Purchased files from the user's providers, unusual tags/encodings, external-drive performance and cache refresh after file changes. Synthetic fixtures do not verify the real collection. |
| Local player | Real MP3/FLAC/WAV/AIFF/M4A files in the user's browser, audible playback, waveform decoding, seeking and cue alignment. A supported extension does not prove codec support. |
| File management | One copied test file: rename/undo, Move to Removed/undo, collision handling and actual SSD permissions. Check DJList link preservation and Rekordbox relocation separately. Removed storage does not free space. |
| Metadata corrections | Real problematic tags, artist roles, remix/featuring wording, correction persistence and reset. The parser proposes text changes, not authoritative credits. |
| Download inbox | Review a small real batch, check the exact mix, confirm links in their intended locations and verify preserved annotations and later preparation states. |
| Moved-file review | Confirm a real old/current folder mapping and the selected recording before relying on changed links. Simulated file tests cannot verify the user's audio identity. |
| Library health and repair | Check real missing/offline distinctions, duplicate candidates and selected replacements on the external SSD. A checksum comparison establishes file-byte identity, not the correct mix when bytes differ. |
| Database snapshots | Confirm the actual active master.db, Mac process and locking checks, destination permissions, daily operation and interruption behavior. A checked raw copy is not a full library backup or proof of a working restore. Keep Rekordbox's native Backup Library workflow. |
| Cue XML import | Establish the installed Rekordbox version and manually import one reviewed track after a full library backup. Verify existing cues, loops, selected additions, file location and tempo transitions. Check grids and manually entered downbeats by listening. |
| CueGen | Actual 1.0.37 executable/runtime, macOS sandbox availability, phrase/Mixed In Key data and one-track output. Exercise source protection and XML import on test copies before relying on it for the collection. |
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
  supplied grid data; the optional CueGen adapter consumes existing analysis.
  Neither creates new phrase/beat/key analysis from unanalysed recordings.
- **Continuous nested-folder monitoring:** reliably detect additions or changes
  inside an already connected music folder without expensive repeated scans.
  Current automatic refresh checks root availability and the selected XML;
  Refresh media and Scan inbox remain the controls for new files.
- **Persistent Spotify sign-in:** store refresh credentials using an appropriate
  local credential store, with reconnection and expiry handling. Current tokens
  remain in memory for one run.
- **Multiple Spotify sources:** design source membership and removal behavior
  before allowing additional playlists to share the same saved track records.
- **Richer tags and exports:** sourced artist genres, additional metadata fields,
  reviewed embedded-tag writing and exports of separate suggestions. Current
  corrections stay in DJList; CSV is not a complete database backup.
- **Installer/updater:** simplify launch and upgrades while keeping stored data
  separate and avoiding mixed interface/server versions.
- **Broader file identification:** acoustic fingerprints for the same recording
  across different formats or encodings, and stronger evidence for difficult
  replacement matches. Renamed-file candidates based on available metadata are
  now reviewable; uncertain matches still require a human decision. No automatic
  merge, deletion or relink is planned without a separate explicit design.
- **Purchases/downloads:** automated provider checkout, receipt handling and
  full-track downloads. The purchase panel opens stores and records progress;
  the inbox reviews files the user has downloaded. Promotional previews are not
  DJ audio downloads.
- **Read-only Rekordbox database adapter:** support the chosen Rekordbox version,
  SQLCipher and its schema, with enforced read-only access and a consistent view
  of database state, preferably from a verified snapshot. Assess packaging and
  licence requirements before adding a dependency. The selected database is
  currently read only as raw backup bytes. CueGen can query an isolated working
  copy for its narrow one-track suggestion workflow; that is not a general
  live-library record reader.
- **Full-library backup and restore:** database snapshots exclude analysis files
  and music. Continue Rekordbox's native full-library backup; any DJList restore
  workflow requires separate validation. Running backups while DJList is closed
  would also need an explicitly configured OS scheduling mechanism.
- **Automatic Rekordbox import and broader CueGen writes:** validate XML import
  behavior on the installed version. The isolated CueGen adapter is implemented;
  live database writes, MyTag/track-colour changes, cue replacement/removal,
  new audio-based phrase detection and automatic import remain unavailable.

The [open-source tools assessment](docs/OPEN_SOURCE_DJ_TOOLS.md) records primary
sources, licenses and proposed roles for CueGen, pyrekordbox, librosa,
Chromaprint and other candidates. CueGen has a version-specific external adapter
and an attributed MIT colour palette. No executable, new audio-analysis engine,
general database adapter or model is bundled in v0.7.

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
