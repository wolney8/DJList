"""Synthetic tags/header fixtures; no commercial recordings or audio writes."""
import hashlib
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import audio_metadata
from audio_metadata import file_fingerprint, read_audio_metadata
from app import Application, Store
from library_inventory import LibraryInventory


TITLE = "Night Window (Extended Mix)"
ARTIST = "Example Artist"


def id3_bytes(title=TITLE, artist=ARTIST, bpm="172", key="C minor"):
    fields = {b'TIT2': title, b'TPE1': artist, b'TALB': "Fictional release", b'TCON': "Jungle",
              b'TBPM': bpm, b'TKEY': key, b'TSRC': "GBABC2600001"}
    frames = b""
    for name, value in fields.items():
        payload = b'\x03' + value.encode('utf-8')
        frames += name + struct.pack('>I', len(payload)) + b'\x00\x00' + payload
    size = len(frames)
    synchsafe = bytes([(size >> shift) & 0x7f for shift in (21, 14, 7, 0)])
    return b'ID3\x03\x00\x00' + synchsafe + frames


def mp3_bytes(**kwargs):
    # Valid MPEG frame headers with synthetic payload; this is not a recording.
    return id3_bytes(**kwargs) + (b'\xff\xfb\x90\x64' + b'\x00' * 413) * 50


def comments_bytes(title=TITLE, artist=ARTIST, bpm="172", key="C minor", version=""):
    comments = ["TITLE=" + title, "ARTIST=" + artist, "ALBUM=Fictional release", "GENRE=Jungle",
                "BPM=" + bpm, "INITIALKEY=" + key, "ISRC=GBABC2600001"]
    if version:
        comments.append("VERSION=" + version)
    return struct.pack('<I', 0) + struct.pack('<I', len(comments)) + b''.join(
        struct.pack('<I', len(value.encode())) + value.encode() for value in comments)


def flac_bytes(**kwargs):
    info = b'\x10\x00\x10\x00' + b'\x00' * 6
    info += struct.pack('>Q', (44100 << 44) | (1 << 41) | (15 << 36) | (44100 * 193))
    info += b'\x00' * 16
    comments = comments_bytes(**kwargs)
    return b'fLaC\x00' + len(info).to_bytes(3, 'big') + info + b'\x84' + len(comments).to_bytes(3, 'big') + comments


def wav_bytes(**kwargs):
    def chunk(kind, payload):
        return kind + struct.pack('<I', len(payload)) + payload + b'\x00' * (len(payload) % 2)
    body = b'WAVE' + chunk(b'fmt ', struct.pack('<HHIIHH', 1, 1, 8000, 16000, 2, 16))
    body += chunk(b'id3 ', id3_bytes(**kwargs)) + chunk(b'data', b'\x00' * 1600)
    return b'RIFF' + struct.pack('<I', len(body)) + body


def aiff_bytes(**kwargs):
    def chunk(kind, payload):
        return kind + struct.pack('>I', len(payload)) + payload + b'\x00' * (len(payload) % 2)
    rate = bytes.fromhex('400eac44000000000000')  # 44100 in 80-bit extended form
    body = b'AIFF' + chunk(b'COMM', struct.pack('>hIh', 1, 44100, 16) + rate)
    body += chunk(b'ID3 ', id3_bytes(**kwargs)) + chunk(b'SSND', b'\x00' * 8)
    return b'FORM' + struct.pack('>I', len(body)) + body


def mp4_bytes(title=TITLE, artist=ARTIST, bpm="172", key="C minor"):
    def atom(kind, payload):
        return struct.pack('>I', len(payload) + 8) + kind + payload
    def text_atom(kind, value):
        return atom(kind, atom(b'data', struct.pack('>II', 1, 0) + value.encode()))
    tags = b''.join(text_atom(k, v) for k, v in [(b'\xa9nam', title), (b'\xa9ART', artist),
                                               (b'\xa9alb', 'Fictional release'), (b'\xa9gen', 'Jungle'), (b'tmpo', bpm)])
    tags += atom(b'----', atom(b'mean', b'\x00' * 4 + b'com.apple.iTunes') +
                 atom(b'name', b'\x00' * 4 + b'initialkey') + atom(b'data', struct.pack('>II', 1, 0) + key.encode()))
    moov = atom(b'mvhd', b'\x00' * 12 + struct.pack('>II', 1000, 193000))
    moov += atom(b'udta', atom(b'meta', b'\x00' * 4 + atom(b'ilst', tags)))
    return atom(b'ftyp', b'M4A ' + b'\x00' * 4 + b'isom') + atom(b'moov', moov)


