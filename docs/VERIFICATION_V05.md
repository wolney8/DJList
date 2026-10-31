# DJList 0.5.0 — verification record

31 October 2026. Development build; this record is not a claim of release
readiness or WCAG conformance. The user reports that v0.2.0 works on their Mac,
including Spotify connection. That report does not verify the v0.5.0 changes.

## Verified locally

- **195 Python unit/regression tests pass.** They use temporary records,
  synthetic audio metadata and simulated provider responses. Run
  `python3 -m unittest discover -s tests -v`.
- **Six JavaScript model tests pass.** These cover pagination, numeric BPM
  sorting, combined filters, sourced metadata/manual-field precedence and
  artwork URL/colour handling. Run `node --test tests/test_ui_model.js`.
- **The real HTTP smoke test passes.** A temporary loopback server exercises
  state, saved edits, setup checks, embedded metadata and explicit selection of
  inbox links. It confirms ownership/downloaded status, fills an empty key,
  preserves saved BPM, notes and tags, and checks unchanged source audio bytes.
  Prior media/XML refresh, library review, offline-copy warnings, simulated
  iTunes/MusicBrainz routes, moved-file review, stale-plan rejection, annotation
  preservation, startup asset consistency and occupied-port guidance also pass.
  Run `python3 tools/smoke_test.py`. No real collection, Spotify account or user
  audio is used; the smoke test does not open a real native chooser.
- JavaScript syntax, launcher syntax and `git diff --check` pass.

### v0.5 folder setup and automatic refresh

Native-picker tests use mocked process responses. They check fixed chooser
scripts, allowed kinds, cancellation, timeout/error recovery, concurrent-picker
rejection and request guards. Browsing returns a draft path without changing
saved settings. **Actual macOS chooser interaction remains unverified.**

Setup checks distinguish a readable but not yet imported XML, an imported
snapshot matching the selected file's source fingerprint, changed or failed
exports, and a missing file. A failed import preserves the prior snapshot.
These checks compare files and saved inventory; they cannot detect unexported
changes inside Rekordbox.

Watcher tests cover startup, stable five-second XML detection, disconnection
and reconnection, disabled/demo modes, busy-operation retry, and shutdown.
They verify that unchanged roots do not cause repeated recursive scans,
failure backoff starts after a slow operation finishes, retries are bounded,
and a successful manual refresh clears exhausted errors without a remount.
An application-level check imports a changed export without altering track
annotations. Clocks and drive changes are simulated; this is not evidence of
real SSD or macOS event behavior.

### v0.5 embedded audio metadata

Synthetic fixtures exercise **MP3, FLAC, WAV, AIFF, M4A and Ogg/Opus** identity,
existing BPM/key tags and duration. Tests verify unchanged source bytes, no
artwork extraction, bounded fields/reads, explicit malformed-file warnings and
raw AAC filename fallback. They do not test every encoding or purchased file.

Path checks reject symlinks, traversal, files outside the selected root and
special files. File replacement during a read discards its metadata. Cache
tests cover selected-root identity, same-size changes, XML precedence, offline
retention and annotation preservation. A transient parser `OSError` regression
failed before the fix: it now retries on the next scan despite an unchanged
fingerprint, then reuses the successful result. Errors stay visible without
becoming permanently reusable cache entries. Separate version notes require
review. Vendored TinyTag runtime checksums match the recorded upstream files.

### v0.5 inbox review and link concurrency

Tests cover an explicitly selected inbox outside the music root, real-reader
synthetic FLAC matching, filename/version warnings and selected-pair linking.
They check 500-file batch limits, ten-minute expiry, single-use tokens,
partial/slow scans, root/file identity changes, symlink swaps, changed tracks
or links, and whole-batch rollback on stale evidence or database failure.
Existing links cannot be overwritten. Confirmation preserves concurrent
annotations and later preparation states, fills only empty BPM/key and leaves
personal tags, notes and audio bytes unchanged.

