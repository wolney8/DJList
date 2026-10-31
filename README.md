# DJList

**A local Spotify wishlist and DJ music tracker.** Find purchase candidates,
record ownership, review missing files and duplicates, keep database snapshots
and prepare selected cue markers for a separate Rekordbox XML export.

Version **0.6.0, API 6 — 31 October 2026**. Development build; see [VERIFICATION.md](VERIFICATION.md) for
the checks performed and [ROADMAP.md](ROADMAP.md) for outstanding work and access
requirements.

## Open it on a Mac

1. Download this repository with **Code → Download ZIP** and unzip it.
2. Double-click **Start_DJList.command**.
3. Choose **Explore the demo**, or open Settings to connect your Spotify app.

Keep the Terminal window running while using DJList. Press **Control+C** there
to stop it. Python 3.9 or later is required; no additional Python packages need
to be installed. The audio-tag reader is included with DJList. The launcher
checks common Python locations. Node is only needed for developer checks.

This launcher is unsigned and unnotarised. macOS may require **System Settings →
Privacy & Security → Open Anyway** for this specific file. Follow
[Apple's guidance](https://support.apple.com/en-gb/102445). Do not disable system
security settings. You can also run `python3 app.py` from this folder.

## Update an installed copy

Stop DJList with **Control+C**, then open the launcher from the updated folder.
The header shows the running version. Existing records and settings remain in
the same data folder. Reconnect Spotify after restarting.

If another copy is using the port, the launcher reports the running version
when available and gives restart instructions. It does not kill another
process or silently switch ports. Since 0.2.0, the server keeps a fixed snapshot
of its interface files, so replacing files cannot mix a new UI with an old API.
A frontend/API mismatch also shows a restart warning.

## Connect Spotify

Create a personal application in the Spotify developer dashboard and register:

```text
http://127.0.0.1:8888/callback
```

In DJList Settings, enter your **Client ID** and playlist link, then choose
**Save & connect Spotify**. No client secret is needed. Return to DJList and
choose **Refresh Spotify**. Enter `liked` instead of a playlist link for Liked
Songs. Account eligibility, Premium and allowed-user requirements depend on
Spotify's development-mode rules.

One source is tracked per collection. It stays fixed after the first sync to
prevent accidentally replacing your records. Repeated sync preserves ticks,
tags and notes. Removed Spotify tracks remain recorded.

Tokens remain in memory for the current run. They are not saved to disk,
exported, logged or committed to Git.

## Automatic shop lookup

After a successful **Refresh Spotify**, DJList queues UK iTunes Store lookups
for new or changed tracks, results at least 24 hours old, and failed or unfinished
lookups. This is enabled by default. Turn **Find shop links after Spotify
refresh** off in Settings to stop automatic lookup and cancel pending work.

The queue starts at most one track lookup every eight seconds. Progress appears
above the table, and **Stop lookup** cancels the queue; a request already in progress
may need to return first. You can keep editing your tracks while it runs.
Lookups run only while DJList is open. Saved results survive a restart;
unfinished work needs another refresh.

A single likely match can show a suggested shop link, listed price and labelled
iTunes genre in the table. Ambiguous results ask you to review the candidates.
These suggestions stay separate from your ownership ticks, confirmed purchase
links, tags, key, BPM and notes. They do not overwrite your saved details.

Use **Refresh shop links** to recheck the current Spotify list, or a track's
refresh icon to recheck just that song. The UK iTunes Store is the only live
catalogue returning prices and previews inside DJList in this version.

## Work with your track list

Choose **10, 25, 50 or 100 rows** per page. Search tracks, artists or tags;
filter by shop status and possible existing copies, linked files or unavailable
linked files. Sort by artist, title, BPM, key or shop status. The existing
**To buy**, **Owned** and **Files linked** views remain available.

Each row has four direct **Actions** icons: **edit**, **find downloads and
preview**, **refresh shop link and metadata**, and **review existing library
copies**. Hover or keyboard-focus an icon to see its label. There is no separate
action-selection dialog; the editor and download panels still open when needed.
Spotify album artwork appears when available; a consistent coloured music-note
icon is used when artwork is unavailable.

## Find and preview a purchase candidate

Open a track and choose **Find downloads**. DJList shows saved candidates first,
or searches the supported catalogue using that track's details. Choose **Search**
to try another query. The toolbar also accepts a manual artist/title search
without Spotify.

### Live catalogue: UK iTunes Store

- Cards show the store title, artist, duration, genre and lookup time.
- Track prices show the returned currency. Unknown or album-only prices are
  not invented. Check current price and format on the vendor page.
- Match reasons highlight artist, title, duration and version differences.
  Every result remains a candidate until you check the recording.
- **Preview** streams the permitted sample after a click, one at a time.
- Previews require the approved iTunes badge beside the same-record purchase
  link and the attribution **provided courtesy of iTunes**.
- Preview audio is not downloaded, cached, saved or used for DJ playback.

**Use this shop link** fills the purchase URL in the existing track draft.
Optionally select the store genre as a tag. Existing tags remain, and **Save
changes** is still required. No search result marks a track owned or buys it.

These are store offers, not guaranteed checkout quotes or a cross-store price
comparison. Verify the exact mix, region and download format before buying.

### More shop links: MusicBrainz

For a selected track, choose **Find more shop links** in the download panel.
DJList uses its ISRC recording code to ask MusicBrainz for community-listed
direct **Bandcamp** and **Beatport** purchase links. No extra account or API key
is needed for this lookup. A missing ISRC or absent community link can leave
the result empty even when a shop sells the track.

Results show their MusicBrainz source, title, artist, duration where known and
recording/version notes. Conflicting versions, additional recording notes and
incomplete evidence require review. Malformed or unavailable metadata is not
reported as a successful search with no matches. Choose a link only after
checking the recording, then **Save changes** to keep it in track details.

This is a manual metadata lookup, separate from automatic iTunes searches.
It does not fetch the shop page, verify current availability, or supply shop
prices, previews, BPM or key. Successful complete responses are cached in memory
for this run, for up to seven days; explicit refresh respects provider pacing
and cooldowns. See the [MusicBrainz API](https://musicbrainz.org/doc/MusicBrainz_API).

**Verification limit:** the live MusicBrainz API was unavailable from the
development environment during this update. Automated response-handling tests
are separate from live provider verification; see [VERIFICATION.md](VERIFICATION.md).

### External shop searches

Bandcamp and Beatport open their own search pages. Traxsource uses a labelled
Google site search. An all-three Google link is also available. These links
remain useful when MusicBrainz has no direct link. They do not supply live
inventory, prices or embedded previews inside DJList.

## Tags, BPM and key

Spotify provides supported metadata such as title, artists, album and duration,
plus ISRC where returned. New/development app restrictions prevent using its
Audio Features and Audio Analysis endpoints for automatic BPM/key here.

Artist genres can be broad or empty; they are not track-specific DJ mood or
energy tags. Automatic Spotify artist-genre enrichment is deferred. A likely
iTunes match can show a labelled genre suggestion; adding it to your own tags
still requires your selection and **Save changes**. The iTunes catalogue used
here does not supply BPM or musical key.

Import a **Rekordbox XML inventory** to use existing analyzed key/BPM values,
or read values already embedded in your purchased audio files. Confirming a
file link can fill empty key/BPM fields from those sources; it preserves values
you have already saved. You can also enter them manually. DJList does not
analyze audio or claim that a key came from Spotify.

## Set up and check your folders

1. In **Settings**, use **Browse** beside **Music folder** and **Rekordbox XML
   file**. On a Mac this opens its native folder or file chooser. You can still
   paste a full path. Export an XML from Rekordbox first if you do not have one.
2. Optionally choose a **Download inbox folder** for purchased audio. It may
   be inside your music folder or a separate folder, including on an SSD.
   Choose **Save settings** to keep your choices; browsing only fills the draft.
3. Choose **Check setup** to review the saved paths, current XML freshness,
   inventory counts and automatic-refresh status. Unsaved Settings drafts are
   not checked. A readable XML file still needs to be imported with
   **Refresh XML** before it supplies your library inventory.

Export future Rekordbox XML snapshots to the same selected file. **Check setup**
can identify a changed or older export; it cannot establish whether that export
contains every change currently in Rekordbox. The real Mac chooser, filesystem
permissions and SSD connection still need checking on your computer.

## Automatic library refresh

**Refresh library automatically** is on by default. While DJList is running,
it checks the selected music folder's availability and the selected XML file
every **five seconds**. A changed XML must remain unchanged for at least five
seconds before DJList attempts to import it. A media scan runs at startup,
after relevant saved-setting changes and when the selected music drive
reconnects. Work waits if another library operation is in progress.

This does not export XML from Rekordbox. Export a fresh snapshot when its
collection changes. It also does not continuously scan every nested music
folder for new files: use **Refresh media** after adding or changing files on
an already connected drive. Use **Scan inbox** to review newly downloaded
audio. Automatic refresh preserves open editing drafts and existing annotations.
When the drive or XML is unavailable, DJList retains its previous inventory.
Turn the setting off to use the manual refresh controls alone.

## Refresh and review your existing library

- **Refresh media** reads filenames, file information and supported embedded
  audio tags in your configured music folder. Unchanged tag results are cached.
  It reports partial scans and keeps previous inventory entries when some files
  or folders cannot be read.
- **Refresh XML** rereads the **Rekordbox XML file** path saved in Settings.
  Export a fresh XML from Rekordbox to that same file when its collection changes.
  This button does not read the live Rekordbox database or create a new export.
  **Import XML** also lets you choose an export without saving its path.
- **Review library** lists the saved inventory, including unavailable files,
  and lets you search or filter possible duplicates before buying another copy.

The XML snapshot stays in DJList across restarts and while an SSD is disconnected.
A missing or invalid export leaves the previous snapshot intact. If an otherwise
usable export contains skipped entries, DJList shows a warning to review it.
Existing XML metadata from a v0.2 collection is retained on upgrade.

Duplicate suggestions compare artist, full title/version and duration where
available. The source is labelled as **Rekordbox XML**, **Audio file tags** or
**Filename**. Filename matches and separate version notes require particular
care; they are suggestions, not proof of identical audio. Nothing is
automatically linked, deleted or marked owned by a scan or XML refresh. Choose
**Select this file** and **Save changes** to confirm a link; this records
ownership and can fill empty key/BPM fields from XML or embedded tags. You can
also tick ownership manually.

### Embedded audio metadata

DJList includes the MIT-licensed **TinyTag 2.3.2** reader. It reads supported
title, artist, album, genre, duration, ISRC, version notes and existing BPM/key
tags. It does not calculate BPM or key, extract artwork or alter the audio.
Absent fields stay unknown. Existing Rekordbox XML metadata takes precedence
over audio tags for the same library file; a filename remains the fallback
when useful tags cannot be read.

Metadata reads are local and bounded. Symlinks and paths outside the selected
folder are rejected. A file that changes during a read is not accepted as a
stable metadata result. Cached results are tied to the selected folder, file
identity and reader version, and are refreshed when the file changes. An
unreadable or unsupported tag does not remove the file from the filename
inventory. Offline inventory keeps its earlier metadata with an availability
warning.

Synthetic-file tests cover **MP3, FLAC, WAV, AIFF, M4A and Ogg/Opus** metadata.
They do not establish compatibility with every encoding or tag layout in those
formats. Raw **AAC** currently uses filename fallback. Real purchased files
and external-drive reads remain to be checked on the user's Mac. See
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for the bundled reader's licence
and provenance.

## Check missing files and duplicates

After **Refresh media** and **Refresh XML**, open **Library health**. Search,
filter and page through missing files, disconnected drives, possible duplicates
and paths needing review. **Export report** saves the filtered findings as CSV.
A disconnected drive is reported separately from a file missing on an available
drive; existing ownership and library records are kept.

For a missing file already linked to a DJList track, choose **Review replacement
file**, check the proposed recording and choose **Confirm file link**. Candidates
come from the indexed music folder and can include renamed or reorganized files
when their metadata still matches. Nothing is selected automatically. The review
expires after ten minutes; files, folders, metadata and the previous link are
checked again before the selected DJList address changes. Ownership, notes,
tags, BPM/key and preparation state are preserved.

This replacement review requires the old file to be confirmed missing inside an
available selected folder. Reconnect an offline drive first. Use **Find moved
files** for an old folder or renamed SSD, as described below. An XML-only entry
without a saved DJList track link can be reviewed but is not relinked by this
control. Update its location in Rekordbox separately and export fresh XML.

For possible duplicates, **Check file contents** reads the complete files in
that group and compares SHA-256 checksums. A matching result confirms identical
file bytes. Different bytes can mean different tags, formats or recordings;
they do not establish that the songs sound different. Comparisons are bounded,
only cover eligible files in the configured music folder and become stale when
files change. This is not an acoustic duplicate search across every file. DJList
never deletes, merges or moves a duplicate.

## Review purchased files in the download inbox

Choose **Download inbox → Scan inbox** after saving its folder in Settings.
DJList reads that folder and suggests matches to your Spotify tracks using
embedded tags where available, with filename fallback. Review the exact mix,
artist and version notes, choose the correct track for each file and tick the
matches you have checked. Nothing is selected by default. Then choose
**Confirm selected links**.

Confirmation links each chosen file **where it already is** and marks the
track owned. A track in **Wishlist** or **Bought** moves to **Downloaded**;
later preparation states are preserved. Empty BPM/key fields can be filled
from the file's existing tags. Saved BPM/key, tags and notes are preserved;
genre is not automatically added to your personal tags. The inbox does not
replace an existing file link, move downloads into your music folder, check
receipts or import anything into Rekordbox.

Each review holds up to **500 files** and expires after **10 minutes**. Files,
folders and saved links are checked again at confirmation; a changed selection
requires a fresh scan instead of applying part of the batch. Use a focused
inbox folder and check any limit or skipped-file warning. If you plan to move
purchased files onto your SSD, put them in their intended location before
confirming their links. Moving them later requires reviewing the saved paths
again.

## Find files after moving a folder or renaming an SSD

Save the **current music folder** in Settings, then choose **Find moved files**.
Check the **Previous music folder** path and choose **Preview locations**.
Select the proposed links you have checked, then choose **Update selected links**.
Nothing is selected by default.

This works when the folder layout and filenames beneath the old and current
folders are unchanged. DJList maps those relative paths and compares previously
indexed file sizes when available. It does not compare audio content. Missing
files, size mismatches and symlinks cannot be selected. For individually renamed
or reorganized files inside an available selected folder, use **Library health
→ Review replacement file** to inspect metadata-based candidates. If no suitable
candidate is available, select the file manually in track details.

The preview expires after **10 minutes**. If files, folders or saved links change,
make a fresh preview. Each preview holds up to 500 proposed links; after updating
a batch, preview again for any remaining links. Already usable links are skipped.

Only the selected file addresses in DJList change. Ownership, preparation state,
tags, BPM/key and notes are preserved. Audio files are not moved and Rekordbox is
not edited. Update Rekordbox separately and export fresh XML when its file
locations change, then use **Refresh media** and **Refresh XML** in DJList.

## Music and local records

Set your actual music folder in Settings; external SSDs are supported. New
installations suggest `~/Music/DJ Music`. Existing settings are preserved. The
optional Rekordbox database path selects the actual `master.db` to copy for
backup while Rekordbox is closed. It is not used to query or edit the collection.

Scanning reads filenames, file information and supported embedded metadata.
Suggestions need confirmation. It never moves, renames, deletes or edits audio.
Ownership is separate from file availability, so a disconnected drive does not
clear ticks or saved paths.

If you move folders or rename the SSD, update the music folder in Settings and
use **Find moved files** to review compatible saved links. Export a fresh
Rekordbox XML after updating its locations. Folder changes alone do not silently
rewrite saved links.

On macOS, the SQLite database lives in:

```text
~/Library/Application Support/DJList/
```

Include that folder in your backups. Updating the application folder does not
move those records. CSV export is a readable record copy, not a full database
backup. Demo records stay separate from the real collection.

## Keep Rekordbox database snapshots

1. In **Settings**, choose the **active Rekordbox master.db** that your current
   library uses. Leave **Backup folder** blank to use DJList's own data folder,
   or choose a separate destination. Save settings.
2. Close Rekordbox, CueGen and other library-writing tools. Open **Rekordbox
   backups** and choose **Back up now**. Check the result before relying on it.
3. Keep **Back up the database daily** enabled and leave DJList running when you
   want automatic checks. Use **Check backup** in backup history to compare
   an existing snapshot with its recorded checksums again.

The default destination on macOS is:

```text
~/Library/Application Support/DJList/rekordbox-backups/
```

Automatic backups are enabled by default after an active database path is saved.
They are due initially and then every 24 hours after a successful source check, while
DJList remains open and Rekordbox is closed. An unchanged source rechecks the
previous snapshot instead of making another copy. A busy, disconnected or
unverifiable source waits; DJList never falls back to another database. Automatic
backups pause in demo mode and stop when DJList exits.

Each new snapshot gets its own folder, copied `master.db`, optional companion
`masterPlaylists6.xml`, and a manifest of checksums. Existing snapshots are never
automatically deleted or overwritten, and there is no restore operation in the
app. Failed or interrupted copies remain visibly incomplete. Keep the backup
destination separate from the database folder; a destination on the same drive
cannot protect against losing that drive.

**These are database snapshots, not full Rekordbox library backups.** They do
not include analysis files or your music. Use Rekordbox's **File → Library →
Backup Library** as well, particularly before importing cues or changing a
collection. Matching file bytes verifies the copy, not database health or a
successful restoration. Actual Mac/SSD backup and restore behavior remains
unverified; see [VERIFICATION.md](VERIFICATION.md).

### How database access is limited

The backup component opens the explicitly selected source as a raw read-only
file (`O_RDONLY`), with file locks, bounded copying and identity checks. It does
not open a SQLite/SQLCipher connection, query the collection, checkpoint a
journal, recover a database, or write to `master.db`. Active write journals or
uncertain process/locking state pause the operation. XML remains the source of
Rekordbox collection metadata, including existing analyzed BPM/key values.

A read-only database adapter is feasible future work. It must support the
specific Rekordbox version, its SQLCipher encryption and schema, and a
consistent view of a database that Rekordbox may be changing. The unofficial
[pyrekordbox documentation](https://pyrekordbox.readthedocs.io/en/stable/quickstart.html)
describes encrypted database access; SQLite documents the
[live WAL and read-only requirements](https://www.sqlite.org/wal.html).
Pointing a normal SQLite reader at the live file is not enough. A disconnected
SSD must not silently select a different database.

Writing tracks, tags or cues back needs a separate, controlled design. It is
deferred until version compatibility, a restorable library backup, a closed
Rekordbox session and an explicitly reviewed small batch can be checked on the
user's Mac. No direct writes or CueGen execution are enabled in this build.

## Review and export cue markers

Export a fresh XML from Rekordbox to the saved file, then use **Refresh XML**.
In **Review library**, choose **Plan cue markers** beside one available track.
Choose the spacing and maximum number of new hotcues, then **Create suggestions**.
Check the recording and timing, select only the suggestions you want and choose
**Export selected cues**. A **Save XML file** link remains available if the
browser does not start the download automatically.

DJList calculates positions from the track's existing 4/4 XML beatgrid, including
tempo changes. It preserves the complete original track metadata, tempo markers,
cues and loops, and adds only selected markers in unused hotcue slots A–H.
Positions within four grid bars of an existing cue or loop boundary are skipped.
Nothing is preselected. The review expires after ten minutes, and changed source
files or folder settings require a fresh plan.

Refresh XML with the music drive connected before your first cue review after
updating DJList. The planner compares the audio-file identity recorded at XML
refresh with the current file; a changed file or older record needs another
refresh. This check cannot prove that the XML grid fits the recording, so listen
and check the timing before importing.

If that XML track has no grid, enter a BPM between **20 and 400** and its first
downbeat in seconds, then confirm you checked those values. The resulting grid
is constant 4/4. DJList does not analyze audio to find beats, phrases, intros,
drops or outros, and it does not replace an existing grid with entered values.
A file must be in the saved, refreshed XML so existing cues can be preserved.

The download is a separate, uniquely named one-track XML file. DJList does not
apply it to your collection. Before manually importing it, make a full Rekordbox
library backup and try one reviewed track on your actual Rekordbox version.
Check that its original cues, loops and beatgrid remain correct. Import behavior
and compatibility on your Mac remain unverified.

The [open-source tools assessment](docs/OPEN_SOURCE_DJ_TOOLS.md) explains the
roles of CueGen, pyrekordbox, librosa and other possible additions. They have
been researched, not installed or run by this build.

## Privacy and operation

The server listens only on `127.0.0.1:8888`. Vercel is not used. Spotify requests
go to Spotify. Live catalogue searches send artist/title terms to Apple's
public API. Authorized previews stream directly from Apple's audio host after
a click. Spotify artwork loads from Spotify's image service. External shop/Google
links send the query when opened. **Find more shop links** sends the selected
track's ISRC to MusicBrainz; title, artist and duration comparisons happen locally.
The app does not upload your music library or audio files.

Embedded tags and file fingerprints are read locally and retained in DJList's
own database for inventory and matching. The included reader makes no network
requests and does not save a copy of the audio. Native Browse controls open a
chooser on the computer running DJList when you request one.

Requested duplicate comparisons read audio bytes locally without decoding or
uploading them. Database snapshots and their manifests stay in the configured
local backup folder. Cue export reads the saved XML and file identities; it
does not read audio content or the Rekordbox database.

The public repository contains source, tests and documentation. Keep personal
Client IDs, settings, playlists, databases, tokens, logs and music outside Git.

## Development

```bash
python3 -m unittest discover -s tests -v
python3 tools/smoke_test.py
node --test tests/test_ui_model.js
node --check web/app.js
node --check web/library-tools.js
node --check web/track-model.js
bash -n Start_DJList.command
```

The smoke test runs a temporary copy over real loopback HTTP and checks edits,
routes, update consistency and occupied-port handling. It does not call Spotify
or a store. A sandbox blocking loopback binding is not a passing test.

GitHub Actions runs checks on Linux and macOS. Follow [AGENTS.md](AGENTS.md).
Apple badge assets retain Apple's rights; see their adjacent attribution file.
The bundled TinyTag runtime and its original MIT licence are under
`vendor/tinytag/`; [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) records its
source and checksums. No separate package installation is needed to launch.

## Further work

[ROADMAP.md](ROADMAP.md) separates implemented features, checks requiring the
user's Mac, provider-access requirements and deferred development. Automatic
purchasing, full-track downloading, audio moves, audio analysis and direct
Rekordbox/cue writes are not implemented. Reviewed XML cue export is available;
the actual import is a separate user action. Preparation statuses record your
own progress.

## References

- [Spotify PKCE](https://developer.spotify.com/documentation/web-api/tutorials/code-pkce-flow)
- [Spotify API access changes](https://developer.spotify.com/blog/2024-11-27-changes-to-the-web-api)
- [Spotify development-mode guide](https://developer.spotify.com/documentation/web-api/tutorials/february-2026-migration-guide)
- [Apple Search API and promotional-content terms](https://performance-partners.apple.com/search-api)
- [Apple iTunes identity guidelines](https://marketing.services.apple/itunes-identity-guidelines)
- [Bandcamp API documentation](https://bandcamp.com/developer)
- [MusicBrainz API](https://musicbrainz.org/doc/MusicBrainz_API)
- [MusicBrainz request limits](https://musicbrainz.org/doc/MusicBrainz_API/Rate_Limiting)
- [MusicBrainz recording/version conventions](https://musicbrainz.org/doc/Style/Recording)
- [Rekordbox XML format](https://cdn.rekordbox.com/files/20200410160904/xml_format_list.pdf)
- [TinyTag package and supported metadata](https://pypi.org/project/tinytag/)
- [TinyTag upstream source](https://github.com/tinytag/tinytag)
