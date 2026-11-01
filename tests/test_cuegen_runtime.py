"""Component provisioning uses a tiny synthetic archive, never a real launch."""
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import Store
from cue_planner import CuePlannerError
import cuegen_runtime as runtime_module
from cuegen_runtime import CueGenRuntime, unpack_release, _approved_url, _ReleaseRedirect


class CueGenRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.home = self.root / "Home"
        (self.home / "Downloads").mkdir(parents=True)
        self.store = Store(self.root / "DJList")
        self.store.put_settings({"mode": "real"})
        self.binary = b"\xcf\xfa\xed\xfe" + b"synthetic pinned runtime; not executable" * 40
        self.archive = self.archive_for("CueGen.Console", self.binary)
        self.pin = patch.dict(runtime_module.RELEASE, {
            "archive_size": len(self.archive), "archive_sha256": hashlib.sha256(self.archive).hexdigest(),
            "executable_size": len(self.binary), "executable_sha256": hashlib.sha256(self.binary).hexdigest(),
        })
        self.pin.start()
        self.downloads = []
        self.runtime = CueGenRuntime(self.store, platform="darwin", downloader=self.download, home=self.home)

    def tearDown(self):
        self.pin.stop()
        self.temp.cleanup()

    @staticmethod
    def archive_for(name, content, attributes=None, extra=False):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            info = zipfile.ZipInfo(name)
            if attributes is not None:
                info.external_attr = attributes << 16
            archive.writestr(info, content)
            if extra:
                archive.writestr("unexpected.txt", b"unexpected")
        return buffer.getvalue()

    def download(self, progress):
        self.downloads.append(True)
        return self.archive

    def assert_code(self, code, action):
        with self.assertRaises(CuePlannerError) as result:
            action()
        self.assertEqual(result.exception.code, code)

    def test_first_use_installs_exact_component_and_repeat_stays_offline(self):
        path = self.runtime.prepare()
        self.assertEqual(path.read_bytes(), self.binary)
        self.assertEqual(path, self.runtime.path)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700)
        self.assertEqual(path.stat().st_nlink, 1)
        manifest = json.loads((path.parent / "manifest.json").read_text())
        self.assertEqual(manifest["sha256"], runtime_module.RELEASE["executable_sha256"])
        self.assertEqual(manifest["source"], "official pinned release")
        for name in runtime_module.NOTICE_FILES:
            content = (path.parent / name).read_bytes()
            self.assertEqual(content, (Path(runtime_module.__file__).resolve().parent / "vendor" / "cuegen" / name).read_bytes())
            self.assertEqual(manifest["notices"][name], hashlib.sha256(content).hexdigest())
        self.assertTrue(self.runtime.status()["installed"])
        self.assertEqual(self.runtime.prepare(), path)
        self.assertEqual(len(self.downloads), 1)
        self.assertEqual(list(self.runtime.root.glob("install-*")), [])

    def test_reuses_old_download_only_with_exact_pinned_bytes(self):
        prior = self.home / "Downloads" / "CueGen.Console"
        prior.write_bytes(self.binary)
        original = prior.read_bytes()
        installed = self.runtime.prepare()
        self.assertEqual(installed.read_bytes(), original)
        self.assertEqual(prior.read_bytes(), original)
        self.assertNotEqual(prior.stat().st_ino, installed.stat().st_ino)
        self.assertEqual(self.downloads, [])

    def test_unknown_prior_version_is_preserved_and_never_executed(self):
        prior = self.home / "Downloads" / "CueGen.Console"
        prior.write_bytes(b"unknown prior version")
        self.runtime.prepare()
        self.assertEqual(prior.read_bytes(), b"unknown prior version")
        self.assertEqual(len(self.downloads), 1)

    def test_explicit_existing_path_is_only_a_verified_import_source(self):
        prior = self.root / "PriorCueGen"
        prior.write_bytes(self.binary)
        self.store.put_settings({"cuegen_path": str(prior)})
        self.runtime.prepare()
        self.assertEqual(self.downloads, [])
        self.assertEqual(self.store.settings()["cuegen_path"], str(prior))

    def test_bad_download_hash_never_installs(self):
        self.runtime.downloader = lambda progress: self.archive[:-1] + b"!"
        self.assert_code("cuegen_checksum", self.runtime.prepare)
        self.assertFalse(self.runtime.path.exists())
        self.assertFalse(self.runtime.status()["installed"])

    def test_notice_failure_prevents_publishing_an_incomplete_component(self):
        with patch.object(self.runtime, "_copy_notices", side_effect=OSError("missing fixture notice")):
            self.assert_code("cuegen_setup", self.runtime.prepare)
        self.assertFalse(self.runtime.path.exists())
        self.assertEqual(list(self.runtime.root.glob("install-*")), [])

    def test_archive_allowlist_rejects_traversal_links_and_extra_files(self):
        for data in (self.archive_for("../CueGen.Console", self.binary),
                     self.archive_for("CueGen.Console", self.binary, stat.S_IFLNK | 0o777),
                     self.archive_for("CueGen.Console", self.binary, extra=True)):
            with self.subTest(data=len(data)):
                with patch.dict(runtime_module.RELEASE, {"archive_size": len(data), "archive_sha256": hashlib.sha256(data).hexdigest()}):
                    self.assert_code("cuegen_checksum", lambda: unpack_release(data))

    def test_extracted_binary_gets_its_own_checksum(self):
        data = self.archive_for("CueGen.Console", self.binary[:-1] + b"!")
        with patch.dict(runtime_module.RELEASE, {"archive_size": len(data), "archive_sha256": hashlib.sha256(data).hexdigest()}):
            self.assert_code("cuegen_checksum", lambda: unpack_release(data))

    def test_mutated_install_is_not_overwritten_or_used(self):
        installed = self.runtime.prepare()
        installed.write_bytes(self.binary[:-1] + b"!")
        self.assertFalse(self.runtime.status()["installed"])
        self.assertIn("failed verification", self.runtime.status()["message"])
        self.assert_code("cuegen_checksum", self.runtime.prepare)
        self.assertEqual(installed.read_bytes(), self.binary[:-1] + b"!")
        self.assertEqual(len(self.downloads), 1)

    def test_installation_symlink_cannot_redirect_write(self):
        elsewhere = self.root / "Elsewhere"
        elsewhere.mkdir()
        (self.store.directory / "engines").symlink_to(elsewhere, target_is_directory=True)
        with self.assertRaises(CuePlannerError):
            self.runtime.prepare()
        self.assertEqual(list(elsewhere.iterdir()), [])

    def test_prior_symlink_and_hardlink_are_ignored(self):
        actual = self.root / "actual"
        actual.write_bytes(self.binary)
        prior = self.home / "Downloads" / "CueGen.Console"
        prior.symlink_to(actual)
        self.assertIsNone(self.runtime._existing_binary())
        prior.unlink()
        os.link(actual, prior)
        self.assertIsNone(self.runtime._existing_binary())

    def test_status_is_read_only_and_demo_or_wrong_os_never_downloads(self):
        self.assertFalse(self.runtime.status()["installed"])
        self.assertFalse(self.runtime.root.exists())
        self.store.put_settings({"mode": "demo"})
        self.assert_code("demo_mode", self.runtime.prepare)
        self.assertIn("demo", self.runtime.status()["message"])
        self.store.put_settings({"mode": "real"})
        self.runtime.platform = "linux"
        self.assert_code("cuegen_setup", self.runtime.prepare)
        self.assertEqual(self.downloads, [])

    def test_existing_unidentified_directory_is_not_replaced(self):
        self.runtime.path.parent.mkdir(parents=True)
        unknown = self.runtime.path.parent / "keep.txt"
        unknown.write_text("preserve")
        self.assert_code("cuegen_checksum", self.runtime.prepare)
        self.assertEqual(unknown.read_text(), "preserve")

    def test_redirects_require_approved_https_origin(self):
        for bad in ("http://github.com/a", "https://evil.invalid/a", "https://github.com:444/a",
                    "https://user:password@github.com/a", "file:///tmp/archive"):
            self.assertFalse(_approved_url(bad))
            self.assert_code("cuegen_download", lambda: _ReleaseRedirect().redirect_request(None, None, 302, "", {}, bad))
        self.assertTrue(_approved_url("https://release-assets.githubusercontent.com/a?sig=not-logged"))


if __name__ == "__main__":
    unittest.main()