Threaded link tests serialize settings/demo changes and competing file-link
edits, including clearing a path. Rechecks reject root or namespace changes and
same-size/same-mtime file replacement after metadata preparation. Ordinary
annotation edits remain possible during a tag read and survive linking; editing
notes on an unchanged offline link does not demand a metadata read. These are
temporary-file concurrency checks, not verification against a live Rekordbox
library. DJList does not open or edit `master.db`.

### Store lookup and data preservation

Existing regression coverage checks successful-sync enqueueing, disabled
automation, failed sync, 24-hour freshness, forced refresh/cache bypass,
queue deduplication, new work arriving during lookup, bounded retries, progress,
cancellation and restart recovery. In-flight results are discarded after
cancellation or a track identity change. A failed refresh retains a previous
successful result.

Lookup results persist separately from annotations. Tests confirm that ownership,
confirmed purchase links, notes, tags and manually entered BPM/key are preserved.
Ambiguous versions and incomplete provider responses remain review candidates.
No invented BPM/key is supplied when the catalogue lacks it.

### Manual MusicBrainz shop-link discovery

Tests verify the fixed ISRC endpoint, required includes, identifiable User-Agent,
request pacing, rate-limit cooldowns, bounded response/cache sizes and rejection
of redirects, HTML and malformed JSON. Only allowlisted HTTPS Bandcamp/Beatport
track links are exposed; URLs with credentials, unsafe paths or extra query
parameters are rejected. No shop page or audio content is fetched.

Same-ISRC records still undergo artist, title/version and duration comparison.
Wrong artists or base titles are excluded; version conflicts, unknown duration,
multiple recordings and recording disambiguation require review. Skipped,
malformed or truncated evidence is labelled incomplete and does not become a
cached ordinary negative result. These cases include regressions added after
independent review found hidden disambiguation and incomplete-response issues.

Application-route checks cover request guards, reference-track handling and
preservation of saved fields. Simulated success is not evidence of a successful
live MusicBrainz API request; see the provider section below.

### Library inventory and moved-file review

Existing tests cover saved-path refresh, offline XML entries, invalid/unreadable
XML retaining the prior snapshot, successful replacement, migration of v0.2 XML
metadata, pagination, duplicate groups and artist/version/duration matching.
Legacy missing identity fields remain filename evidence with lower confidence.
Partial media scans retain previous entries and a persistent skipped-file warning;
a completely failed refresh retains the previous successful scan status.

Moved-file tests cover exact relative-path mapping, bounded batches and the
previous-folder hint. Preview does not change links or probe arbitrary old
locations. Missing files, known size mismatches, symlinks and unsafe locations
cannot be selected. New-file fingerprints include device, inode, size and
high-resolution timestamps; a same-size replacement is still detected.

Apply checks the current folder and original saved links under a database write
transaction, validates the entire selected batch and preserves later annotation
edits. A stale candidate or database failure rolls back the batch. Tests cover
ten-minute expiry, single-use tokens, root changes, restart/demo invalidation,
non-ASCII token rejection and 501-track batching. Only selected `local_file`
fields change; ownership, preparation, key/BPM, tags, notes and cue notes remain.
Audio contents and Rekordbox records are not modified or moved.

Normal explicit file linking can fill empty key/BPM from XML or embedded tags.
Moved-file relinking deliberately preserves those fields instead of inferring
new values from a location match.

### Runtime and request boundaries

Host, Origin and CSRF guards remain active. The production server binds only to
numeric IPv4 loopback. The macOS reverse-DNS startup fix from v0.2.0 remains covered.
Runtime version and API version agree at **0.5.0 / 5**. Startup asset snapshots
prevent an old Python process from serving a new incompatible interface.
Spotify tokens remain in memory only. Source defaults contain no personal Client
ID or absolute library path. New network work does not run merely on startup.

## Browser interface verification

The production loopback server cannot be reached by the managed browser. Its
bind address and request guards remain intact. Browser checks use the exact web
assets with an isolated, fictional API fixture. This verifies interface behavior
separately from the real HTTP tests above; it is not a browser-to-production-
backend end-to-end test.

### v0.5 browser checks

- Browse controls receive fictional picker responses into the Settings draft.
  Cancelling and reopening restores all three saved paths. Save settings closes
  the dialog and the setup check uses those saved paths. Native chooser behavior
  is covered separately by mocked process tests, not by this browser fixture.
