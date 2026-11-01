"""HTTP boundaries for library maintenance; no live database or external audio."""
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import Application, Handler
from cue_planner import CuePlannerError


class Stream:
    def __init__(self, request):
        self.input, self.output = io.BytesIO(request), io.BytesIO()

    def makefile(self, mode, *args):
        return self.input if "r" in mode else self.output

    def sendall(self, data):
        self.output.write(data)


class LibraryToolRouteTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.app = Application(Path(self.temporary.name) / "data")

    def tearDown(self):
        self.app.close()
        self.temporary.cleanup()

    def request(self, path, payload=None, headers=None):
        method = "GET" if payload is None else "POST"
        data = b"" if payload is None else json.dumps(payload).encode()
        fields = {"Host": "127.0.0.1:8888", "Origin": self.app.origin,
                  "Content-Type": "application/json", "Content-Length": str(len(data)),
                  "X-CSRF-Token": self.app.csrf_token, **(headers or {})}
        raw = method + " " + path + " HTTP/1.1\r\n" + "".join(k + ": " + v + "\r\n" for k, v in fields.items()) + "\r\n"
        stream = Stream(raw.encode() + data)
        Handler(stream, ("127.0.0.1", 10001), type("Server", (), {"app": self.app})())
        head, body = stream.output.getvalue().split(b"\r\n\r\n", 1)
        return int(head.split()[1]), head, body

    def test_new_read_routes_keep_host_and_origin_boundaries(self):
        for route in ("/api/backups", "/api/library-audit", "/api/library-audit.csv", "/api/activity",
                      "/api/file-operations", "/api/cuegen/status", "/api/rekordbox-relocation", "/api/library-audio?entry_id=" + "a" * 24):
            for invalid in ({"Host": "outside.example"}, {"Origin": "https://outside.example"}):
                with self.subTest(route=route, invalid=invalid):
                    self.assertEqual(self.request(route, headers=invalid)[0], 403)

    def test_exact_action_payloads_reject_unexpected_fields_before_file_work(self):
        actions = {"/api/backups/create": {}, "/api/backups/verify": {"backup_id": "x"},
                   "/api/duplicates/verify": {"group_id": "x"}, "/api/missing/preview": {"track_id": 1},
                   "/api/missing/apply": {"token": "x", "entry_id": "x"},
                   "/api/cues/plan": {"entry_id": "x"}, "/api/cues/export": {"token": "x", "selected_ids": []},
                   "/api/cuegen/prepare": {}, "/api/rekordbox-relocation/verify": {},
                   "/api/file-operations/preview": {"action": "rename", "entry_ids": []},
                   "/api/file-operations/apply": {"token": "x", "selected_ids": []},
                   "/api/file-operations/undo-preview": {"operation_id": "x"},
                   "/api/file-operations/undo": {"token": "x", "selected_ids": []},
                   "/api/metadata/preview": {"entry_ids": []}, "/api/metadata/reset-preview": {"entry_ids": []},
                   "/api/metadata/apply": {"token": "x", "selected_ids": []},
                   "/api/metadata/reset": {"token": "x", "selected_ids": []}}
        with patch.object(self.app, "library_task") as work:
            for route, payload in actions.items():
                with self.subTest(route=route):
                    self.assertEqual(self.request(route, {**payload, "arbitrary_path": "/outside"})[0], 400)
            work.assert_not_called()
        for route in ("/api/library-audit?kind=missing&kind=offline", "/api/library-audit?path=/outside",
                      "/api/library-audit?page=no", "/api/library-audit.csv?page=2"):
            self.assertEqual(self.request(route)[0], 400)

    def test_busy_library_work_fails_before_backup_or_cue_access(self):
        entered, release = threading.Event(), threading.Event()

        def holding():
            with self.app.work_lock:
                entered.set()
                release.wait(3)

        worker = threading.Thread(target=holding)
        worker.start()
        self.assertTrue(entered.wait(1))
        try:
            with patch.object(self.app.backups, "create") as backup, patch.object(self.app.cue_planner, "plan") as cue:
                self.assertEqual(self.request("/api/backups/create", {})[0], 409)
                self.assertEqual(self.request("/api/cues/plan", {"entry_id": "x"})[0], 409)
                backup.assert_not_called()
                cue.assert_not_called()
        finally:
            release.set()
            worker.join(3)

    def test_cue_errors_expose_review_code_and_exports_keep_binary_xml_filename(self):
        with patch.object(self.app.cue_planner, "plan", side_effect=CuePlannerError("Confirm checked values.", "manual_grid_required")):
            status, _, body = self.request("/api/cues/plan", {"entry_id": "x"})
            self.assertEqual(status, 409)
            self.assertEqual(json.loads(body)["code"], "manual_grid_required")
        filename = "DJList-cue-review-01234567-89abcdef.xml"
        payload = b'<?xml version="1.0"?><DJ_PLAYLISTS/>'
        with patch.object(self.app.cue_planner, "export", return_value={"filename": filename, "xml": payload, "selected_count": 1}):
            status, head, body = self.request("/api/cues/export", {"token": "review", "selected_ids": ["cue-1"]})
            self.assertEqual((status, body), (200, payload))
            self.assertIn(b"Content-Type: application/xml", head)
            self.assertIn(('filename="' + filename + '"').encode(), head)
            self.assertIn(b"Cache-Control: no-store", head)

    def test_demo_never_exposes_or_uses_real_backup_paths(self):
        self.app.store.put_settings({"mode": "demo", "rekordbox_db": "/private/example/master.db"})
        with patch.object(self.app.backups, "create") as backup:
            self.assertEqual(self.request("/api/backups/create", {})[0], 409)
            backup.assert_not_called()
        status, _, body = self.request("/api/backups")
        self.assertEqual(status, 200)
        self.assertNotIn(b"/private/example", body)
        self.assertEqual(json.loads(body)["backups"], [])
        self.assertEqual(self.request("/api/library-audit")[0], 409)
        with patch.object(Path, "is_file", side_effect=AssertionError("Demo must not probe the live database")), \
                patch.object(Path, "is_dir", side_effect=AssertionError("Demo must not probe the music root")):
            state = self.app.state()
        self.assertFalse(state["drive"]["rekordbox_db_exists"])

    def test_engine_preparation_requires_csrf_and_real_collection_before_any_install(self):
        with patch.object(self.app.cuegen, "prepare_runtime") as prepare:
            self.assertEqual(self.request("/api/cuegen/prepare", {}, {"X-CSRF-Token": "bad"})[0], 403)
            prepare.assert_not_called()
            self.app.store.put_settings({"mode": "demo"})
            self.assertEqual(self.request("/api/cuegen/prepare", {})[0], 409)
            prepare.assert_not_called()
            self.app.store.put_settings({"mode": "real"})
            prepare.return_value = {"installed": True, "runtime_check": "passed", "message": "Checked"}
            status, _, body = self.request("/api/cuegen/prepare", {})
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body)["runtime_check"], "passed")
            prepare.assert_called_once_with(progress=self.app.cuegen_progress)

    def test_rekordbox_relocation_checks_xml_and_reports_activity(self):
        def checked():
            active = self.app.activity.snapshot()["active"]
            self.assertTrue(any("renamed tracks" in item["label"] for item in active))
            return {"entries": [], "counts": {"total": 0}, "direct_database_write": False}
        with patch.object(self.app.rekordbox_relocation, "verify", side_effect=checked) as verify:
            self.assertEqual(self.request("/api/rekordbox-relocation/verify", {}, {"X-CSRF-Token": "bad"})[0], 403)
            verify.assert_not_called()
            status, _, body = self.request("/api/rekordbox-relocation/verify", {})
            self.assertEqual(status, 200)
            self.assertFalse(json.loads(body)["direct_database_write"])
            self.assertEqual(self.app.activity.snapshot()["active"], [])
            self.app.store.put_settings({"mode": "demo"})
            self.assertEqual(self.request("/api/rekordbox-relocation")[0], 409)
            self.assertEqual(self.request("/api/rekordbox-relocation/verify", {})[0], 409)
            self.assertEqual(verify.call_count, 1)


if __name__ == "__main__":
    unittest.main()
