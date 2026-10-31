# DJList roadmap

Status for **0.5.0 — 31 October 2026**. Implemented does not mean verified on the
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

Synthetic audio fixtures exercise metadata reads for MP3, FLAC, WAV, AIFF, M4A
and Ogg/Opus. Raw AAC uses filename fallback. These checks do not establish
every encoding or tag layout, real SSD behavior or recording identity. Provider
metadata and preview-address checks do not establish audible playback or
checkout success. See the verification record for current browser and
automated-test evidence.

## Verification requiring the user's Mac or actual library

| Check | What remains to establish |
| --- | --- |
| Spotify | v0.5 sign-in, real playlist refresh and artwork in the user's installed app. |
| Audio and shops | Audible iTunes preview playback, opening a correct vendor page, current price, download format and region availability. |
| Mac setup | Native chooser selection and cancellation, saved-path checks and actual macOS permission prompts. Browsing must leave a draft until Save settings. |
| SSD and XML | Actual permissions, automatic reconnect refresh, changed XML detection, real duplicate-match quality and a fresh Rekordbox export. |
| Audio-file tags | Purchased files from the user's providers, unusual tags/encodings, external-drive performance and cache refresh after file changes. Synthetic fixtures do not verify the real collection. |
| Download inbox | Review a small real batch, check the exact mix, confirm links in their intended locations and verify preserved annotations and later preparation states. |
| Moved-file review | Confirm a real old/current folder mapping and the selected recording before relying on changed links. Simulated file tests cannot verify the user's audio identity. |
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
  measure BPM/key accuracy on representative music and keep uncertainty visible.
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
- **Renamed or reorganized files:** reviewed matching beyond an unchanged
  relative folder layout. Current moved-file review does not guess filename
  changes or silently rewrite links.
- **Purchases/downloads:** checkout, receipt handling and full-track downloads.
  The current inbox reviews files the user has already downloaded; it does not
  buy, download or move music. Permitted previews are not DJ audio downloads.
- **Read-only Rekordbox database adapter:** support the chosen Rekordbox version,
  SQLCipher and its schema, with enforced read-only access and a consistent view
  of live database state. Assess packaging and licence requirements before adding
  a dependency. The current database path is a note, not an active connection.
- **Rekordbox import/export and cues:** design a controlled workflow around
  compatible versions and the user's existing cue process. No live database
  writes or CueGen execution are enabled.

### Gates before Rekordbox writes

Before enabling future track, tag or cue writes, verify version compatibility,
a restorable library backup, a closed Rekordbox session and an explicitly
selected small new batch on the user's Mac. Test the full library state that
Rekordbox expects, not only an individual SQL row. Existing audio and library
records must be preserved. A missing external drive must never cause a fallback
to a different Rekordbox database. Current preparation and cue-note fields
record the user's own work; they do not verify or generate cues.

For the technical constraints, see the unofficial
[pyrekordbox master-database documentation](https://pyrekordbox.readthedocs.io/en/stable/quickstart.html)
and SQLite's [live WAL/read-only behavior](https://www.sqlite.org/wal.html) and
[read-only connection options](https://www.sqlite.org/uri.html). Those libraries
and capabilities are not bundled or enabled by this build.
