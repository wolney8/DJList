# DJList 0.3.0 — verification record

30 October 2026. Development build; this record is not a claim of release
readiness or WCAG conformance. The user reports that v0.2.0 works on their Mac,
including Spotify connection. The checks below concern the v0.3.0 changes.

## Verified locally

- **83 Python unit/regression tests pass.** They use temporary records and
  simulated provider responses. Run `python3 -m unittest discover -s tests -v`.
- **Six JavaScript model tests pass.** These cover pagination, numeric BPM
  sorting, combined filters, sourced metadata/manual-field precedence and
  artwork URL/colour handling. Run `node tests/test_ui_model.js`.
- **The real HTTP smoke test passes.** A temporary loopback server exercises
  state, saved edits, media/XML refresh, library review, an offline-copy warning,
  background lookup using a simulated provider, manual-field preservation,
  startup asset consistency and occupied-port guidance. Run
  `python3 tools/smoke_test.py`. No real collection or Spotify account is used.
- JavaScript syntax, launcher syntax and `git diff --check` pass.

### Store lookup and data preservation

Regression coverage checks successful-sync enqueueing, disabled automation,
failed sync, 24-hour freshness, forced refresh/cache bypass, queue deduplication,
new work arriving during a lookup, bounded retries, progress, cancellation and
restart recovery. In-flight results are discarded after cancellation or a
track identity change. A failed refresh retains a previous successful result.

Lookup results persist separately from annotations. Tests confirm that ownership,
confirmed purchase links, notes, tags and manually entered BPM/key are preserved.
Ambiguous versions and incomplete provider responses remain review candidates.
No invented BPM/key is supplied when the catalogue lacks it.

### Library inventory

Tests cover saved-path refresh, offline XML entries, invalid/unreadable XML
retaining the prior snapshot, successful replacement, migration of v0.2 XML
metadata, pagination, duplicate groups and artist/version/duration matching.
Legacy missing identity fields remain filename evidence with lower confidence.

Symlinks and paths outside the configured music root cannot be linked. Audio
files are not opened for analysis, changed, moved or deleted. Linking rechecks
current availability and can fill only unspecified key/BPM fields from XML.
Partial media scans retain previous entries and a persistent skipped-file warning;
a completely failed refresh retains the previous successful scan status.

### Runtime and request boundaries

Host, Origin and CSRF guards remain active. The production server binds only to
numeric IPv4 loopback. The macOS reverse-DNS startup fix from v0.2.0 remains covered.
Runtime version and API version agree at **0.3.0 / 3**. Startup asset snapshots
prevent an old Python process from serving a new incompatible interface.
Spotify tokens remain in memory only. Source defaults contain no personal Client
ID or absolute library path. New background work does not run merely on startup.

## Browser interface verification

The production loopback server cannot be reached by the managed browser. Its
bind address and request guards were kept intact. Browser checks used the exact
web assets with an isolated, fictional API fixture: 57 tracks and 33 XML entries.
This verifies interface behavior separately from the real HTTP tests above;
it is not a browser-to-production-backend end-to-end test.

Verified in that fixture:

- Desktop table fits its container; responsive layouts at 320, 390, 768 and
  1280 px frame widths have no horizontal document overflow. Small screens use
  labelled track cards and retain sort, filter and action controls.
- Changing to 10 rows and advancing shows rows 11–20; filtering resets the page
  and count. The 10/25/50/100 options are present.
- Row Actions opens edit, finder, refresh and existing-copy review controls.
- Choosing a shop candidate and its optional genre keeps unsaved notes/tags;
  explicit Save persists the combined details in the fixture. Reopening verifies
  those values. Escape dismisses the editor and returns focus to the row action.
- Library pagination shows entries 26–33 on page two. Duplicate and unavailable
  filters work with search. An unavailable candidate's link control is disabled.
  Closing the library dialog returns focus to Review library.
- Per-track and bulk refresh show progress; Stop lookup displays stopped status.
- Static checks find no duplicate IDs or missing literal element references.

Browser review exposed a mobile grid placement defect, which was fixed and
rechecked at all four widths. Review also corrected hidden partial-scan warnings
and overconfident legacy XML filename matches.

## Provider evidence and limits

Apple's live GB catalogue was checked for v0.2.0 earlier on 30 October 2026. It
returned full title/version, artist, GBP price, duration, broad genre, listing
and preview URLs. The v0.3.0 queue uses that catalogue adapter; this update's
automated checks simulate responses. Live audio identity, playback and checkout
were not verified by those metadata requests.

Official guidance consulted:

- Apple Search API: https://performance-partners.apple.com/search-api
- Apple badge guidelines: https://marketing.services.apple/itunes-identity-guidelines
- Spotify display guidance: https://developer.spotify.com/documentation/design
- Spotify playlist items: https://developer.spotify.com/documentation/web-api/reference/get-playlists-items
- Spotify API changes: https://developer.spotify.com/blog/2024-11-27-changes-to-the-web-api

Artwork uses Spotify's image host with attribution/linking and a coloured music
icon fallback. Preview audio remains streamed only after a user gesture, with
the required badge and same-record purchase link. See THIRD_PARTY_NOTICES.md.

## Continuous checks

GitHub Actions runs the 83-test Python suite, real HTTP smoke test, six model
tests and script syntax checks on Linux/Python 3.9 and macOS/Python 3.12. Inspect
the run for the installed commit in the
[Actions view](https://github.com/wolney8/DJList/actions).

## Unverified and deferred

**Unverified:** the user's actual SSD and XML inventory; v0.3.0 Spotify sync and
artwork on their Mac; real preview playback; store navigation/checkout prices;
Safari behavior; screen-reader output and a full WCAG 2.2 AA audit. Browser QA
used fictional API responses and did not play real audio.

**Deferred:** structured catalogues/prices/previews for other shops; automatic
cross-store price comparison; Spotify genre enrichment; local audio analysis;
multiple Spotify sources; persistent sign-in; automatic purchase/download;
automatic path relocation; Rekordbox database/cue writes. CSV exports retain
the existing saved-track fields and do not include the separate lookup cache.

## Next check on the Mac

Stop the old Terminal process with Control+C and launch the new folder. Confirm
the header shows **0.3.0**, reconnect Spotify and refresh. Save the music folder
and XML export path in Settings, then use **Refresh media**, **Refresh XML** and
**Review library** to check one known existing track before relying on matches.
