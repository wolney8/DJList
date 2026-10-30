# DJList

**A local Spotify wishlist and DJ music tracker.** Find purchase candidates,
listen to permitted store previews, record ownership and link your audio files.

Version **0.2.0**. Development build; see [VERIFICATION.md](VERIFICATION.md) for
the checks performed and the remaining limits.

## Open it on a Mac

1. Download this repository with **Code → Download ZIP** and unzip it.
2. Double-click **Start_DJList.command**.
3. Choose **Explore the demo**, or open Settings to connect your Spotify app.

Keep the Terminal window running while using DJList. Press **Control+C** there
to stop it. Python 3.9 or later is required; no additional Python packages are
needed. The launcher checks common Python locations. Node is only needed for
developer syntax checks.

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

## Find and preview a purchase candidate

Open a track and choose **Find downloads**. DJList automatically searches the
supported catalogue using that track's details. The toolbar also accepts a
manual artist/title search without Spotify.

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

### Other shop searches

Bandcamp and Beatport open their own search pages. Traxsource uses a labelled
Google site search. An all-three Google link is also available. These links
do not supply live inventory, prices or embedded previews in this version.

## Tags, BPM and key

Spotify provides supported metadata such as title, artists, album and duration,
plus ISRC where returned. New/development app restrictions prevent using its
Audio Features and Audio Analysis endpoints for automatic BPM/key here.

Artist genres can be broad or empty; they are not track-specific DJ mood or
energy tags. Automatic Spotify artist-genre enrichment is deferred. Catalogue
genres carry their iTunes source and are only added when selected.

Import a **Rekordbox XML inventory** to use existing analyzed key/BPM values
when confirming a file match, or enter them manually. DJList does not analyze
audio or claim that a key came from Spotify.

## Music and local records

Set your actual music folder in Settings; external SSDs are supported. New
installations suggest `~/Music/DJ Music`. Existing settings are preserved. The
optional Rekordbox database path is configuration only; DJList does not open or
modify the live database.

Scanning reads filenames and file information. Suggestions need confirmation.
It never moves, renames, deletes or edits audio. Ownership is separate from
file availability, so a disconnected drive does not clear ticks or saved paths.

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
a click. External shop/Google links send the query when opened. The app does
not upload your music library or audio files.

The public repository contains source, tests and documentation. Keep personal
Client IDs, settings, playlists, databases, tokens, logs and music outside Git.

## Development

```bash
python3 -m unittest discover -s tests -v
python3 tools/smoke_test.py
node --check web/app.js
bash -n Start_DJList.command
```

The smoke test runs a temporary copy over real loopback HTTP and checks edits,
routes, update consistency and occupied-port handling. It does not call Spotify
or a store. A sandbox blocking loopback binding is not a passing test.

GitHub Actions runs checks on Linux and macOS. Follow [AGENTS.md](AGENTS.md).
Apple badge assets retain Apple's rights; see their adjacent attribution file.

## Further work

- Supported catalogue access for more shops and comparable prices.
- Spotify artist-genre suggestions with clear provenance.
- Multiple playlists, persistent sign-in and a simpler installer/updater.
- Audio analysis, controlled Rekordbox import/export and CueGen integration.
- Browser, keyboard, screen-reader and responsive checks toward WCAG 2.2 AA.

Automatic purchasing, full-track downloading, audio moves and Rekordbox/cue
writes are not implemented. Preparation statuses record your own progress.

## References

- [Spotify PKCE](https://developer.spotify.com/documentation/web-api/tutorials/code-pkce-flow)
- [Spotify API access changes](https://developer.spotify.com/blog/2024-11-27-changes-to-the-web-api)
- [Spotify development-mode guide](https://developer.spotify.com/documentation/web-api/tutorials/february-2026-migration-guide)
- [Apple Search API and promotional-content terms](https://performance-partners.apple.com/search-api)
- [Apple iTunes identity guidelines](https://marketing.services.apple/itunes-identity-guidelines)
- [Bandcamp API documentation](https://bandcamp.com/developer)
- [Rekordbox XML format](https://cdn.rekordbox.com/files/20200410160904/xml_format_list.pdf)
