# Rekordbox relocation: implemented boundary and direct-write requirements

This work stays on DJList 0.7.0. It is not a new release and does not enable
live `master.db` writes.

## Implemented

A successful reviewed rename records an old-to-new path in the same DJList
transaction as its durable file-operation history. The queue follows further
renames and undo. Recoverable removal suspends the relocation target until the
file is restored; it never asks Rekordbox to use Removed storage.

Before a rename, refresh a collection XML while the drive is connected. DJList
captures the original unique `TrackID`, the audio identity and the complete
exported cue/grid attributes. It also records exported playlist membership.
Without this baseline, an older operation remains **Needs review**.

The supported handoff is:

1. In Rekordbox, use **File > Display All Missing Files > Relocate** on the
   existing record and select the new path from DJList.
2. Export the collection XML to the path saved in DJList.
3. Use **Check fresh Rekordbox XML**. DJList reads that file again itself.

**Verified in XML** requires the same unique `TrackID` at the final path,
unchanged audio-file identity, all previously exported cue/grid entries and
playlist memberships retained, and a new source-file fingerprint. A new track
import, duplicate TrackID/location, lost cue, changed grid, missing playlist
evidence, changed file, different XML source or disconnected drive does not
receive that label. A later XML refresh invalidates the previous result until
it is checked again.

This proves the contents of the exported snapshot only. It does not claim that
DJList queried or updated the live Rekordbox database. No checkbox can mark a
relocation as verified.

## Why an edited XML is not an automatic database update

Rekordbox documents **Relocate** and **Auto Relocate** in its own interface.
Importing an XML with a different audio location is not a verified way to
update an existing database record while preserving its internal identity.
DJList therefore does not generate an XML and call that an applied relocation.

The official manual route is documented at:

- <https://rekordbox.com/en/support/faq/v6/#faq-q600162>
- <https://cdn.rekordbox.com/files/20260409151936/rekordbox7.214_manual_EN.pdf>

No documented external relocation API was found in the reviewed official
material. Automating UI clicks would require access to the real Mac and a
tested workflow for that Rekordbox version; a headless test cannot establish it.

## What a direct adapter must update and verify

The reviewed pyrekordbox source implements `update_content_path` by changing
`FolderPath`, conditional `OrgFolderPath`, `FileNameL`, and the PPTH path in all
corresponding analysis files. Newer analysis data can use a `?/filename` form.
Committing also participates in the library's registry/USN bookkeeping. A
single SQL update to the path would miss part of this behavior.

- Source: <https://pyrekordbox.readthedocs.io/en/latest/_modules/pyrekordbox/masterdb/database.html#MasterDatabase.update_content_path>
- Runtime requirements: <https://github.com/dylanljones/pyrekordbox/blob/master/pyproject.toml>

At the time of this review, upstream master requires Python 3.11 or newer and
SQLCipher support; DJList's minimum is Python 3.9. A direct adapter therefore
also needs a pinned, separately managed compatible runtime. It is not installed
or invoked by this queue.

Before enabling an automatic writer, the implementation needs:

- An identified Rekordbox version and supported schema, checked on a copy of
  the user's actual database with the correct external analysis root.
- A full, restorable Rekordbox backup and byte-verified snapshots of every
  affected database, playlist companion and analysis file. Current cold DB
  snapshots intentionally omit the analysis tree and music.
- An exact reviewed set of stable record IDs and old/new file identities.
- A copy-only dry run that compares every changed database row and analysis
  tag, including registry/USN effects, against an allowed change set.
- A durable multi-file commit/recovery journal, exclusive closed-tool checks,
  no fallback database and recovery after interruption or drive removal.
- A small real-Mac relocation/reopen/play/restore check showing that cues,
  beatgrids, playlists and any cloud-library state remain intact.

Synthetic SQL/XML fixtures can test bookkeeping and failure paths. They cannot
prove compatibility with an unseen encrypted schema or demonstrate successful
restoration in the user's Rekordbox installation. Direct writes remain disabled
until that compatibility work is done; the queue is not a substitute for it.
