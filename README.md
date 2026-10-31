# DJList

**A local Spotify wishlist and DJ music tracker.** Find purchase candidates,
listen to permitted store previews, record ownership and link your audio files.

Version **0.4.0**. Development build; see [VERIFICATION.md](VERIFICATION.md) for
the checks performed and [ROADMAP.md](ROADMAP.md) for outstanding work and access
requirements.

## Open it on a Mac

1. Download this repository with **Code → Download ZIP** and unzip it.
2. Double-click **Start_DJList.command**.
3. Choose **Explore the demo**, or open Settings to connect your Spotify app.

Keep the Terminal window running while using DJList. Press **Control+C** there
to stop it. Python 3.9 or later is required; no additional Python packages are
needed. The launcher checks common Python locations. Node is only needed for
developer checks.

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

Import a **Rekordbox XML inventory** to use existing analyzed key/BPM values
when confirming a file match, or enter them manually. DJList does not analyze
audio or claim that a key came from Spotify.

## Refresh and review your existing library

- **Refresh media** reads filenames and file information in your configured
  music folder. It reports partial scans and keeps previous inventory entries
  when some files or folders cannot be read.
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
available. Filename matches are labelled as less certain. They are prompts to
check the files, not proof of identical audio. Nothing is automatically linked,
deleted or marked owned by a scan or XML refresh. Choose **Select this file**
and **Save changes** to confirm a link; this records ownership and can fill
empty key/BPM fields from the XML. You can also tick ownership manually.

## Find files after moving a folder or renaming an SSD

Save the **current music folder** in Settings, then choose **Find moved files**.
Check the **Previous music folder** path and choose **Preview locations**.
Select the proposed links you have checked, then choose **Update selected links**.
Nothing is selected by default.

This works when the folder layout and filenames beneath the old and current
folders are unchanged. DJList maps those relative paths and compares previously
indexed file sizes when available. It does not compare audio content. Missing
files, size mismatches and symlinks cannot be selected; renamed files or a
different folder layout need manual linking.

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
optional Rekordbox database path is configuration only; DJList does not open or
modify the live database.

Scanning reads filenames and file information. Suggestions need confirmation.
It never moves, renames, deletes or edits audio. Ownership is separate from
file availability, so a disconnected drive does not clear ticks or saved paths.

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

## Privacy and operation

The server listens only on `127.0.0.1:8888`. Vercel is not used. Spotify requests
go to Spotify. Live catalogue searches send artist/title terms to Apple's
public API. Authorized previews stream directly from Apple's audio host after
a click. Spotify artwork loads from Spotify's image service. External shop/Google
links send the query when opened. **Find more shop links** sends the selected
track's ISRC to MusicBrainz; title, artist and duration comparisons happen locally.
The app does not upload your music library or audio files.

The public repository contains source, tests and documentation. Keep personal
Client IDs, settings, playlists, databases, tokens, logs and music outside Git.

## Development

```bash
python3 -m unittest discover -s tests -v
python3 tools/smoke_test.py
node --test tests/test_ui_model.js
node --check web/app.js
node --check web/track-model.js
bash -n Start_DJList.command
```

The smoke test runs a temporary copy over real loopback HTTP and checks edits,
routes, update consistency and occupied-port handling. It does not call Spotify
or a store. A sandbox blocking loopback binding is not a passing test.

GitHub Actions runs checks on Linux and macOS. Follow [AGENTS.md](AGENTS.md).
Apple badge assets retain Apple's rights; see their adjacent attribution file.

## Further work

[ROADMAP.md](ROADMAP.md) separates implemented features, checks requiring the
user's Mac, provider-access requirements and deferred development. Automatic
purchasing, full-track downloading, audio moves and Rekordbox/cue writes are
not implemented. Preparation statuses record your own progress.

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
