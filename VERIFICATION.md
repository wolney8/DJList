# DJList 0.7.0 — verification record

31 October 2026. Runtime/API **0.7.0 / 7**. These checks do not establish release
readiness, full WCAG conformance, backup restorability or compatibility with the
user’s Mac and installed Rekordbox version. No user music, Spotify account,
external SSD or live master.db was available in this environment.

The previous record is preserved in [VERIFICATION_V06.md](docs/VERIFICATION_V06.md).

## Automated checks

- **377 Python unit/regression tests pass** on the local Linux environment.
- **12 JavaScript helper cases pass**, across the track-model and library-workspace
  test files. They cover filtering/pagination, metadata precedence, artwork,
  price validation/currency ordering, waveform peaks, markers and unknown versus
  zero cue evidence.
- **Real loopback HTTP integration passes.** A temporary DJList process uses
  synthetic audio headers/XML/database bytes and simulated shop providers. It
  exercises settings, local preview bytes, metadata preview, checked backups,
  duplicate hashes, missing-file relinking, cue XML export, Incoming linking,
  real native file rename/removal/undo, file history, offline inventory,
  annotations, asset snapshots and occupied-port handling.
- JavaScript syntax, Mac launcher shell syntax and `git diff --check` pass.

Commands:

```sh
python3 -m unittest discover -s tests -v
python3 tools/smoke_test.py
node --test tests/test_ui_model.js tests/test_library_workspace_model.js
node --check web/app.js
node --check web/library-tools.js
node --check web/library-workspace.js
node --check web/local-player.js
node --check web/activity.js
node --check web/acquisition.js
bash -n Start_DJList.command
git diff --check
```

The HTTP test injects closed-process results only in its temporary test runner.
Its file moves, database-copy byte checks, persistence and request handling are
real. It does not execute CueGen or contact a store or Spotify.

## Reported Spotify/settings problem

A thread-held disk work lock reproduces the original condition. Regression
checks show that an unchanged complete settings form, or changed Spotify
connection settings, can save during a media scan. Folder changes remain blocked
and name the active task. Connection changes remain blocked during a Spotify
refresh. A numeric value cannot bypass boolean validation by comparing equal
to `true` or `false`.

Activity reports task name, phase, elapsed time and real counts when available.
Automatic backup, library watcher and shop lookup state are included. Failure
and completion release the busy state. The browser fixture shows the activity
both persistently and within Settings; a real Spotify authorization round trip
has not been repeated here.

## Library, audio and file boundaries

The new focused suites cover:

- Read-only audio streaming by indexed ID, full/HEAD/single-range responses,
  suffix/open-ended ranges, valid 416 headers, changed files/settings/directories,
  source byte preservation, and rejection of unsafe/outside/removed paths.
- XML cue/loop/grid counts and provenance for each duplicate member. Unknown
  cue information is distinct from a known zero count. Stale evidence stays
  visibly stale; it is never a live master.db query.
- Text-based metadata proposals, explicit selection, expiry, stale evidence,
  reset, preservation of source tags/Spotify/annotations, and atomic DJList-only
  corrections. File identity changes invalidate corrections.
- **31 file-operation tests:** real exclusive native rename, collisions,
  explicit number stripping, custom naming, rename/remove/undo, restart history,
  same-drive Removed storage, stale/offline/symlink/hardlink guards, active-process
  refusal, batch rollback and rollback failure, journal/link transaction failures,
  destination/undo races and rapid-operation history order.

Removal is recoverable storage, not permanent deletion or disk-space recovery.
No operation updates Rekordbox records or source XML. Interrupted/incomplete
operations expose recovery state and are not automatically resumed.

Native audio playback depends on the browser’s codecs and has a 512 MiB encoded
file limit. Waveform decoding is opt-in, limited to 32 MiB and a known duration
of no more than 15 minutes. Larger or unsupported files receive an explanation;
where the browser supports the format, native playback remains available.

## Browser checks

