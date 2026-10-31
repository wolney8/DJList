"""Cold backup safety with temporary synthetic databases and injected processes."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import Store
from rekordbox_backups import INTERVAL_SECONDS, RekordboxBackups, check_rekordbox_closed
from spotify_api import AppError


class ProcessCheckTests(unittest.TestCase):
    def test_fixed_mac_process_query_reports_only_known_names(self):
        runner = Mock(return_value=subprocess.CompletedProcess([], 0, stdout="/sbin/launchd\n/Applications/rekordbox 7/rekordbox\n/Downloads/CueGen.Console --db /private/example/master.db\n/Applications/Library Fixer\n", stderr=""))
        result = check_rekordbox_closed("darwin", runner)
        self.assertTrue(result["known"])
        self.assertEqual(result["running"], ["CueGen", "Library Fixer", "Rekordbox"])
        self.assertNotIn("private", json.dumps(result))
        self.assertEqual(runner.call_args.args[0], ["/bin/ps", "-axww", "-o", "command="])
        self.assertEqual(runner.call_args.kwargs["timeout"], 5)

    def test_failed_empty_oversized_or_unsupported_process_checks_fail_closed(self):
        runner = Mock()
        self.assertFalse(check_rekordbox_closed("linux", runner)["known"])
        runner.assert_not_called()
        for response in (subprocess.CompletedProcess([], 1, stdout="x", stderr="denied"),
                         subprocess.CompletedProcess([], 0, stdout="", stderr=""),
                         subprocess.CompletedProcess([], 0, stdout="x" * (2 * 1024 * 1024 + 1), stderr="")):
            runner.return_value = response
            self.assertFalse(check_rekordbox_closed("darwin", runner)["known"])
        runner.side_effect = subprocess.TimeoutExpired("ps", 5)
        self.assertFalse(check_rekordbox_closed("darwin", runner)["known"])


class RekordboxBackupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="djlist-backup-test-")
        self.root = Path(self.temporary.name).resolve()
        self.library = self.root / "Rekordbox"
        self.library.mkdir()
        self.source = self.library / "master.db"
        self.original = bytes(range(256)) * 512
        self.source.write_bytes(self.original)
        self.companion = self.library / "masterPlaylists6.xml"
        self.companion.write_bytes(b'<PLAYLISTS Version="synthetic"/>')
        self.companion_bytes = self.companion.read_bytes()
        self.store = Store(self.root / "DJList")
        self.store.put_settings({"rekordbox_db": str(self.source), "rekordbox_backup_root": "", "auto_rekordbox_backup": True})
        self.work_lock = threading.RLock()
        self.now = 1793450000
        self.process = Mock(return_value={"known": True, "running": []})
        self.backups = RekordboxBackups(self.store, self.work_lock, process_checker=self.process,
                                        platform="darwin", clock=lambda: self.now)

    def tearDown(self):
        self.backups.close()
        self.temporary.cleanup()

    def assert_source_kept(self):
        self.assertEqual(self.source.read_bytes(), self.original)
        self.assertEqual(self.companion.read_bytes(), self.companion_bytes)

    def test_new_snapshot_is_byte_verified_with_manifest_and_no_source_writes(self):
        actual_open = os.open
        source_opens = []

        def traced_open(path, flags, *args, **kwargs):
            directory_fd = kwargs.get("dir_fd")
            if str(path) in ("master.db", "masterPlaylists6.xml") and directory_fd is not None:
                if os.fstat(directory_fd).st_ino == self.library.stat().st_ino:
                    source_opens.append(flags)
            return actual_open(path, flags, *args, **kwargs)

        # supported checks compare os.open identity with os.supports_dir_fd.
        with patch("rekordbox_backups.os.open", side_effect=traced_open) as patched_open:
            with patch("rekordbox_backups.os.supports_dir_fd", os.supports_dir_fd | {patched_open}):
                result = self.backups.create()
        self.assertTrue(result["created"])
        self.assertFalse(result["unchanged"])
        record = result["backup"]
        self.assertEqual(record["verification"], "verified")
        self.assertTrue(record["completed"])
        self.assertEqual(record["scope"], "database_snapshot")
        self.assertEqual((Path(record["path"]) / "master.db").read_bytes(), self.original)
        self.assertEqual((Path(record["path"]) / "masterPlaylists6.xml").read_bytes(), self.companion_bytes)
        manifest = json.loads((Path(record["path"]) / "manifest.json").read_text())
        self.assertEqual(manifest["files"][0]["sha256"], hashlib.sha256(self.original).hexdigest())
        self.assertNotIn("logical_integrity", manifest)
        self.assert_source_kept()
        self.assertGreaterEqual(len(source_opens), 2)
        self.assertTrue(all(flags & os.O_ACCMODE == os.O_RDONLY for flags in source_opens))
        self.assertEqual(self.backups.status()["last_backup_at"], record["created_at"])

    def test_unchanged_database_reverifies_existing_snapshot_without_creating_another(self):
        first = self.backups.create()["backup"]
        self.now += 300
        result = self.backups.create()
        self.assertFalse(result["created"])
        self.assertTrue(result["unchanged"])
        self.assertEqual(result["backup"]["id"], first["id"])
        self.assertEqual(len(self.backups.status()["backups"]), 1)
        self.assert_source_kept()

    def test_changed_database_and_companion_create_new_versions_without_deleting_old(self):
        first = self.backups.create()["backup"]
        self.source.write_bytes(self.original + b"new")
        self.now += 60
        second = self.backups.create()["backup"]
        self.companion.write_bytes(b"second playlist snapshot")
        self.now += 60
        third = self.backups.create()["backup"]
        self.assertEqual(len({first["id"], second["id"], third["id"]}), 3)
        self.assertEqual((Path(first["path"]) / "master.db").read_bytes(), self.original)
        self.assertEqual(len(list((self.store.directory / "rekordbox-backups").iterdir())), 3)
        self.assertEqual(len(self.backups.status()["backups"]), 3)

    def test_optional_companion_is_not_required(self):
        self.companion.unlink()
        result = self.backups.create()["backup"]
        self.assertEqual([item["name"] for item in result["files"]], ["master.db"])
        self.assertTrue(self.backups.verify(result["id"])["verified"])

    def test_running_or_unknown_processes_block_copy_without_source_changes(self):
        for state in ({"known": True, "running": ["Rekordbox"]}, {"known": False, "running": []}, None):
            self.process.return_value = state
            with self.assertRaises(AppError):
                self.backups.create()
            self.assertFalse(self.backups.status()["running"])
            self.assertEqual(self.backups.status()["backups"], [])
            self.assert_source_kept()
        self.assertFalse((self.store.directory / "rekordbox-backups").exists())

    def test_nonempty_wal_and_journal_are_preserved_and_prevent_snapshot(self):
        for suffix in ("-wal", "-journal"):
            path = self.source.with_name("master.db" + suffix)
            path.write_bytes(b"transaction data must not be discarded")
            with self.assertRaisesRegex(AppError, "journal still contains"):
                self.backups.create()
            self.assertEqual(path.read_bytes(), b"transaction data must not be discarded")
            self.assert_source_kept()
            path.unlink()  # Test cleanup only; the service has no deletion path.
        self.assertFalse(self.backups.status()["backups"])

    def test_zero_journal_is_kept_and_sidecar_symlinks_are_rejected(self):
        journal = self.library / "master.db-journal"
        journal.write_bytes(b"")
        self.assertTrue(self.backups.create()["created"])
        self.assertTrue(journal.exists())
        journal.unlink()
        journal.symlink_to(self.source)
        with self.assertRaises(AppError):
            self.backups.create()
        self.assertTrue(journal.is_symlink())
        self.assert_source_kept()

    def test_source_changes_during_copy_leave_an_incomplete_unusable_backup(self):
        copy = self.backups._copy

        def changed(*args, **kwargs):
            result = copy(*args, **kwargs)
            if args[2] == "master.db":
                self.source.write_bytes(self.original + b"external change")
            return result

        with patch.object(self.backups, "_copy", side_effect=changed):
            with self.assertRaisesRegex(AppError, "changed"):
                self.backups.create()
        record = self.backups.status()["backups"][0]
        self.assertEqual(record["verification"], "failed")
        self.assertFalse(record["completed"])
        self.assertFalse(self.backups.verify(record["id"])["verified"])
        self.assertEqual(self.source.read_bytes(), self.original + b"external change")

    def test_same_size_same_mtime_replacement_is_rejected(self):
        before = self.source.stat()
        original_copy = self.backups._copy

        def replaced(*args, **kwargs):
            result = original_copy(*args, **kwargs)
            if args[2] == "master.db":
                alternate = self.library / "replacement.db"
                alternate.write_bytes(self.original[::-1])
                os.utime(alternate, ns=(before.st_atime_ns, before.st_mtime_ns))
                alternate.replace(self.source)
            return result

        with patch.object(self.backups, "_copy", side_effect=replaced):
            with self.assertRaisesRegex(AppError, "changed"):
                self.backups.create()
        self.assertFalse(self.backups.status()["backups"][0]["completed"])

    def test_app_launch_or_journal_creation_during_copy_rejects_snapshot(self):
        copy = self.backups._copy

        def launched(*args, **kwargs):
            result = copy(*args, **kwargs)
            self.process.return_value = {"known": True, "running": ["CueGen"]}
            return result

        with patch.object(self.backups, "_copy", side_effect=launched):
            with self.assertRaisesRegex(AppError, "Close Rekordbox"):
                self.backups.create()
        self.assert_source_kept()
        self.process.return_value = {"known": True, "running": []}

        def journal_created(*args, **kwargs):
            result = copy(*args, **kwargs)
            (self.library / "master.db-wal").write_bytes(b"new transaction")
            return result

        with patch.object(self.backups, "_copy", side_effect=journal_created):
            with self.assertRaisesRegex(AppError, "journal"):
                self.backups.create()
        self.assertEqual((self.library / "master.db-wal").read_bytes(), b"new transaction")
        self.assertTrue(all(not record["completed"] for record in self.backups.status()["backups"]))

    def test_source_symlink_parent_symlink_and_fifo_are_rejected_without_opening(self):
        linked = self.root / "Alias"
        linked.symlink_to(self.library, target_is_directory=True)
        self.store.put_settings({"rekordbox_db": str(linked / "master.db")})
        with self.assertRaises(AppError):
            self.backups.create()
        self.store.put_settings({"rekordbox_db": str(self.source)})
        self.source.rename(self.library / "original.db")
        self.source.symlink_to(self.library / "original.db")
        with self.assertRaises(AppError):
            self.backups.create()
        self.source.unlink()
        os.mkfifo(self.source)
        with self.assertRaises(AppError):
            self.backups.create()
        self.assertEqual((self.library / "original.db").read_bytes(), self.original)

    def test_source_or_companion_hardlinks_cannot_drop_the_source_lock(self):
        alias = self.root / "hardlinked-source.db"
        os.link(self.source, alias)
        with self.assertRaisesRegex(AppError, "hardlinks"):
            self.backups.create()
        alias.unlink()
        self.companion.unlink()
        os.link(self.source, self.companion)
        with self.assertRaisesRegex(AppError, "hardlinks"):
            self.backups.create()
        self.assertEqual(self.source.read_bytes(), self.original)
        self.assertFalse((self.store.directory / "rekordbox-backups").exists())

    def test_destination_source_folder_ancestor_and_symlink_are_rejected(self):
        for destination in (self.library, self.library / "Backups", self.root):
            self.store.put_settings({"rekordbox_backup_root": str(destination)})
            with self.assertRaisesRegex(AppError, "separate folder"):
                self.backups.create()
        alias = self.root / "Backup Alias"
        alias.symlink_to(self.library, target_is_directory=True)
        self.store.put_settings({"rekordbox_backup_root": str(alias)})
        with self.assertRaises(AppError):
            self.backups.create()
        self.assert_source_kept()
        self.assertEqual(sorted(p.name for p in self.library.iterdir()), ["master.db", "masterPlaylists6.xml"])

    def test_existing_snapshot_directory_is_never_overwritten(self):
        with patch("rekordbox_backups.secrets.token_hex", return_value="a" * 16):
            first = self.backups.create()["backup"]
            self.source.write_bytes(self.original + b"changed")
            with self.assertRaises(AppError):
                self.backups.create()
        self.assertEqual((Path(first["path"]) / "master.db").read_bytes(), self.original)
        self.assertEqual(len(self.backups.status()["backups"]), 1)

    def test_changed_source_or_backup_folder_is_rejected_without_deleting_files(self):
        copy = self.backups._copy
        for target in (self.library, self.store.directory / "rekordbox-backups"):
            moved = target.with_name(target.name + "-moved")
            changed = []

            def renamed(*args, **kwargs):
                result = copy(*args, **kwargs)
                if args[2] == "master.db" and not changed:
                    target.rename(moved)
                    target.mkdir()
                    changed.append(True)
                return result

            with patch.object(self.backups, "_copy", side_effect=renamed):
                with self.assertRaises(AppError):
                    self.backups.create()
            self.assertTrue(moved.exists())
            self.assertFalse(any(row["completed"] for row in self.backups.status()["backups"]))
            target.rmdir()  # Only the empty replacement made by this test.
            moved.rename(target)
            self.assert_source_kept()

    def test_verification_detects_tampered_files_and_never_opens_the_source(self):
        record = self.backups.create()["backup"]
        actual_open = os.open

        def forbid_source(path, *args, **kwargs):
            parent = kwargs.get("dir_fd")
            if str(path) == "master.db" and parent is not None and os.fstat(parent).st_ino == self.library.stat().st_ino:
                raise AssertionError("Verification opened the live source")
            return actual_open(path, *args, **kwargs)

        with patch("rekordbox_backups.os.open", side_effect=forbid_source) as opened:
            with patch("rekordbox_backups.os.supports_dir_fd", os.supports_dir_fd | {opened}):
                self.assertTrue(self.backups.verify(record["id"])["verified"])
        (Path(record["path"]) / "master.db").write_bytes(self.original[::-1])
        result = self.backups.verify(record["id"])
        self.assertFalse(result["verified"])
        self.assertEqual(self.backups.status()["backups"][0]["verification"], "failed")
        self.assert_source_kept()

    def test_manifest_tampering_and_missing_backup_cannot_be_reported_as_verified(self):
        record = self.backups.create()["backup"]
        (Path(record["path"]) / "manifest.json").write_text("{}")
        self.assertFalse(self.backups.verify(record["id"])["verified"])
        Path(record["path"]).rename(Path(record["path"]).with_name("missing-snapshot"))
        self.assertFalse(self.backups.verify(record["id"])["verified"])
        self.assert_source_kept()

    def test_tampered_previous_snapshot_causes_new_copy_instead_of_unchanged_skip(self):
        record = self.backups.create()["backup"]
        (Path(record["path"]) / "master.db").write_bytes(b"bad")
        self.now += 30
        next_copy = self.backups.create()
        self.assertTrue(next_copy["created"])
        self.assertNotEqual(next_copy["backup"]["id"], record["id"])
        self.assertEqual(len(self.backups.status()["backups"]), 2)
        self.assertEqual((Path(record["path"]) / "master.db").read_bytes(), b"bad")
        self.assert_source_kept()

    def test_backup_file_hardlinks_and_symlinks_are_rejected(self):
        record = self.backups.create()["backup"]
        copied = Path(record["path"]) / "master.db"
        alias = self.root / "hardlinked.db"
        os.link(copied, alias)
        self.assertFalse(self.backups.verify(record["id"])["verified"])
        copied.unlink()
        copied.symlink_to(self.source)
        self.assertFalse(self.backups.verify(record["id"])["verified"])
        self.assert_source_kept()

    def test_due_checks_persist_and_skip_unchanged_backup_until_next_day(self):
        self.assertTrue(self.backups.tick()["created"])
        calls = self.process.call_count
        self.now += INTERVAL_SECONDS - 1
        self.assertIsNone(self.backups.tick())
        self.assertEqual(self.process.call_count, calls)
        self.now += 2
        self.assertTrue(self.backups.tick()["unchanged"])
        calls = self.process.call_count
        self.backups.close()
        self.backups = RekordboxBackups(self.store, self.work_lock, self.process, clock=lambda: self.now, platform="darwin")
        self.assertIsNone(self.backups.tick())
        self.assertEqual(self.process.call_count, calls)
        self.assertEqual(len(self.backups.status()["backups"]), 1)

    def test_first_automatic_failure_is_visible_only_for_the_attempted_paths(self):
        self.process.return_value = {"known": True, "running": ["Rekordbox"]}
        self.assertIsNone(self.backups.tick())
        failed = self.backups.status()
        self.assertIn("Close Rekordbox", failed["message"])
        self.assertEqual(failed["message"], failed["error"])
        self.assertIsNone(failed["last_backup_at"])
        self.store.put_settings({"rekordbox_db": str(self.root / "Other library" / "master.db")})
        changed = self.backups.status()
        self.assertEqual(changed["error"], "")
        self.assertNotIn("Close Rekordbox, CueGen", changed["message"])
        self.assertIn("first backup", changed["message"])
        self.store.put_settings({"rekordbox_db": str(self.source), "rekordbox_backup_root": str(self.root / "Other backups")})
        self.assertEqual(self.backups.status()["error"], "")
        self.store.put_settings({"mode": "demo"})
        self.assertIsNone(self.backups.status()["last_attempt_config"])
        self.assertIsNone(self.backups.status()["last_config"])

    def test_returning_to_previously_saved_paths_does_not_show_another_path_error(self):
        self.backups.create()
        self.store.put_settings({"rekordbox_backup_root": str(self.library)})
        self.assertIsNone(self.backups.tick())
        self.assertIn("separate folder", self.backups.status()["message"])
        self.store.put_settings({"rekordbox_backup_root": ""})
        result = self.backups.status()
        self.assertEqual(result["error"], "")
        self.assertNotIn("separate folder", result["message"])
        self.assertIsNotNone(result["last_backup_at"])

    def test_disabled_demo_unconfigured_and_closed_scheduler_do_not_probe_source(self):
        with patch("rekordbox_backups._directory") as directory:
            for changes in ({"auto_rekordbox_backup": False}, {"auto_rekordbox_backup": True, "mode": "demo"},
                            {"mode": "real", "rekordbox_db": ""}):
                self.store.put_settings(changes)
                self.assertIsNone(self.backups.tick())
            self.store.put_settings({"rekordbox_db": str(self.source), "mode": "demo"})
            status = self.backups.status()
            self.assertEqual(status["source_path"], "")
            self.assertEqual(status["backups"], [])
            with self.assertRaises(AppError):
                self.backups.create()
            with self.assertRaises(AppError):
                self.backups.verify("20261031T120000Z-" + "a" * 16)
            directory.assert_not_called()
        self.process.assert_not_called()
        self.store.put_settings({"mode": "real"})
        self.backups.close()
        self.assertIsNone(self.backups.tick())

    def test_missing_ssd_never_falls_back_or_removes_prior_backup(self):
        first = self.backups.create()["backup"]
        self.library.rename(self.root / "Disconnected")
        self.now += INTERVAL_SECONDS + 1
        self.assertIsNone(self.backups.tick())
        self.assertEqual(len(self.backups.status()["backups"]), 1)
        self.assertTrue((Path(first["path"]) / "master.db").exists())
        self.assertFalse(self.backups.status()["running"])
        (self.root / "Disconnected").rename(self.library)
        self.assertTrue(self.backups.tick()["unchanged"])

    def test_low_disk_and_copy_failure_never_delete_previous_backups(self):
        first = self.backups.create()["backup"]
        self.source.write_bytes(self.original + b"new version")
        disk = Mock(f_bavail=0, f_frsize=4096)
        with patch("rekordbox_backups.os.fstatvfs", return_value=disk):
            with self.assertRaisesRegex(AppError, "free space"):
                self.backups.create()
        with patch.object(self.backups, "_copy", side_effect=OSError("disk error")):
            with self.assertRaises(AppError):
                self.backups.create()
        self.assertEqual((Path(first["path"]) / "master.db").read_bytes(), self.original)
        self.assertEqual(self.source.read_bytes(), self.original + b"new version")
        self.assertTrue(any(not row["completed"] for row in self.backups.status()["backups"]))

    def test_record_save_failure_clears_running_and_preserves_existing_backup(self):
        first = self.backups.create()["backup"]
        with patch.object(self.backups, "_save", side_effect=sqlite3.OperationalError("database is full")):
            with self.assertRaisesRegex(AppError, "record the backup verification"):
                self.backups.verify(first["id"])
            self.assertFalse(self.backups.status()["running"])
            self.source.write_bytes(self.original + b"changed")
            with self.assertRaises(AppError):
                self.backups.create()
            self.assertFalse(self.backups.status()["running"])
        self.assertEqual((Path(first["path"]) / "master.db").read_bytes(), self.original)
        self.assertEqual(self.source.read_bytes(), self.original + b"changed")

    def test_failed_initial_state_write_never_leaves_running_true(self):
        first = self.backups.create()["backup"]
        connect = self.store.connect

        @contextmanager
        def read_only_djlist():
            with connect() as database:
                database.execute("PRAGMA query_only=ON")
                yield database

        with patch.object(self.store, "connect", side_effect=read_only_djlist):
            for operation in (self.backups.create, lambda: self.backups.verify(first["id"])):
                with self.assertRaises(AppError):
                    operation()
                self.assertFalse(self.backups.status()["running"])
        self.assertTrue(self.backups.verify(first["id"])["verified"])
        self.assert_source_kept()

    def test_source_and_companion_caps_stop_before_copying(self):
        with patch("rekordbox_backups.MAX_DATABASE_BYTES", len(self.original) - 1):
            with self.assertRaisesRegex(AppError, "too large"):
                self.backups.create()
        with patch("rekordbox_backups.MAX_COMPANION_BYTES", len(self.companion_bytes) - 1):
            with self.assertRaisesRegex(AppError, "too large"):
                self.backups.create()
        self.assertFalse((self.store.directory / "rekordbox-backups").exists())
        self.assert_source_kept()

    def test_reentrant_settings_or_demo_change_cannot_finish_a_snapshot(self):
        copy = self.backups._copy

        def changed(*args, **kwargs):
            result = copy(*args, **kwargs)
            self.store.put_settings({"mode": "demo"})
            return result

        with patch.object(self.backups, "_copy", side_effect=changed):
            with self.assertRaisesRegex(AppError, "settings changed"):
                self.backups.create()
        self.store.put_settings({"mode": "real"})
        self.assertFalse(self.backups.status()["backups"][0]["completed"])
        self.assert_source_kept()

    def test_other_library_work_blocks_backup_and_lock_is_released_after_failure(self):
        entered, release = threading.Event(), threading.Event()

        def occupy():
            with self.work_lock:
                entered.set()
                release.wait(3)

        thread = threading.Thread(target=occupy)
        thread.start()
        entered.wait(2)
        try:
            with self.assertRaisesRegex(AppError, "Another library action"):
                self.backups.create()
        finally:
            release.set()
            thread.join(3)
        self.assertTrue(self.backups.create()["created"])

    def test_read_lock_blocks_a_real_sqlite_writer_until_snapshot_finishes(self):
        self.source.unlink()
        with sqlite3.connect(self.source) as database:
            database.execute("CREATE TABLE example(value TEXT)")
            database.execute("INSERT INTO example VALUES ('original')")
        source_bytes = self.source.read_bytes()
        copy = self.backups._copy
        attempts = []
        code = "import sqlite3,sys\nc=sqlite3.connect(sys.argv[1],timeout=0)\ntry:\n c.execute(\"INSERT INTO example VALUES ('writer')\"); c.commit(); print('wrote')\nexcept sqlite3.OperationalError:\n print('blocked')\nfinally:\n c.close()\n"

        def concurrent_writer(*args, **kwargs):
            if args[2] == "master.db":
                attempt = subprocess.run([sys.executable, "-c", code, str(self.source)], capture_output=True, text=True, timeout=5)
                attempts.append(attempt.stdout.strip())
            return copy(*args, **kwargs)

        with patch.object(self.backups, "_copy", side_effect=concurrent_writer):
            result = self.backups.create()
        self.assertEqual(attempts, ["blocked"])
        self.assertTrue(result["created"])
        self.assertEqual(self.source.read_bytes(), source_bytes)
        later = subprocess.run([sys.executable, "-c", code, str(self.source)], capture_output=True, text=True, timeout=5)
        self.assertEqual(later.stdout.strip(), "wrote")


if __name__ == "__main__":
    unittest.main()