- Setup results show a changed-XML warning alongside readable folder and bundled
  reader checks. Browser review found a scrollable results area without keyboard
  access. It now has a named, focusable region and a visible inset focus outline;
  Tab reaches it and Page Down scrolls it.
- Inbox review initially selects nothing. It shows audio-tag/version evidence,
  distinguishes filename fallback and blocks an already-linked file. Confirming
  exactly two selected pairs updates their ownership, paths and downloaded state.
  Reopening their editors confirms preserved notes/tags, empty BPM/key filled
  for one track, and existing BPM/key retained for the other.
- A simulated SSD disconnect increments the watcher revision. Automatic polling
  updates the drive warning while an editor stays open with its unsaved tags
  and notes intact. Reconnection is also simulated; no actual SSD is involved.
- At 320, 390, 768 and 1280 px frame widths, the track view has no horizontal
  overflow and all row-action targets measure at least 44 by 44 px. Populated
  setup/inbox dialogs and Settings fit the 320 px frame without sideways
  scrolling; its Browse controls measure 44 px high. Frame content widths are
  smaller by the browser's scrollbar width. Zoom and text enlargement were not
  checked.
- **axe-core 4.14 reports zero violations and zero incomplete checks** for the
  checked dark/light track views, Settings, populated setup results and inbox.
  Rules cover WCAG 2 A/AA, 2.1 A/AA and 2.2 AA tags. This is scoped automated
  evidence, not a full WCAG audit or VoiceOver verification.

### Historical v0.4 checks

- Rows have four direct action buttons for editing, finding/previewing downloads,
  refreshing shop metadata and reviewing library copies. The extra chooser
  dialog has been removed. Browser checks exercised the direct editor, finder,
  refresh and library-review actions. Refresh starts a job without opening a
  dialog; library review focuses the matching-copy section in track details.
- Keyboard Tab reaches the icons and shows their labels. Escape dismisses the
  tooltip while keeping focus on its icon; Enter opens the finder directly.
  Saving or closing the editor returns focus to its originating row control.
  The finder retains the editor workflow so a selected link can be saved.
- At 320, 390, 768 and 1280 px frame widths, the document has no horizontal
  overflow and all row-action targets measure 44 × 44 px. The track editor also
  fits the 320 px frame. Desktop buttons form a compact grid; narrow rows use an
  action strip. Browser zoom and text-only enlargement were not checked.
- Choosing 10 rows and advancing shows rows 11–20. Searching then resets the
  page and count. The four page-size choices remain available.
- A manual MusicBrainz lookup displays a sourced Bandcamp candidate without
  invented price, preview, BPM or key fields. Selecting it keeps unsaved tags
  and notes; Save persists the combined details in the fixture. A track without
  an ISRC displays an explanation and disables the additional-link lookup.
- The moved-file preview initially selects nothing. Unavailable and size-mismatch
  candidates are disabled. Applying explicitly selected links preserves the
  fixture's ownership, notes and tags.
- **axe-core 4.14 reports zero automated violations** in the checked dark-theme
  and light-theme table views, moved-file dialog, finder and track editor. These
  runs also report no incomplete checks. Tests use the WCAG 2 A/AA, 2.1 A/AA and
  2.2 AA rule tags. Browser review found and corrected visible-label/accessibility-
  name mismatches and low-contrast light-theme navigation counts. These are
  specific checked views, not a claim of full WCAG conformance or screen-reader
  usability.

### Historical v0.3 regression evidence

The v0.3 fixture used 57 tracks and 33 XML entries. It verified no horizontal
layout overflow at 320, 390, 768 and 1280 px frame widths; pagination and filter
reset behavior; saved shop-link/genre selection retaining unsaved notes/tags;
Escape returning focus to the then-current row action; library pagination,
duplicate/unavailable filters and search; and lookup progress/cancellation.
These earlier checks establish regression context. They are not presented as
fresh v0.5 passes.

## Provider evidence and limits

