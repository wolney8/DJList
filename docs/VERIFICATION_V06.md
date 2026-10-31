# DJList 0.6.0 — verification record

31 October 2026. Runtime/API **0.6.0 / 6**. This is a development build, not a
claim of release readiness, backup restorability or WCAG conformance. The user
reported that v0.2 works, including Spotify, and explicitly said they have not
yet tested v0.5. No current access to their Mac, SSD, master.db or Rekordbox
installation was available during this work.

The previous record is preserved in
[docs/VERIFICATION_V05.md](docs/VERIFICATION_V05.md). Historical checks are not
presented as new testing of the user's installation.

## Verified locally

- **293 Python unit/regression tests pass**, using temporary files, synthetic
  metadata/XML, injected process state and simulated providers.
- **Six JavaScript model tests pass**, covering track pagination, filters,
  numeric BPM sorting, metadata precedence and artwork handling.
- **The real HTTP smoke test passes.** A temporary loopback server exercises
  database-copy creation/verification, unchanged-source reuse, duplicate hashing,
  reviewed missing-file repair and selected cue XML export. It checks preserved
  cues/grid and unchanged source XML/audio/database bytes. Existing setup, tag
  reading, inbox, offline inventory, shop simulations, folder relocation,
  annotation preservation, startup assets and occupied-port behavior also pass.
  Only the backup process/platform check is injected; file operations,
  persistence and HTTP handling are real.
- JavaScript syntax, Mac launcher syntax and whitespace checks pass.

Run:

    python3 -m unittest discover -s tests -v
    python3 tools/smoke_test.py
    node --test tests/test_ui_model.js
    node --check web/app.js
    node --check web/library-tools.js
    node --check web/track-model.js
    bash -n Start_DJList.command
    git diff --check

