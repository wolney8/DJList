# DJList operating rules

Current implementation: **0.7.0 / API 7**. These rules cover the local library
workspace, reviewed reversible audio-file operations, checked database snapshots
and isolated CueGen-to-XML review described below.

## Product goal

Build a local Mac application with a browser interface for moving from a Spotify
wishlist to owned, located and prepared DJ tracks. Preserve the user's existing
Rekordbox library, annotations and audio files.

## Operating stance

- Optimise for a working, releasable product, not agreeable progress reports.
- Report verified, unverified, broken, deferred and remaining work accurately.
- Do not declare the product complete or ready merely because tests pass.
- Keep setup and ordinary use simple. Use plain language and avoid marketing copy.

## Local-first boundaries

- The production server binds only to the IPv4 loopback interface.
- Use read-only Spotify scopes and PKCE. Never request a client secret.
- Keep OAuth tokens out of logs, version control and exported files.
- Keep personal client IDs and absolute library paths in local settings, not
  repository defaults or examples.
- DJList state belongs in its own database, separate from Rekordbox.
- Default historical paths are configuration suggestions, not proof of current state.
- Missing external storage must never trigger a fallback to another Rekordbox database.
- Scanning and matching must not rename, move, delete or modify audio files.
- Read embedded audio metadata only within explicitly selected music or
  download folders. Keep bounded reads, file-identity checks and source labels;
  unknown BPM/key must remain unknown rather than being inferred as analysis.
- Permit raw `O_RDONLY` reads of the explicitly configured, closed `master.db`
  and supported companion file solely for cold database snapshots. Require the
  Mac process/locking checks, bounded reads, stable source identity and checked
  copies in a separate destination; default to DJList's own data folder.
- Never open a SQLite/SQLCipher connection to the live Rekordbox database,
  recover/checkpoint its journals or write to it. An independently configured
  CueGen 1.0.37 may open only an exclusive working copy of a freshly checked
  retained snapshot, inside the mandatory macOS sandbox. Deny network access,
  source database access and source audio/analysis writes. Keep runtime caches
  inside the work directory and never run an unsandboxed fallback. Check exact
  version, selected track, bounded output and completion before accepting cues.
  A future direct record adapter still needs a separate compatibility review.
- Database snapshots are not full library backups. Keep existing snapshots,
  report incomplete copies and never add deletion, overwrite or restore behavior
  to the current workflow. Daily checks run only while DJList is open and
  Rekordbox is closed; an unchanged source reuses a verified copy.
- Permit a separate XML download of explicitly reviewed cue suggestions while
  preserving the original track, cues, loops and grid. Do not import it into
  Rekordbox or modify source XML/audio. Enable future live cue writes only after
  version compatibility, a restorable full-library backup and an explicitly
  selected small batch have been verified on the user's Mac.

## Data integrity

- Spotify sync must preserve user annotations and acquisition state.
- Match by stable Spotify identifiers; do not merge distinct recordings by title.
- Mark removal only after a complete successful sync.
- Keep demo and real collections separate.
- Treat file matches as suggestions until the user confirms them.
- Keep ownership separate from current file availability.
- Keep automated shop suggestions separate from annotations. Preserve prior
  successful results on a failed refresh, and discard results for changed tracks.
- Retain imported XML records when their drive is offline. A refresh reads the
  selected export; it must not imply that DJList exported Rekordbox itself.
- Distinguish XML identity from filename guesses when ranking possible copies.
- Retain separate version/mix notes from audio tags when matching files. Read
  errors stay visible and must be retried on refresh, not cached indefinitely.
- Native browsing fills the Settings draft only. Check setup uses saved paths.
  Watch only configured local paths, retain the previous inventory on failure,
  and preserve editing drafts when automatic status changes arrive.
- Download-inbox linking requires explicit file/track selection and a fresh
  review token. Revalidate the whole batch and update atomically; never replace
  existing links or overwrite saved annotations. Serialize new/changed/cleared
  file links against settings and other library actions while keeping ordinary
  annotation edits available.
- Relinking moved files requires a fresh preview and explicit track selection.
  Change only those DJList file paths, preserving ownership and every other
  annotation. Recheck file/root identity and reject stale batches atomically.
  Never probe arbitrary old folders or imply that relinking updates Rekordbox.
- Library health must distinguish missing files from disconnected drives,
  outside-folder paths and unsafe paths. Keep missing XML inventory records.
  Replacement review may update only a confirmed missing DJList link after an
  explicit choice; recheck the old link, candidate metadata, file/root identity
  and settings. Preserve every other annotation and never relink offline paths
  as though their files were confirmed missing.