During the v0.4 work, Apple's live GB catalogue returned metadata, and a preview
address was checked with an HTTP HEAD request. This establishes metadata and
address reachability only. It does not establish audible playback, recording
identity, current checkout price, purchase format or a completed purchase.
Promotional audio was not downloaded for these checks.

**The live MusicBrainz API was unavailable from the development environment.**
Response-handling and application-route tests use simulated responses. Official
API documentation and relationship definitions support the implemented request
shape; live operation still needs verification on a reachable connection. This
is a current environment limitation, not a claim that the service is down or
that the user's connection will fail.

Official guidance consulted:

- Apple Search API: https://performance-partners.apple.com/search-api
- Apple badge guidelines: https://marketing.services.apple/itunes-identity-guidelines
- Spotify display guidance: https://developer.spotify.com/documentation/design
- Spotify playlist items: https://developer.spotify.com/documentation/web-api/reference/get-playlists-items
- Spotify API changes: https://developer.spotify.com/blog/2024-11-27-changes-to-the-web-api
- MusicBrainz API: https://musicbrainz.org/doc/MusicBrainz_API
- MusicBrainz rate limits: https://musicbrainz.org/doc/MusicBrainz_API/Rate_Limiting
- MusicBrainz recording/version conventions: https://musicbrainz.org/doc/Style/Recording
- Recording purchase-link relationship: https://musicbrainz.org/relationship/92777657-504c-4acb-bd33-51a201bd57e1

Artwork uses Spotify's image host with attribution/linking and a coloured music
icon fallback. Preview audio remains streamed only after a user gesture, with
the required badge and same-record purchase link. See THIRD_PARTY_NOTICES.md.

## Continuous checks

The GitHub Actions workflow runs Python tests, the real HTTP smoke test, six
model tests and script syntax checks on Linux/Python 3.9 and macOS/Python 3.12.

The 195-test Python result above is local evidence. GitHub records the CI result
separately for each commit. Inspect the run matching the installed commit in the
[Actions view](https://github.com/wolney8/DJList/actions).

The initial v0.5 macOS run passed all 195 unit/regression tests, then exposed
an expected-path error in the HTTP smoke test: a macOS temporary-directory
alias was compared with DJList's canonical saved path. The assertion now
expects the resolved location and separately checks notes/tags. A local smoke
run through an aliased temporary directory exercises the same distinction;
application path validation was not weakened.

## Unverified and deferred

**Unverified:** native Mac picker interaction and permissions; the user's
actual SSD, XML exports and purchased-file tags; real automatic reconnect/XML
refresh and inbox/moved-file selections; v0.5 Spotify sync and artwork on their
Mac; audible preview playback; store navigation/checkout
prices; live MusicBrainz API retrieval; Safari, screen-reader output, browser
zoom and a full WCAG 2.2 AA audit. Browser fixtures and synthetic files do not
verify the user's recordings.

**Deferred:** direct store catalogues/prices/previews beyond iTunes; automatic
cross-store price comparison; Spotify genre enrichment; local audio analysis;
multiple Spotify sources; persistent sign-in; automatic purchase/download;
continuous nested music-folder monitoring; matching renamed/reorganized paths
beyond exact folder-prefix mapping; a version-specific read-only Rekordbox
database adapter and all Rekordbox/cue writes. CSV retains the existing
saved-track fields and does not include the separate lookup cache. See
[ROADMAP.md](ROADMAP.md) for provider and future database requirements.

## Next check on the Mac

1. Stop the old Terminal process with Control+C, launch the updated folder and
   confirm **0.5.0**. Use Settings → Browse for your music, exported XML and
   optional inbox, then **Save settings → Check setup**.
2. Reconnect Spotify and refresh. Check one known recording's inventory source,
   exact version and existing BPM/key. Export a fresh XML and confirm automatic
   refresh; reconnect the SSD and check availability without losing ownership.
3. Try a small inbox batch in its intended final location. Check each match
   before confirming links, then verify saved tags/notes and BPM/key. If folders
   moved, use **Find moved files** to review compatible links separately.

Existing records remain in DJList's separate data folder. These checks do not
import files into Rekordbox or write to its database.
