# Third-party notices

## CueGen colour palette and optional external adapter

DJList 0.7.0 adapts the hot-cue and memory-cue RGB lookup tables from
**CueGen**, copyright **(c) 2021 Michael Ganss**, under the **MIT licence**.
The tuple values in `cuegen_bridge.py` are a Python representation of the
upstream colour data used to translate returned cue colours into XML.

Pinned source:

- [CueGen/ColorTable.cs at eac34c8c1f007599261f45127095717e0f4abd01](https://github.com/mganss/CueGen/blob/eac34c8c1f007599261f45127095717e0f4abd01/CueGen/ColorTable.cs)
- [CueGen upstream](https://github.com/mganss/CueGen)
- [CueGen releases](https://github.com/mganss/CueGen/releases)

Retrieved and checked **31 October 2026**. The complete upstream copyright and
permission notice is retained in [`vendor/cuegen/LICENSE`](vendor/cuegen/LICENSE).
Distributions of this palette must retain that notice.

The optional adapter invokes a separately installed **CueGen 1.0.37** executable
selected by the user. DJList does not redistribute that executable, its runtime,
dependencies or analysis data. The adapter uses a disposable database copy,
mandatory macOS sandbox and bounded one-track output; it does not grant rights
to recordings or analysis from other products. Actual binary/runtime
compatibility and Rekordbox import are separate verification requirements.

## TinyTag audio metadata reader

DJList 0.5.0 bundles the unmodified runtime package from **TinyTag 2.3.2**,
released 7 September 2026. TinyTag is licensed under the **MIT licence**. The
original copyright and permission notice are retained in
[`vendor/tinytag/LICENSE`](vendor/tinytag/LICENSE); distribution of this bundled
code must retain that notice. This dependency does not change DJList's licence.

Official project and release sources:

- [TinyTag on PyPI](https://pypi.org/project/tinytag/2.3.2/)
- [TinyTag upstream repository](https://github.com/tinytag/tinytag)
- [Official 2.3.2 wheel](https://files.pythonhosted.org/packages/6b/54/d378858d14e5c5c7b921cb9ffccf27be3d34f3d6d010c57404ddd634685d/tinytag-2.3.2-py3-none-any.whl)

Retrieved **31 October 2026**. SHA-256 of the original wheel:

`ebaf957915266b9c20414a10f8c5170a345aa039a794650d39d66e7d6b96199f`

Only `__init__.py`, `tinytag.py` and the upstream licence are included from that
release. [`vendor/tinytag/PROVENANCE.json`](vendor/tinytag/PROVENANCE.json) records
the source archive, retrieval date and individual file checksums. No separate
package installation or network download is required when the user starts
DJList. The bounds, path checks, caching and field selection in
`audio_metadata.py` are DJList's wrapper around the unmodified reader.

This integration reads supported embedded tags and file-header duration
locally. Image extraction is disabled. It does not analyze audio to generate
BPM/key, modify audio, or copy the full recording. Selected metadata and file
fingerprints are cached in DJList's own local database. The reader's software
licence does not grant any rights to recordings, artwork or third-party tags.

## iTunes Store badge

`web/assets/download-on-itunes.svg` is Apple's approved **Get it on iTunes
Store** badge, copied unchanged from:

`US_UK_iTunes_Store_Get_Badge_RGB_012618.svg`

Guidelines:
https://marketing.services.apple/itunes-identity-guidelines

Official package linked by those guidelines:
https://www.apple.com/105/media/us/ipoditunes/itunes/2018/3a5387c5_1ebd_4993_9633_ff7264dc27bf/itunes-store-badges/us_uk.zip

Retrieved 30 October 2026. SHA-256:

`1509ad061e178ab3acb8095ef5488cdc02f8a3b8c196b6e44b661051ae8bbd4f`

Apple retains its rights in this artwork. iTunes Store is a trademark of Apple
Inc., registered in the U.S. and other countries. The badge is used to link to
the corresponding store content and must not be redrawn or altered.

## Promotional previews and catalogue metadata

Source and terms: https://performance-partners.apple.com/search-api

Preview audio is streamed for promoting the corresponding iTunes content. It
is displayed with the same-record link and approved badge and the attribution
**provided courtesy of iTunes**. It is not downloaded, saved, cached, converted
or offered as DJ audio by this application. Search responses use a five-minute
in-memory metadata cache. Automated lookup results, including their source and
check time, are retained in the user's local DJList database. Explicit per-track
refresh bypasses the search cache while respecting the provider rate limit.

Catalogue information and prices are supplied by the provider and may change.
The store page and checkout govern the actual offer.

## Spotify metadata and artwork

Source and display guidance:
https://developer.spotify.com/documentation/design

Playlist artwork is loaded from Spotify's image host, displayed without cropping
or overlays, and accompanied by the Spotify mark and a link to that track. Image
bytes are not downloaded into the music library or bundled with the application.
Spotify retains rights in its marks; artwork belongs to its respective owners.
Missing or failed artwork uses DJList's music-note icon with a stable colour.

## MusicBrainz recording metadata and shop links

The optional **Find more shop links** action uses MusicBrainz, a community
metadata project operated by the MetaBrainz Foundation. When a network lookup
is needed, DJList sends the selected track's ISRC recording identifier to
MusicBrainz. It compares the returned artist, title, version notes and duration
locally, and displays eligible direct Bandcamp and Beatport purchase links with
a link to their MusicBrainz recording source. These are community suggestions;
the shop page and recording still need to be checked.

MusicBrainz classifies recordings, ISRCs, relationships and URLs as core data,
released under **CC0**. DJList's additional-link lookup uses that core metadata.
It does not request supplementary annotations, tags or genre associations,
which have a separate licence. The MusicBrainz database licence does not grant
rights to retailer websites, vendor artwork, recordings or other vendor assets.

The client identifies DJList in its User-Agent and spaces network requests by
at least **1.05 seconds**. Complete results, including no-match results, have a
bounded **seven-day in-memory metadata cache**. Errors and incomplete results
are not cached. **Check again** bypasses that cache while retaining request
pacing and any server-requested cooldown. MusicBrainz lookup is manual; Spotify
refresh does not trigger it or poll its metadata for changes.

This integration does not fetch MusicBrainz audio or imagery, retrieve retailer
pages, provide previews, or supply prices, BPM or musical key. It does not infer
those values from the existence of a purchase link. MusicBrainz's API service
usage terms are separate from its database licence; its documentation describes
free non-commercial access and links to commercial service plans.

Official sources:

- API and ISRC lookup: https://musicbrainz.org/doc/MusicBrainz_API
- Request pacing and identification: https://musicbrainz.org/doc/MusicBrainz_API/Rate_Limiting
- Database licence: https://musicbrainz.org/doc/About/Data_License
- Core and supplementary data: https://musicbrainz.org/doc/MusicBrainz_Database
- Recording purchase-link relationship: https://musicbrainz.org/relationship/92777657-504c-4acb-bd33-51a201bd57e1
