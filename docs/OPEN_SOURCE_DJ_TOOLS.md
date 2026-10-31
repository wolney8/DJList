# Open-source DJ tools and cue preparation

Assessment checked against the primary sources below on 31 October 2026.
Repository activity and upstream compatibility claims do not establish
compatibility with the user's installed Rekordbox version.

## Implemented in DJList 0.6

`cue_planner.py` implements a review and XML export workflow using Python's
standard library. It does not include or run any of the candidate tools below.
The existing TinyTag integration remains responsible for reading embedded audio
tags; it does not detect beats, musical keys or phrases from audio.

The planner reads the selected, refreshed Rekordbox XML and preserves the full
original track element, including its metadata, existing cues, loops and tempo
markers. It calculates suggestions at an interval of 8, 16, 32 or 64 bars. It
honors 4/4 tempo changes and the beat number at each XML tempo anchor. These are
calculated grid positions: they do not identify intros, drops, phrases or outros.
Other time signatures are explicitly unsupported in this version.

If the XML track has no tempo markers, the user can confirm a BPM between 20 and
400 and a first downbeat in seconds. The export then includes a constant 4/4
grid calculated from those values. DJList does not analyze the recording to
verify them or replace an existing XML grid.

Nothing is preselected. A review expires after ten minutes and can be exported
once. The export includes only selected suggestions, uses unused hotcue slots
A–H, and skips positions within four grid bars of an existing cue or loop
boundary. An XML file with all eight slots occupied cannot gain new hotcues.
Source XML, music-folder identity and audio-file identity are checked again at
export; stale or unavailable inputs require a new review. Uploaded XML without
a saved, refreshable source is insufficient for cue export.

Planning also compares the current audio identity with the fingerprint recorded
at XML refresh. Older inventory records, files unavailable during import and
files changed since refresh require Refresh XML with the drive present. A fresh
snapshot cannot establish that a beatgrid fits the recording or include cues
that have not been exported from Rekordbox.

The result is a separate, uniquely named XML download for one track. It neither
imports into Rekordbox nor opens `master.db`, reads audio content, edits tags,
moves music, or overwrites the source XML. Export is disabled in demo mode.
Existing XML cues may lag changes made inside Rekordbox: export a fresh
collection from Rekordbox before planning, and make a full Rekordbox library
backup before a manual import.