The [Checks workflow](https://github.com/wolney8/DJList/actions/workflows/checks.yml)
runs the suite on Ubuntu/Python 3.9 and macOS/Python 3.12. Its commit-specific
result is the source for CI status; local passing results alone do not imply a
passing remote run.

## Database snapshots

**33 focused backup tests pass.** They verify read-only source descriptors,
unchanged source bytes, a real temporary SQLite writer blocked by the shared
read lock, and release of that lock after copying. Running or unknown library
processes, nonempty WAL/journals, missing storage, symlinks, hardlinks, changed
files and replaced directories prevent a verified snapshot.

Copies use new exclusive directories/files. Tests cover checksums, a protected
manifest, prior-copy retention, insufficient space, tampering, incomplete
copies, configuration races and failures persisting DJList's own records.
Failed verification cannot promote an incomplete copy to verified. A failed
first automatic attempt exposes its blocker; stale errors do not follow a
changed source/destination. No restore, checkpoint, source-write or deletion
path is provided.

Scheduler checks are approximately once a minute while DJList is open. A
successful check becomes due again after 24 hours; an unchanged source
reverifies its existing copy. Checks persist across restart. Demo, disabled
and unconfigured scheduling does not probe the source. A missing SSD never
selects an internal database automatically.

Bounds are 4 GiB for master.db, 32 MiB for optional masterPlaylists6.xml, and
180 seconds per copy/verification. Budgets are checked between OS reads and
cannot forcibly interrupt a stalled filesystem call. History lists the latest
50 records; all copies are retained.

**A verified checksum means copied bytes match.** It does not validate an
encrypted database's structure, prove a restore works, or protect against
damage already present in the source. Snapshots omit music and analysis files
and do not replace Rekordbox's native Backup Library. Physical Mac process
detection, SSD locking/permissions and restoration remain unverified.

## Library health, duplicates and missing files

Tests distinguish missing files from offline folders, unsafe paths and paths
outside configured roots. XML-only entries remain reportable. Metadata-based
duplicate groups retain version/duration distinctions; hardlinks count as one
physical file. No duplicate action deletes or merges anything.

Explicit content checks use SHA-256 and reject changed or unsafe files. They
are bounded to 20 files, 2 GiB and 20 seconds between reads. Cached hashes are
tied to complete file identity. Identical bytes establish identical files;
different bytes can still encode the same recording with different tags,
formats or compression. Acoustic matching is not implemented.

Health retrieval selects at most 50,001 unique paths in SQL before loading
metadata or checking availability, then reviews at most 50,000. The extra path
detects truncation. A fixture with 10,000 XML and over 12,000 file records
verifies that a two-path request stays below 5,000 SQLite VM instructions,
skips unselected malformed metadata and probes only selected locations.
Hash-cache retrieval is restricted to selected paths.

Missing-link repair requires an explicitly chosen candidate, a ten-minute
single-use review and a provably missing original path inside a selected
folder. It rechecks file/root identity, links, metadata and settings in the
transaction. Tests verify rollback on stale evidence and preservation of every
other annotation, including false ownership. Arbitrary old roots are not
probed. Rekordbox XML locations remain report-only; fix those in Rekordbox and
export fresh XML.

CSV reports use formula-safe cells. They include findings and saved/duplicate
locations; replacement candidates are reviewed in the UI.

## Cue and beatgrid review

**29 focused cue tests pass.** The planner preserves the original TRACK
element, metadata, cues, loops and tempo anchors, and exports only explicitly
selected suggestions in unused slots A–H. It supports 4/4 grids, including
tempo changes and beat numbers, and avoids positions within four grid bars of
existing cue/loop boundaries. Other meters are rejected.

When no XML grid exists, a user-confirmed BPM of 20–400 and first downbeat can
supply a constant 4/4 grid. This is arithmetic based on supplied values, not
audio analysis or phrase/drop detection. Invalid, nonfinite and extremely small
timing values cannot generate invalid positions.

Tests cover changed XML, root/file changes, malformed XML, expiry, token reuse,
demo isolation and changes during calculation/serialization. Planning/export
also require the audio identity recorded on XML import to match the current
file. Older records, unavailable-at-import audio and replaced files require
Refresh XML with the drive connected. Refreshing cannot prove that a grid fits
the recording or include unexported changes in Rekordbox.

No cue operation opens audio/master.db content or writes source XML/audio.
The HTTP check parses the returned one-track XML, verifies the original cue
and TEMPO are retained, and checks that only one selected cue was added.
Route tests check the XML MIME type, safe unique filename, no-store headers
and structured review errors. **Actual Rekordbox import/merging and retention
of cues/beatgrids remain unverified.**

## Browser checks

Chrome used an isolated fictional API fixture with the production HTML, CSS
and JavaScript. These UI checks are separate from the real HTTP smoke test.
No real library, backup, purchase or native chooser was used.

Observed workflows include backup history/manual creation/checking; settings
and mock Browse controls; duplicate filtering/content-result display; health
pagination/filter reset; explicit missing-file selection; cue planning,
unselected suggestions, and the checked-manual-grid branch. Repair returns to
refreshed health results with focus on the close control. Missing-grid input
receives keyboard focus. An offline fixture shows zero missing files and a
distinct offline count/warning.

Measured no horizontal page/dialog overflow in backup review at 320, 390, 768,
1024 and 1280 CSS-pixel frame widths; cue review at 320, 390 and 768; and health
review/repair at the 320 frame width. Classic scrollbars reduce content width
by 15 px. Desktop and light/dark views were inspected. These checks do not
establish Safari or VoiceOver behavior.

Axe-core 4.14.0 reported **zero violations in the inspected states**, including
backup, health, replacement, cue and settings panels. Some backup/replacement
and light-theme checks returned incomplete color-contrast checks for clipped
scrolling text. Those are not counted as passes. Full keyboard, zoom, screen
reader and WCAG 2.2 AA validation remains outstanding.

The browser rendered a cue export confirmation and a Blob-based Save XML file
link. Its download-capture operation timed out, and browser policy prohibited
opening the downloads manager. This session therefore **did not verify a saved
browser-download artifact**. The server XML response is verified separately;
file saving in the user's browser and Rekordbox import must still be tested.

## Open-source assessment

[docs/OPEN_SOURCE_DJ_TOOLS.md](docs/OPEN_SOURCE_DJ_TOOLS.md) records primary-source
research into CueGen, pyrekordbox, librosa, Chromaprint, Mixxx, aubio,
libkeyfinder and Essentia. They are candidates, not installed integrations.
This batch adds no analysis engine, third-party binary, model, SQLCipher
connection or CueGen execution. Previously bundled TinyTag remains the audio
metadata reader.

## Remaining real-world checks

1. Update the local app and confirm v0.6.0. Save the actual active master.db
   path, close Rekordbox and run Back up now. Check the destination/result;
   retain a full native Rekordbox library backup as well.
2. Refresh media and a fresh XML with the SSD connected. Review a small sample
   of health findings, duplicate comparisons and one replacement candidate.
   Confirm the exact recording and preserved annotations.
3. Review/export one cue plan. Confirm the XML saves, then test its import only
   after the full library backup. Check original/new cues, loops, beatgrid and
   file location on the installed Rekordbox version.

No assertion is made that the user's database has been backed up, restored or
modified by this session. Direct database queries/writes, acoustic duplicate
detection, audio-based beat/phrase analysis, automatic import and backup
operation while DJList is closed remain deferred.
