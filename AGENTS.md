# DJList operating rules

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
- No CueGen execution or Rekordbox database writes in this initial build.
- Enable future cue writes only after version compatibility, a library backup and
  explicit selection of a small new batch have been verified on the user's Mac.

## Data integrity

- Spotify sync must preserve user annotations and acquisition state.
- Match by stable Spotify identifiers; do not merge distinct recordings by title.
- Mark removal only after a complete successful sync.
- Keep demo and real collections separate.
- Treat file matches as suggestions until the user confirms them.
- Keep ownership separate from current file availability.

## Interface

- Follow Material 3 principles: consistent colour roles, typography, spacing,
  shape, elevation and visible component states.
- Target WCAG 2.2 AA. Use semantic controls, accessible names, keyboard support,
  visible focus, meaningful contrast and status announcements.
- Honour reduced motion. Avoid colour-only status and inaccessible icon controls.
- Use at least 44 px targets for primary interactive controls where practical.
- Label unverified store searches and planned features clearly.
- Separate catalog candidates from confirmed recordings. Never silently copy a
  candidate's metadata over the user's annotations or mark it owned.
- Stream authorized previews only after a user gesture, with the provider's
  required attribution and purchase link. Do not download or cache preview audio.

## Verification

- Test persistence, sync merge behaviour, failure recovery and local request guards.
- Test scan/link boundaries with temporary files, never the user's live library.
- Review the UI with keyboard navigation and at desktop/mobile widths.
- Separate locally simulated checks from actual Mac, SSD and Spotify verification.
- Exercise the startup asset snapshot and occupied-port path whenever update
  behavior changes. A running process must serve a consistent UI/API build.

## Collaboration

- Keep changes scoped to the assigned files while parallel work is active.
- Do not deploy to Vercel or another host for this local-only project.
- Keep credentials, user databases, logs, downloaded music and QA fixtures out of Git.
- Use the public wolney8/DJList repository as the canonical source. Preserve
  remote changes and use guarded ref updates; never force-push shared history.
