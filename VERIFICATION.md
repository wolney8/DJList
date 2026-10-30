# DJList 0.2.0 — verification record

30 October 2026. Development build; not a claim of release readiness or WCAG
conformance. The user reports that the previous build opens on macOS and that
Spotify sign-in and playlist connection work. Those reports do not verify every
path in this update.

## Verified locally

- **45 unit/regression tests pass.** They use temporary records and simulated
  provider responses; `python3 -m unittest discover -s tests -v` reproduces them.
- **The live HTTP smoke test passes:** a real temporary loopback server serves
  health/state, saves demo edits, serves the provider route, validates the
  catalogue route and handles duplicate launches. No real collection is used.
- The update failure is reproducible: a long-running process can otherwise
  serve new web files while its old Python routes remain loaded. The fix
  snapshots assets at startup. Tests replace files on disk and confirm the
  running process stays consistent; a new process sees the new build.
- Health/version fields and response headers identify the actual running build.
  Occupied-port handling recognizes current and legacy DJList servers and gives
  restart instructions without killing processes or opening the older UI.
- Existing source binding, annotation persistence, failure rollback, concurrent
  edits, missing-drive handling, path confinement and read-only XML/file scans
  retain their regression coverage.
- Catalogue tests cover query simplification, full-version comparison, strict
  artist matching, duration warnings, unknown prices, fixed provider origins,
  input validation, response limits, errors, local rate limiting and metadata
  caching. Audio is not fetched or cached by the Python client.
- HTTP Host, Origin and CSRF guards remain active. Preview CSP permits only the
  specific Apple audio host used by this adapter.
- Source defaults contain no personal Spotify Client ID or music-library path.
  Saved settings survive the generic-default change.
- JavaScript and launcher syntax checks pass. Static UI review covers labels,
  element references, text rendering and visible source/price/match warnings.

## Live provider verification

The finished client queried Apple's official GB catalogue on 30 October 2026.
It returned a candidate with the requested full title/version and primary artist,
a GBP track price, duration, broad genre, listing URL and preview URL. Alternative
versions were distinguished. This verifies metadata retrieval, not the audio
recording's identity, playback, checkout or cross-provider duration equality.

Official sources inspected:

- Apple Search API: https://performance-partners.apple.com/search-api
- Apple badge guidelines: https://marketing.services.apple/itunes-identity-guidelines
- Spotify API changes: https://developer.spotify.com/blog/2024-11-27-changes-to-the-web-api

The approved standalone iTunes Store badge was obtained from Apple's linked
package, visually checked and copied unchanged. See THIRD_PARTY_NOTICES.md.

## Review findings corrected

- Old server/new interface mismatch: startup snapshot, visible version and
  actionable restart messaging.
- Provider errors initially looked like empty search results: structured errors
  now show an incomplete/unavailable lookup. Missing timestamps no longer
  render as January 1970.
- Artist-name substrings could rank tribute acts as likely matches: comparison
  now requires exact normalized primary-artist identity, with a regression test.
- Personal setup defaults and historical local paths removed from public source
  and documentation while retaining local saved settings.
- A macOS test compared a temporary path alias with its canonical path. The
  test now captures the linked path before simulating a drive disconnection and
  verifies that the exact saved value, ownership and settings survive it.

## Continuous checks

GitHub Actions runs the unit/regression suite, real HTTP smoke test and syntax
checks on Linux and macOS. Inspect the run for the commit being installed in
the [Actions view](https://github.com/wolney8/DJList/actions); passing automation
does not verify browser interaction, preview playback or the user's actual SSD.

## Remaining unverified checks

- Actual preview playback and store navigation on the user's Mac.
- Keyboard/focus behavior, screen-reader output, responsive layout and zoom in
  the updated browser interface. The managed preview environment cannot reach
  the loopback-only app; no production network guard was weakened to bypass it.
- Current prices at checkout, purchase format and region-specific availability.
- The user's actual SSD scan, file-match quality and Rekordbox XML inventory.

## Deferred

Live catalogues/prices/previews for the other shops; automatic cross-store price
comparison; Spotify artist-genre enrichment; audio analysis; multiple sources;
persistent sign-in; automatic purchases/downloads; file moves; Rekordbox/cue writes.

## Next check on the Mac

Stop the previous Terminal process with Control+C and launch this version.
Confirm the header shows 0.2.0, then open a real track's Find downloads panel.
Check one candidate's price, click Preview and use its shop link. Confirm that
unsaved track details survive the search and that an explicitly saved purchase
link/selected genre survives a page reload.
