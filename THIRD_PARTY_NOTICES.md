# Third-party notices

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
