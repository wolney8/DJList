# DJList 0.4.0 — verification record

31 October 2026. Development build; this record is not a claim of release
readiness or WCAG conformance. The user reports that v0.2.0 works on their Mac,
including Spotify connection. That report does not verify the v0.4.0 changes.

## Verified locally

- **126 Python unit/regression tests pass.** They use temporary records and
  simulated provider responses. Run `python3 -m unittest discover -s tests -v`.
- **Six JavaScript model tests pass.** These cover pagination, numeric BPM
  sorting, combined filters, sourced metadata/manual-field precedence and
  artwork URL/colour handling. Run `node tests/test_ui_model.js`.
- **The real HTTP smoke test passes.** A temporary loopback server exercises
  state, saved edits, media/XML refresh, library review, offline-copy warnings,
  background iTunes lookup and the manual MusicBrainz route with simulated
  provider responses. It also previews and applies moved-file mappings, rejects
  a reused/stale plan, and checks preservation of ownership set to false, notes,
  tags and confirmed purchase links. Startup asset consistency and occupied-port
  guidance remain covered. Run `python3 tools/smoke_test.py`. No real collection,
  Spotify account or user audio is used.
- JavaScript syntax, launcher syntax and `git diff --check` pass.

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

Normal explicit file linking can still fill unspecified key/BPM from XML.
Moved-file relinking deliberately preserves those fields instead of inferring
new values from a location match.

### Runtime and request boundaries

Host, Origin and CSRF guards remain active. The production server binds only to
numeric IPv4 loopback. The macOS reverse-DNS startup fix from v0.2.0 remains covered.
Runtime version and API version agree at **0.4.0 / 4**. Startup asset snapshots
prevent an old Python process from serving a new incompatible interface.
Spotify tokens remain in memory only. Source defaults contain no personal Client
ID or absolute library path. New network work does not run merely on startup.

## Browser interface verification

The production loopback server cannot be reached by the managed browser. Its
bind address and request guards remain intact. Browser checks use the exact web
assets with an isolated, fictional API fixture. This verifies interface behavior
separately from the real HTTP tests above; it is not a browser-to-production-
backend end-to-end test.

### Current v0.4 checks

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

### Earlier regression evidence

The v0.3 fixture used 57 tracks and 33 XML entries. It verified no horizontal
layout overflow at 320, 390, 768 and 1280 px frame widths; pagination and filter
reset behavior; saved shop-link/genre selection retaining unsaved notes/tags;
Escape returning focus to the then-current row action; library pagination,
duplicate/unavailable filters and search; and lookup progress/cancellation.
These earlier checks establish regression context. They are not presented as
fresh v0.4 passes for the changed action controls.

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

The 126-test Python result above is local evidence. GitHub records the CI result
separately for each commit. Inspect the run matching the installed commit in the
[Actions view](https://github.com/wolney8/DJList/actions).

## Unverified and deferred

**Unverified:** the user's actual SSD and XML inventory; v0.4 Spotify sync and
artwork on their Mac; real moved-file selections; audible preview playback;
store navigation/checkout prices; live MusicBrainz API retrieval; Safari;
screen-reader output, browser zoom and a full WCAG 2.2 AA audit. Browser QA uses fictional
API responses and does not verify the user's recordings.

**Deferred:** direct store catalogues/prices/previews beyond iTunes; automatic
cross-store price comparison; Spotify genre enrichment; local audio analysis;
multiple Spotify sources; persistent sign-in; automatic purchase/download;
matching renamed/reorganized paths beyond exact folder-prefix mapping;
Rekordbox database/cue writes. CSV retains the existing saved-track fields and
does not include the separate lookup cache. See [ROADMAP.md](ROADMAP.md) for the
provider and cue-write requirements.

## Next check on the Mac

Stop the old Terminal process with Control+C and launch the updated folder.
Confirm **0.4.0**, reconnect Spotify and refresh. Check one known existing track
against your actual media/XML inventory. If folders moved, save the current
music folder and use **Find moved files** to review the previous-folder mapping
before selecting links. Existing records remain in DJList's separate data folder.
