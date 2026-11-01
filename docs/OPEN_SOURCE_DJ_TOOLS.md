# Open-source DJ tools and cue preparation

Assessment checked against the primary sources below on 31 October 2026.
Repository activity and upstream compatibility claims do not establish
compatibility with the user's installed Rekordbox version.

The unreleased maintenance branch now provisions CueGen internally from an
exact verified existing copy or its pinned official release. No separate app
launch or terminal command is required. This replaces the separate executable
setup described in the original 0.7 assessment below. The component remains a
protected process with bounded output, not a reimplementation of CueGen's
analysis logic. Native runtime checks use a synthetic encrypted library in
macOS CI; consult the verification record before calling them verified.

Rekordbox relocation is researched separately in
[REKORDBOX_RELOCATION.md](REKORDBOX_RELOCATION.md). The current implementation
tracks renames and verifies fresh XML after Rekordbox performs Relocate; it
does not update the live database or analysis paths.

## Implemented in DJList 0.7: grid-based cue review

`cue_planner.py` implements a review and XML export workflow using Python's
standard library. Its grid engine does not run an external tool. The optional
CueGen adapter below supplies a second suggestion engine through the same review
and export boundary.
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

## Optional external CueGen adapter

[CueGen](https://github.com/mganss/CueGen) is MIT-licensed and creates cues from
existing Rekordbox phrase analysis or Mixed In Key cue data. Upstream normally
operates on a Rekordbox database. DJList's adapter deliberately limits that
operation to a separate working copy and a one-track review.

`cuegen_bridge.py` accepts an explicitly selected **CueGen 1.0.37** executable.
No executable, .NET runtime or CueGen dependencies are bundled or downloaded.
The version is checked from bounded run output; a saved executable path does
not mean its runtime or version has been verified.

Before a run, DJList requires closed library-writing tools, a fresh XML source
and matching audio identity. It creates or rechecks a cold database snapshot,
copies its verified bytes to a unique disposable workspace, and passes only that
database copy and the selected audio path to CueGen. The original database and
retained backup are not CueGen output destinations.

The adapter requires macOS `sandbox-exec`, denies network access and source
writes, and permits writes only within the disposable workspace. It also uses
CueGen's dry-run/merge options, without treating dry-run as the enforcement
boundary. There is no unsandboxed fallback. The run limit is two minutes with
at most 2 MiB of captured output. Actual sandbox/.NET compatibility remains a
Mac verification item.

Supported settings cover phrase or Mixed In Key source, hot or memory cues,
count, minimum spacing/phrase length, beat offset, bar snapping, phrase/energy/
sequence colours, comments, phrase names/priority and intro/outro loops. New
phrase analysis is not generated. Only validated suggestions for the exact
selected XML track/path enter the review; unexpected output or assumed BPM is
rejected.

The shared planner preserves the original XML track, grid, cues and loops.
Suggestions conflicting with occupied slots or nearby existing markers are
excluded. Nothing is preselected. Up to eight hot-cue or ten memory-cue
suggestions can be exported into a separate XML after listening and selection.
MyTag edits, track colours, overwriting/removing existing cues, arbitrary flags,
automatic import and live database writes are unavailable in this adapter.

The hot/memory colour conversion tables are adapted from
[`CueGen/ColorTable.cs` at commit `eac34c8c1f007599261f45127095717e0f4abd01`](https://github.com/mganss/CueGen/blob/eac34c8c1f007599261f45127095717e0f4abd01/CueGen/ColorTable.cs).
The original MIT notice is retained in
[`vendor/cuegen/LICENSE`](../vendor/cuegen/LICENSE); see
[third-party notices](../THIRD_PARTY_NOTICES.md). This palette is the only
upstream CueGen code/data copied into DJList's runtime.

Source review and synthetic parser/sandbox tests do not establish successful
execution with the user's CueGen binary, accurate cue placement or correct
import on their installed Rekordbox version. Keep those checks separate from
implemented adapter behaviour.

## Candidates assessed, not integrated

The last column records an engineering recommendation, rather than a claim that
the tool has been installed, benchmarked or verified with this library.

| Project and primary source | Verified upstream purpose and license | Proposed role and current decision |
| --- | --- | --- |
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
   Include the optional CueGen path only after verifying its binary, sandbox
   runtime and existing analysis data using the isolated working-copy flow.
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
the supported Mac/Python/Rekordbox combination. v0.7 adds no GPL or AGPL source,
third-party binaries or analysis models to DJList; the CueGen palette carries
the MIT notice above.

## Verification boundary

Automated tests use synthetic XML and temporary audio placeholders. They cover
tempo changes, bar phase, existing cue preservation, explicit selections, slot
collisions, invalid numeric data, unsafe XML, source changes, drive/path checks,
expiry and demo isolation. The grid engine does not open audio/database content.
External-engine checks use synthetic CueGen results and disposable source files;
they do not establish that an actual CueGen process can run on the user's Mac.
They do not establish musical accuracy, real Rekordbox import compatibility,
backup restorability, or performance on the user's external SSD.