The interchange format is documented in the official
[Rekordbox XML specification](https://cdn.rekordbox.com/files/20200410160904/xml_format_list.pdf):
`TEMPO` describes tempo anchors and bar phase; `POSITION_MARK` describes cue and
loop positions. The generated playlist references its original file location.
Real import behavior, cue merging and beatgrid retention on the user's
Rekordbox installation remain unverified.

## Candidates assessed, not integrated

The last column records an engineering recommendation, rather than a claim that
the tool has been installed, benchmarked or verified with this library.

| Project and primary source | Verified upstream purpose and license | Proposed role and current decision |
| --- | --- | --- |
| [CueGen](https://github.com/mganss/CueGen), [releases](https://github.com/mganss/CueGen/releases) | MIT. Creates cues from existing Rekordbox phrases or Mixed in Key cue data. It operates directly on the Rekordbox database and requires Rekordbox to be closed. The README lists Rekordbox 6; the release page currently lists 1.0.37. | Closest fit for a future phrase-cue adapter. Do not execute it in this build. Pin a release, confirm the installed Rekordbox version and explicit active database, then validate a selected test batch on a copy before considering any live operation. |
| [pyrekordbox](https://github.com/dylanljones/pyrekordbox) | MIT. Unofficial Python access to master.db, XML and analysis files. The README lists tested versions 5.8.6, 6.7.7 and 7.0.9, and SQLCipher as necessary for Rekordbox 6/7 master.db. | Preferred candidate for a later read-only adapter to a verified snapshot and existing analysis. Its general API also supports writes, so a normal connection must not be treated as read-only by assumption. Version and packaging checks remain outstanding. |
| [librosa](https://github.com/librosa/librosa), [beat tracking](https://librosa.org/doc/0.11.0/generated/librosa.beat.beat_track.html) | ISC. Python music-analysis library with tempo and beat-location estimation. | First candidate for an optional local beat-analysis experiment. Beat estimates would need audible review and separate downbeat confirmation. Evaluate Mac packaging, runtime cost and results on representative recordings before adding a dependency. |
| [Chromaprint](https://github.com/acoustid/chromaprint), [license](https://github.com/acoustid/chromaprint/blob/master/LICENSE.md) | Audio fingerprints designed for nearly identical recordings, including duplicate detection. Upstream describes the combined project as LGPL 2.1; individual code and compiled dependencies have additional license considerations. | Candidate for finding the same recording across formats or encodings. Such results would remain suggestions requiring review; they must not trigger deletion. This is separate from a byte-for-byte duplicate check. No fingerprint engine is included yet. |
| [Mixxx](https://github.com/mixxxdj/mixxx), [features](https://mixxx.org/features/), [license](https://github.com/mixxxdj/mixxx/blob/main/LICENSE) | GPLv2 or later DJ application with BPM/key detection, library organization and external library support. | Useful standalone comparison tool and source of workflow ideas. Embedding a full DJ application is outside this batch. No Mixxx source or binaries are copied into DJList. |
| [aubio](https://github.com/aubio/aubio) | GPLv3 or later. Audio-analysis library with onset, pitch, tempo and beat detection, plus a Python interface. | Alternative beat-analysis candidate after packaging and licensing review. It would supply timing estimates, not prove musical phrase boundaries or safe Rekordbox import. Not bundled. |
| [libkeyfinder](https://github.com/mixxxdj/libkeyfinder) | GPLv3 or later. C++ musical-key estimation library maintained under the Mixxx organization. | Candidate for local key estimation, separate from cue placement. Requires a compatible distribution approach and Mac integration work. Not bundled. |
| [Essentia](https://github.com/MTG/essentia), [licensing](https://essentia.upf.edu/licensing_information.html) | Audio-analysis and classification library. Upstream offers AGPLv3 and commercial licensing; its published models have separate noncommercial licensing conditions. | Possible later source of richer tags, rhythm and classification. Library, model and dependency licenses need separate assessment, along with model size and Mac performance. Deferred; no code or model weights bundled. |

## Next useful increments

1. Verify the generated one-track XML on the actual Rekordbox version: check its
   file location, every existing cue and loop, new selected hotcues, tempo
   transitions and behavior when the track already exists in the collection.
   Keep the full library backup and original XML available during that check.
2. Evaluate read-only access to existing Rekordbox analysis through a pinned
   pyrekordbox version, using a copy and explicit paths. Retain XML as a visible
   source and never silently fall back to a different database or collection.
3. Benchmark an optional analysis worker using representative short, long,
   variable-tempo and rhythmically complex tracks. Store suggestions separately
   from confirmed data; provide waveform/listening review before export.
4. Assess acoustic duplicate detection after the exact-file audit is established.
   An edit, remix, extended version or alternate master must not be automatically
   merged just because metadata or an audio fingerprint looks similar.

Before incorporating any project, pin the dependency, record its license and
required notices, review transitive dependencies and model licenses, and verify
the supported Mac/Python/Rekordbox combination. This assessment adds no GPL or
AGPL source, third-party binaries or analysis models to DJList.

## Verification boundary

Automated tests use synthetic XML and temporary audio placeholders. They cover
tempo changes, bar phase, existing cue preservation, explicit selections, slot
collisions, invalid numeric data, unsafe XML, source changes, drive/path checks,
expiry, demo isolation and the prohibition on opening audio/database content.
They do not establish musical accuracy, real Rekordbox import compatibility,
backup restorability, or performance on the user's external SSD.
