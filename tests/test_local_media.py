"""Local preview range and filesystem boundary checks on disposable files."""
import hashlib
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import Store
from library_inventory import LibraryInventory, _identity
from local_media import LocalMedia
from spotify_api import AppError


class LocalMediaTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.music = self.root / "Music"
        self.music.mkdir()
        self.store = Store(self.root / "data")
        self.store.put_settings({"mode": "real", "music_root": str(self.music)})
        self.library = LibraryInventory(self.store)
        self.media = LocalMedia(self.store, self.library)
        self.path = self.add(self.music / "track.mp3", b"0123456789")

    def tearDown(self):
        self.temporary.cleanup()

    def add(self, path, content=b"bytes"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        with self.store.connect() as db:
            db.execute("INSERT INTO files(path,root,size,mtime) VALUES (?,?,?,?)",
                       (str(path), str(path.parent), len(content), path.stat().st_mtime))
        return path

    def test_full_stream_and_ranges_return_correct_bytes_without_source_change(self):
        before = self.path.stat()
        cases = [(None, 200, b"0123456789", None), ("bytes=2-5", 206, b"2345", "bytes 2-5/10"),
                 ("bytes=8-", 206, b"89", "bytes 8-9/10"), ("bytes=-3", 206, b"789", "bytes 7-9/10"),
                 ("bytes=8-99", 206, b"89", "bytes 8-9/10"), ("bytes=-99", 206, b"0123456789", "bytes 0-9/10")]
        for header, status, expected, content_range in cases:
            with self.subTest(header=header), self.media.open(_identity(str(self.path)), header) as stream:
                self.assertEqual(stream.status, status)
                self.assertEqual(stream.headers["Content-Length"], str(len(expected)))
                self.assertEqual(stream.headers.get("Content-Range"), content_range)
                self.assertEqual(stream.headers["Content-Type"], "audio/mpeg")
                self.assertEqual(stream.headers["Cache-Control"], "no-store")
                self.assertEqual(b"".join(stream.iter_chunks()), expected)
            self.assertTrue(stream.file.closed)
        after = self.path.stat()
        self.assertEqual((before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns),
                         (after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns))

    def test_invalid_range_has_416_and_size(self):
        for value in ("bytes=10-", "bytes=4-2", "bytes=-0", "bytes=", "bytes=0-1,4-5", "x=0-2", "bytes=-"):
            with self.subTest(value=value), self.assertRaises(AppError) as error:
                with self.media.open(_identity(str(self.path)), value):
                    pass
            self.assertEqual(error.exception.status, 416)
            self.assertEqual(error.exception.content_range, "bytes */10")

    def test_paths_and_unknown_ids_are_rejected(self):
        for value in (str(self.path), "../track.mp3", "x" * 24, "f" * 24, None, {}):
            with self.subTest(value=value), self.assertRaises(AppError):
                with self.media.open(value):
                    pass

    def test_outside_files_master_database_and_removed_storage_are_rejected(self):
        for path in (self.root / "secret.mp3", self.music / "master.db", self.music / ".djlist-removed" / "copy.mp3"):
            self.add(path)
            with self.subTest(path=path), self.assertRaises(AppError):
                with self.media.open(_identity(str(path))):
                    pass

    def test_leaf_symlink_hardlink_and_ancestor_symlink_are_rejected(self):
        source = self.path.read_bytes()
        self.path.unlink()
        outside = self.root / "outside.mp3"
        outside.write_bytes(source)
        self.path.symlink_to(outside)
        with self.assertRaises(AppError):
            with self.media.open(_identity(str(self.path))):
                pass
        self.path.unlink()
        os.link(outside, self.path)
        with self.assertRaises(AppError):
            with self.media.open(_identity(str(self.path))):
                pass
        self.path.unlink()
        self.path.write_bytes(source)
        other = self.root / "Moved"
        self.music.rename(other)
        self.music.symlink_to(other, target_is_directory=True)
        with self.assertRaises(AppError):
            with self.media.open(_identity(str(self.path))):
                pass

    def test_stream_stops_for_file_change_settings_change_or_directory_swap(self):
        with self.media.open(_identity(str(self.path))) as stream:
            self.path.write_bytes(b"changed")
            with self.assertRaises(AppError):
                b"".join(stream.iter_chunks())
        with self.media.open(_identity(str(self.path))) as stream:
            self.store.put_settings({"download_root": str(self.root / "Changed")})
            with self.assertRaises(AppError):
                b"".join(stream.iter_chunks())
        with self.media.open(_identity(str(self.path))) as stream:
            self.music.rename(self.root / "Moved")
            self.music.mkdir()
            with self.assertRaises(AppError):
                b"".join(stream.iter_chunks())

    def test_incoming_folder_is_explicitly_allowed_and_demo_is_blocked(self):
        incoming = self.root / "Incoming"
        path = self.add(incoming / "download.flac", b"purchased")
        self.store.put_settings({"download_root": str(incoming)})
        with self.media.open(_identity(str(path))) as stream:
            self.assertEqual(b"".join(stream.iter_chunks()), b"purchased")
        self.store.put_settings({"mode": "demo"})
        with self.assertRaises(AppError):
            with self.media.open(_identity(str(self.path))):
                pass

    def test_limits_empty_and_large_files_and_index_bound(self):
        self.path.write_bytes(b"")
        with self.assertRaises(AppError):
            with self.media.open(_identity(str(self.path))):
                pass
        self.path.write_bytes(b"12345")
        with patch("local_media.MAX_MEDIA_BYTES", 3), self.assertRaises(AppError):
            with self.media.open(_identity(str(self.path))):
                pass
        with patch("local_media.MAX_INDEXED_PATHS", 0), self.assertRaises(AppError):
            with self.media.open(_identity(str(self.path))):
                pass


if __name__ == "__main__":
    unittest.main()