def ogg_bytes(**kwargs):
    def page(packet, sequence, flags=0, granule=0):
        segments = bytes([255] * (len(packet) // 255) + [len(packet) % 255])
        header = b'OggS' + bytes([0, flags]) + struct.pack('<qIII', granule, 1, sequence, 0) + bytes([len(segments)])
        return header + segments + packet
    head = b'OpusHead' + struct.pack('<BBHIhB', 1, 2, 0, 48000, 0, 0)
    return page(head, 0, 2) + page(b'OpusTags' + comments_bytes(**kwargs), 1) + page(b'\x00', 2, 4, 48000 * 193)


class AudioFixture:
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.music = self.root / 'Music'
        self.music.mkdir()

    def tearDown(self):
        self.temporary.cleanup()

    def write(self, name='unknown.flac', payload=None):
        path = self.music / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(flac_bytes() if payload is None else payload)
        return path


class AudioMetadataTests(AudioFixture, unittest.TestCase):
    def test_six_container_families_read_embedded_identity_tempo_key_and_duration(self):
        for extension, factory in [('mp3', mp3_bytes), ('flac', flac_bytes), ('wav', wav_bytes),
                                   ('aiff', aiff_bytes), ('m4a', mp4_bytes), ('ogg', ogg_bytes)]:
            with self.subTest(format=extension):
                file = self.write('generic.' + extension, factory())
                before = file.read_bytes()
                result = read_audio_metadata(file, self.music)
                self.assertEqual(result['status'], 'read', result)
                self.assertEqual((result['title'], result['artist'], result['album'], result['genre']),
                                 (TITLE, ARTIST, 'Fictional release', 'Jungle'))
                self.assertEqual((result['bpm'], result['musical_key']), ('172', 'C minor'))
                self.assertGreater(result['duration_ms'], 0)
                self.assertTrue(result['mime_type'].startswith('audio/'))
                self.assertEqual(file.read_bytes(), before)

    def test_unknown_bpm_and_missing_key_stay_empty(self):
        for value in ('NaN', 'inf', '-120', '0', '1000', 'fast'):
            with self.subTest(value=value):
                file = self.write('bad-number.flac', flac_bytes(bpm=value, key=''))
                result = read_audio_metadata(file, self.music)
                self.assertEqual(result['status'], 'read')
                self.assertEqual((result['bpm'], result['musical_key']), ('', ''))

    def test_control_characters_and_long_fields_are_bounded(self):
        file = self.write(payload=flac_bytes(title='Name\x00\n' + 'x' * 2000, artist='Art\x1bist'))
        result = read_audio_metadata(file, self.music)
        self.assertEqual(result['status'], 'read')
        self.assertLessEqual(len(result['title']), 1000)
        self.assertFalse(any(ord(c) < 32 for c in result['title'] + result['artist']))

    def test_unreadable_and_malformed_files_keep_explicit_failure(self):
        broken = self.write(payload=b'not an audio file')
        self.assertEqual(read_audio_metadata(broken, self.music)['status'], 'error')
        self.assertEqual(read_audio_metadata(self.music / 'missing.flac', self.music)['status'], 'unavailable')
        self.assertEqual(read_audio_metadata(self.write('raw.aac', b'\xff\xf1' + b'\x00' * 100), self.music)['status'], 'unsupported')

    def test_symlinks_outside_paths_and_fifo_are_not_opened_for_metadata(self):
        outside = self.root / 'outside.flac'
        outside.write_bytes(flac_bytes())
        leaf = self.music / 'link.flac'
        leaf.symlink_to(outside)
        parent = self.music / 'linked-folder'
        parent.symlink_to(self.root, target_is_directory=True)
        alias_root = self.root / 'alias'
        alias_root.symlink_to(self.music, target_is_directory=True)
        with patch('audio_metadata.TinyTag.get') as parser:
            for path, root in [(outside, self.music), (leaf, self.music),
                               (parent / 'outside.flac', self.music), (alias_root / 'link.flac', alias_root),
                               (self.music / '..' / 'outside.flac', self.music)]:
                self.assertEqual(read_audio_metadata(path, root)['status'], 'unavailable')
                self.assertIsNone(file_fingerprint(path, root))
            if hasattr(os, 'mkfifo'):
                fifo = self.music / 'pipe.flac'
                os.mkfifo(fifo)
                self.assertEqual(read_audio_metadata(fifo, self.music)['status'], 'unavailable')
            parser.assert_not_called()

    def test_parser_byte_and_operation_budgets_are_enforced(self):
        file = self.write()
        def oversized(**kwargs):
            kwargs['file_obj'].read(audio_metadata.MAX_READ_BYTES + 1)
        with patch('audio_metadata.TinyTag.get', side_effect=oversized):
            result = read_audio_metadata(file, self.music)
            self.assertEqual(result['status'], 'limited')
            self.assertEqual(result['title'], '')
        with patch('audio_metadata.MAX_IO_OPERATIONS', 1):
            self.assertEqual(read_audio_metadata(file, self.music)['status'], 'limited')

    def test_file_replacement_during_read_discards_the_metadata(self):
        file = self.write()
        original = audio_metadata.TinyTag.get
        def replacement(**kwargs):
            tag = original(**kwargs)
            file.rename(self.music / 'old.flac')
            file.write_bytes(flac_bytes(title='Replacement'))
            return tag
        with patch('audio_metadata.TinyTag.get', side_effect=replacement):
            result = read_audio_metadata(file, self.music)
        self.assertEqual(result['status'], 'changed')
        self.assertEqual((result['title'], result['bpm']), ('', ''))
        self.assertIsNone(result['fingerprint'])

    def test_read_does_not_request_artwork_and_uses_supplied_readonly_handle(self):
        file = self.write()
        original = audio_metadata.TinyTag.get
        with patch('audio_metadata.TinyTag.get', wraps=original) as parser:
            self.assertEqual(read_audio_metadata(file, self.music)['status'], 'read')
        args = parser.call_args.kwargs
        self.assertFalse(args['image'])
        self.assertTrue(args['tags'])
        self.assertTrue(args['duration'])
        self.assertFalse(hasattr(args['file_obj'], 'write'))

    def test_vendored_runtime_matches_the_recorded_unmodified_source_hashes(self):
        source = Path(__file__).resolve().parents[1] / 'vendor' / 'tinytag'
        provenance = json.loads((source / 'PROVENANCE.json').read_text())
        self.assertEqual((provenance['version'], provenance['license']), ('2.3.2', 'MIT'))
        for name, digest in provenance['files'].items():
            self.assertEqual(hashlib.sha256((source / name).read_bytes()).hexdigest(), digest)


class MetadataInventoryTests(AudioFixture, unittest.TestCase):
    # These cover metadata affecting match decisions and saved state, not the
    # implementation's individual assignments.
    def setUp(self):
        super().setUp()
        self.store = Store(self.root / 'data')
        self.library = LibraryInventory(self.store)
        self.store.upsert([{'spotify_id': 'fictional', 'artists': ARTIST, 'title_and_version': TITLE,
                           'duration_ms': 193000, 'album': 'Fictional release'}], 'fixture')

    def indexed(self, name='opaque-name.flac', payload=None):
        file = self.write(name, payload)
        with self.store.connect() as db:
            db.execute('INSERT INTO files(path,root,size,mtime,title) VALUES (?,?,?,?,?)',
                       (str(file), str(self.music), file.stat().st_size, file.stat().st_mtime, file.stem))
        metadata = read_audio_metadata(file, self.music)
        self.library.store_scan_metadata({str(file): metadata}, str(self.music), complete=True)
        return file

    def test_audio_tags_match_opaque_filename_without_changing_annotations(self):
        before = self.store.rows('real')
        file = self.indexed()
        entry = self.library.review(before, str(self.music))['entries'][0]
        self.assertEqual((entry['identity_source'], entry['source'], entry['bpm']), ('audio_tags', 'scan', '172'))
        candidate = self.library.matches(before, str(self.music))[0]['candidates'][0]
        self.assertEqual((candidate['path'], candidate['match_status']), (str(file), 'likely_match'))
        self.assertIn('audio file', ' '.join(candidate['reasons']))
        self.assertEqual(self.store.rows('real'), before)

    def test_same_size_changed_file_invalidates_cache_and_identity(self):
        file = self.indexed()
        self.assertEqual(self.library.cached_audio_metadata(file, self.music)['title'], TITLE)
        before = file.stat()
        file.write_bytes(flac_bytes(title=TITLE.replace('Night', 'Other')))
        os.utime(file, ns=(before.st_atime_ns, before.st_mtime_ns))
        self.assertEqual(file.stat().st_size, before.st_size)
        self.assertIsNone(self.library.cached_audio_metadata(file, self.music))
        self.library.invalidate()
        entry = self.library.review(self.store.rows('real'), str(self.music))['entries'][0]
        self.assertEqual((entry['identity_source'], entry['metadata_status']), ('filename', 'stale'))
        self.assertEqual(entry['bpm'], '')

    def test_scan_retries_failed_metadata_without_file_change_then_reuses_success(self):
        file = self.write()
        fingerprint = file_fingerprint(file, self.music)
        application = Application(self.root / 'scan-data')
        try:
            application.change_settings({'music_root': str(self.music),
                                         'auto_library_refresh': False})
            with patch('audio_metadata.TinyTag.get', side_effect=OSError('Temporary drive read failure')):
                first = application.scan()
            self.assertEqual((first['indexed'], first['metadata_issues']), (1, 1))
            entry = application.library.review([], str(self.music))['entries'][0]
            self.assertEqual((entry['metadata_status'], entry['identity_source']), ('error', 'filename'))
            self.assertEqual(file_fingerprint(file, self.music), fingerprint)

            with patch('audio_metadata.TinyTag.get', wraps=audio_metadata.TinyTag.get) as parser:
                retried = application.scan()
                self.assertEqual((retried['metadata_read'], retried['metadata_issues']), (1, 0))
                parser.assert_called_once()
                cached = application.scan()
                self.assertEqual((cached['metadata_read'], cached['metadata_issues']), (1, 0))
                parser.assert_called_once()
            entry = application.library.review([], str(self.music))['entries'][0]
            self.assertEqual((entry['title'], entry['metadata_status'], entry['identity_source']),
                             (TITLE, 'read', 'audio_tags'))
            self.assertEqual(file_fingerprint(file, self.music), fingerprint)
        finally:
            application.close()

    def test_separate_version_notes_are_kept_and_require_match_review(self):
        self.indexed(payload=flac_bytes(version='Radio edit'))
        candidate = self.library.matches(self.store.rows('real'), str(self.music))[0]['candidates'][0]
        self.assertEqual((candidate['version_notes'], candidate['match_status']), ('Radio edit', 'check_version'))
        self.assertIn('Radio edit', ' '.join(candidate['reasons']))

    def test_xml_wins_over_tags_and_offline_tag_snapshot_survives(self):
        file = self.indexed()
        self.library.import_xml('<DJ_PLAYLISTS><COLLECTION Entries="1"><TRACK Location="%s" '
                                'Name="XML Title" Artist="XML Artist" AverageBpm="130" Tonality="F minor"/>'
                                '</COLLECTION></DJ_PLAYLISTS>' % file.as_uri(), str(self.music))
        entry = self.library.review([], str(self.music))['entries'][0]
        self.assertEqual((entry['source'], entry['title'], entry['bpm']), ('rekordbox_xml', 'XML Title', '130'))
        self.library.import_xml('<DJ_PLAYLISTS><COLLECTION Entries="0"/></DJ_PLAYLISTS>', str(self.music))
        self.music.rename(self.root / 'Offline')
        self.library.invalidate()
        entry = self.library.review([], str(self.music))['entries'][0]
        self.assertEqual((entry['identity_source'], entry['metadata_status'], entry['available']), ('audio_tags', 'offline', False))
        self.assertEqual(entry['title'], TITLE)

    def test_metadata_cache_is_scoped_to_selected_root_and_partial_scans_preserve_other_entries(self):
        first = self.indexed('first.flac')
        second = self.write('second.flac')
        metadata = read_audio_metadata(second, self.music)
        self.library.store_scan_metadata({str(second): metadata}, str(self.music), complete=False)
        self.assertIsNotNone(self.library.cached_audio_metadata(first, self.music))
        self.assertIsNone(self.library.cached_audio_metadata(first, self.root))
        self.library.store_scan_metadata({str(second): metadata}, str(self.music), complete=True)
        self.assertIsNone(self.library.cached_audio_metadata(first, self.music))
        self.assertIsNotNone(self.library.cached_audio_metadata(second, self.music))

    def test_prepare_link_rechecks_tags_and_hydrates_only_current_values(self):
        file = self.indexed()
        self.library.prepare_link(str(file), str(self.music))
        with self.store.connect() as db:
            row = dict(db.execute('SELECT * FROM files WHERE path=?', (str(file),)).fetchone())
        self.assertEqual((row['origin'], row['bpm'], row['musical_key']), ('audio_tags', '172', 'C minor'))
        file.write_bytes(flac_bytes(bpm='128', key='D minor'))
        self.library.prepare_link(str(file), str(self.music))
        with self.store.connect() as db:
            row = dict(db.execute('SELECT * FROM files WHERE path=?', (str(file),)).fetchone())
        self.assertEqual((row['bpm'], row['musical_key']), ('128', 'D minor'))
        self.assertFalse(self.store.rows('real')[0]['owned'])


if __name__ == '__main__':
    unittest.main()