The browser used exact production frontend assets with **fictional API fixtures**
and a generated 193-second WAV. These checks verify browser behaviour and layout,
not real Spotify/store/CueGen/server integration.

Observed:

- Visible background scan and Settings explanation.
- Wishlist price/source/check time and purchase totals, filtering, Mark bought,
  and the transition to a filtered Incoming review.
- Dedicated library navigation, search and pagination; duplicate members with
  different cue evidence; explicit per-file selections; comparison result.
- Actual browser playback, decoded waveform, keyboard seek and live spectrum.
- Metadata before/after review and application; rename before/after review;
  file-history undo review and application, against the fictional API only.
- Visual existing/proposed cue markers, XML/CueGen option switching, selected
  suggestions and Listen here seeking. The UI reported XML export creation.
  Browser download-event capture timed out, so a saved browser XML file was not
  verified in this run. Actual XML response bytes are verified by the HTTP test.
- No horizontal overflow in the checked wishlist/library layouts at 320, 390,
  768 and desktop widths (scrollbars reduce the measured content width).
- Axe 4.14.0 WCAG A/AA checks reported **zero violations and zero incomplete
  checks** for the duplicate/player view in light and dark themes and the
  390-pixel fixture. The cue planner reported **zero violations**, with one
  contrast check requiring manual review for its unloaded-waveform overlay.
  This does not replace VoiceOver/Safari or a complete WCAG audit.

Fixed during browser review: stale pagination button state, a filename-form
JavaScript syntax error and cue-button accessible names that differed from their
visible labels. Local screenshots show fictional records only and are not
production library evidence.

## CueGen: implemented adapter, Mac verification outstanding

The adapter supports the inspected **v1.0.37** source/tag at
`eac34c8c1f007599261f45127095717e0f4abd01`. Its 21 focused tests use a fake
executable and an injected runner, plus real local process cleanup tests; they do
not demonstrate a successful CueGen run under macOS.

Checks cover a freshly verified retained backup; exclusive working copy and
read-back SHA verification; live database descriptors opened read-only; source
and settings revalidation; exact executable/track/path/version validation;
bounded output/time; malformed/wrong-track/error output; mandatory sandbox
availability; and process-group termination when a leader exits while a child
keeps stdout open. Existing 29 cue-planner tests continue to pass.

Generated proposals support hot/memory cues, names, colours and loops, while
preserving the original TRACK, existing cues/loops and TEMPO data. Source
Rekordbox writes, overwrite/remove, MyTag changes and track-colour changes are
not enabled. Active-loop state is not guaranteed by the XML export.

**Still unverified:** the real macOS sandbox profile, .NET runtime and installed
CueGen binary combination; actual phrase/Mixed In Key analysis; and import into
the user’s Rekordbox version. The analysis folder is read-only but is not copied
atomically with the database; its contents may change outside DJList. The plan
warns about that consistency limit.

## Remaining validation on the user’s Mac

The first GitHub CI run exposed a macOS temporary-path alias in the new audio
test and Python 3.9 being unavailable on the new `ubuntu-latest` image. The test
now selects its canonical temporary directory, retaining the production path
guard. The minimum-version Linux job is pinned to Ubuntu 22.04. Current CI
results are available in the repository’s Checks workflow; fixture tests still
do not verify the user’s SSD or a real CueGen launch.

1. Restart with v0.7.0. Check Activity while refreshing, then test Spotify settings
   and one local audio preview with the SSD connected and disconnected.
2. Use expendable audio copies to try one rename, Move to Removed and Undo.
   Confirm supported exclusive rename on the actual SSD filesystem and check
   the separate Rekordbox relocation/cleanup workflow.
3. Verify a database snapshot, configure the actual CueGen 1.0.37 executable and
   analysis folder, then test one reviewed cue export against a backed-up test
   collection. Keep full-library backups separate from database snapshots.

Live multi-store purchasing, guaranteed prices/BPM/key from every shop, embedded
metadata writing, perceptual duplicate detection, automatic Rekordbox relocation
and live database edits remain outside this build. The roadmap distinguishes
those from implemented features.
