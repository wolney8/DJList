# Check DJList on your Mac

The current released application is still **0.7.0**. Internal CueGen setup and
the Rekordbox locations view are maintenance work on a separate branch. Do not
treat that branch as a verified installation or a new release. Use the checks
below to establish what works on your actual Mac, browser and SSD.

## 1. Check protection and your saved locations

Connect the music SSD. In DJList Settings, check the music folder, Incoming
folder, active external `master.db` and saved Rekordbox XML path. Choose **Save
settings**, then **Check setup** above the wishlist. The active database must
be the one Rekordbox actually uses; a similarly named internal copy is not a
replacement when the SSD is unavailable.

Close Rekordbox and other library editors. Open **Rekordbox backups → Back up
now**. Pass: the correct database path appears and the saved copy says
**File contents checked**. Use **Check backup** on that copy once. This proves
its saved bytes match; it does not prove a full library restore. Keep Rekordbox's own full library backup
before importing cues or reorganising music.

## 2. Try ordinary features with a few test copies

Use copies of two or three tracks inside a clearly named test folder under your
selected music folder. Keep the originals in place. Refresh media and XML.
An unimported copy may correctly show **Cues unknown**.

| Try | Pass looks like |
| --- | --- |
| Start a media refresh, then open Settings | Activity names the scan. Spotify connection settings remain usable during unrelated disk work. |
| Preview a copied track and load its waveform | Sound plays; scrubbing changes the position; the spectrum responds to playback. |
| Inspect a suspected duplicate pair | Both locations are visible; XML cue evidence matches what you exported. Unknown is not reported as zero. |
| Review one filename cleanup, apply it, then undo | The exact filename changes and is restored; DJList records both operations. |
| Move one test copy to Removed, then undo | The copy returns to its original location; the untouched original remains present. Removed storage does not free disk space. |
| Review one metadata suggestion, apply it, then reset | DJList displays the corrected fields, then the source values again. Audio tags are unchanged. |
| Mark a wishlist track bought and check Incoming | Ownership persists; downloaded status follows a confirmed real matching file, not merely a shop click. |

For the SSD check, stop playback and wait for Activity to become idle. Close
Rekordbox, safely eject the drive in Finder, then open **Missing & offline**
and choose **File status → Drive unavailable**. Use **Refresh this view** if
that view was already open.
Pass: the drive is unavailable/offline, ownership and saved XML remain, and
reconnecting restores availability. Do not unplug a drive during a write.

## 3. Check cue generation and relocation after their maintenance work is verified

For the internal cue engine, **Settings → Check cue engine** must report a
passed protected startup check. There is no separate CueGen launch or command.
An installed component with a failed startup check is not a passing result.

Use one analysed test copy already imported into Rekordbox, with phrase
analysis, a checked beatgrid, at least one free hot-cue slot and a fresh XML
export. Close Rekordbox. In **Plan cues**, use phrases, eight hot cues and
four-bar spacing. Listen around a few suggestions, export only those selected,
then import only that test track in Rekordbox. Pass: it plays from the expected
file, the selected new cues are sensible, and its earlier cues/grid remain.
Existing full cue slots or missing phrase analysis may correctly produce no
new suggestions.

For relocation, first refresh an XML containing the test track's original
record. Close Rekordbox and rename that test copy in DJList. In Rekordbox use
**File → Display All Missing Files → Relocate** for the existing entry, using
the new path from **My library → Rekordbox locations**. Export fresh XML and
choose **Check fresh Rekordbox XML**. Pass: the same track ID, new path and
previous cue/grid/playlist evidence are confirmed. Also check playback.

This tests a manual Rekordbox relocation plus DJList verification. **Automatic
live database updates are not implemented.** Do not delete and re-import the
track as a substitute: that does not establish preservation of the original
record.

## What to report

Send the failing control's exact message or a screenshot, plus your Rekordbox
version from its About window and whether your Mac is Intel or Apple Silicon.
For a backup problem, show the backup status and selected paths. Do not send
Spotify secrets, your full music collection or a live database as a first step.