- Keep metadata duplicate suggestions separate from full-file checksum results.
  Read eligible files only inside the selected music root, enforce read/time
  limits, and invalidate cached hashes when file identity changes. Different
  bytes do not prove different audio; no deletion or merge follows a match.
- File changes require a separate fresh review and explicit selections, at most
  50 files. Permit indexed audio inside available selected roots only, with
  verified closed library-writing processes, fingerprint revalidation and
  native atomic no-overwrite moves. Never add a replacing-rename fallback.
  Removal moves to the same drive's `.djlist-removed` directory; no permanent
  deletion. Retain a durable journal, safe rollback and reviewed undo. Show
  incomplete recovery honestly. Keep source XML/master.db unchanged and explain
  that Rekordbox locations need separate relocation after a rename.
- Distinguish zero XML cues from unknown or stale cue evidence for every copy.
  Do not automatically select a duplicate for removal based on cue counts.
- Metadata corrections require reviewed before/after fields and a fresh token.
  Store corrections only in DJList; retain source metadata and support reset.
  Never change Spotify recording identity or embedded tags in this workflow.
- Stream local previews only by indexed ID and within selected folder boundaries.
  Reject unsafe links and special files, revalidate streaming sources, bound
  reads and support single byte ranges. Load waveforms after a user gesture,
  respect size/duration limits and show a native playback fallback where possible.
- Cue reviews require a saved, refreshed XML source and an available selected
  track whose identity still matches the audio fingerprint saved on XML import.
  Older or offline-at-import records require a new refresh with the drive present.
  Preserve all existing markers and fields, use unused hotcue slots,
  skip collisions and require explicit selection. Reject stale or expired
  reviews. A manually confirmed BPM/downbeat may supply a constant 4/4 grid only
  when none exists; never label calculated grid positions as audio analysis or
  detected musical phrases. Keep source/output paths separate and export names
  unique. Demo mode must not access real backup, repair or cue workflows.
- Keep MusicBrainz lookup optional and manual. Use the fixed ISRC endpoint,
  identify the application and respect pacing/cooldowns; do not poll for changes.
  Treat its Bandcamp/Beatport links as sourced suggestions, with unknown price,
  BPM and key. Preserve version notes and expose incomplete evidence; never
  cache an incomplete response as an authoritative no-match result.

## Interface

- Follow Material 3 principles: consistent colour roles, typography, spacing,
  shape, elevation and visible component states.
- Target WCAG 2.2 AA. Use semantic controls, accessible names, keyboard support,
  visible focus, meaningful contrast and status announcements.
- Honour reduced motion. Avoid colour-only status and inaccessible icon controls.
- Use at least 44 px targets for primary interactive controls where practical.
- Keep track rows within the viewport; use pagination and responsive cards
  instead of horizontal table scrolling. Keep actions reachable at small widths.
- Label unverified store searches and planned features clearly.
- Show running background work persistently, including its name and elapsed
  time. Show percentages only with real totals. Unchanged settings and Spotify
  connection changes must not wait for unrelated disk scans; folder changes
  and active Spotify refreshes keep their relevant serialization guards.
- Keep candidate prices, provider/check time and recorded prices distinct.
  Never imply that opening a shop pays for or downloads a track. Confirm an
  actual Incoming file match before inferring downloaded status.
- Separate catalog candidates from confirmed recordings. Never silently copy a
  candidate's metadata over the user's annotations or mark it owned.
- Stream authorized previews only after a user gesture, with the provider's
  required attribution and purchase link. Do not download or cache preview audio.

## Verification

- Test persistence, sync merge behaviour, failure recovery and local request guards.
- Test scan/link boundaries with temporary files, never the user's live library.
- Exercise backup and cue safeguards only with synthetic files or explicit
  test copies. Matching backup bytes is not proof of a successful full-library
  restore; generated XML is not proof of a correct import on the user's version.
- Review the UI with keyboard navigation and at desktop/mobile widths.
- Separate locally simulated checks from actual Mac, SSD and Spotify verification.
- Exercise the startup asset snapshot and occupied-port path whenever update
  behavior changes. A running process must serve a consistent UI/API build.

## Collaboration

- Keep changes scoped to the assigned files while parallel work is active.
- Do not deploy to Vercel or another host for this local-only project.
- Keep credentials, user databases, logs, downloaded music and QA fixtures out of Git.
- Evaluate licenses, dependencies and compatibility before incorporating an
  open-source DJ tool. Keep researched candidates distinct from bundled code;
  [docs/OPEN_SOURCE_DJ_TOOLS.md](docs/OPEN_SOURCE_DJ_TOOLS.md) records the current assessment.
- Use the public wolney8/DJList repository as the canonical source. Preserve
  remote changes and use guarded ref updates; never force-push shared history.
