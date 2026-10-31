"""Regressions for the reported Spotify/settings lock and acquisition state."""
from contextlib import contextmanager
import threading
import unittest
from unittest.mock import patch

from app import DEFAULTS, Store
from spotify_api import AppError
from test_backend import Fixture, SOURCE, track


class SettingsDuringActivityTests(Fixture):
    @contextmanager
    def background(self, key="scan", label="Scanning music files"):
        entered, release = threading.Event(), threading.Event()
        def run():
            with self.application.activity.track(key, label), self.application.work_lock:
                self.application.activity.update(phase="Reading audio tags", completed=3, total=10, unit="files")
                entered.set()
                release.wait(5)
        worker = threading.Thread(target=run)
        worker.start()
        self.assertTrue(entered.wait(2))
        try:
            yield
        finally:
            release.set()
            worker.join(3)
            self.assertFalse(worker.is_alive())

    def test_unchanged_full_form_and_spotify_change_do_not_wait_for_disk_scan(self):
        settings = {key: self.application.store.settings()[key] for key in DEFAULTS}
        with self.background():
            self.assertTrue(self.application.change_settings(settings)["unchanged"])
            self.assertTrue(self.application.change_settings({**settings, "spotify_client_id": "b" * 32})["saved"])
            activity = self.application.activity_status()
            self.assertTrue(activity["running"])
            self.assertEqual(activity["active"][0]["label"], "Scanning music files")
            self.assertEqual((activity["active"][0]["completed"], activity["active"][0]["total"]), (3, 10))
        self.assertEqual(self.application.store.settings()["spotify_client_id"], "b" * 32)
        self.assertFalse(self.application.activity_status()["running"])
        self.assertEqual(self.application.activity_status()["recent"][0]["status"], "finished")

    def test_folder_change_names_the_running_action_and_preserves_settings(self):
        before = self.application.store.settings()
        with self.background(), self.assertRaises(AppError) as error:
            self.application.change_settings({"music_root": str(self.root / "Other")})
        self.assertEqual(error.exception.status, 409)
        self.assertIn("Scanning music files", str(error.exception))
        self.assertIn("Activity", str(error.exception))
        self.assertEqual(self.application.store.settings(), before)

    def test_spotify_change_waits_for_an_actual_spotify_refresh(self):
        with self.background("spotify", "Refreshing the Spotify playlist"), self.assertRaises(AppError) as error:
            self.application.change_settings({"spotify_client_id": "b" * 32})
        self.assertEqual(error.exception.status, 409)
        self.assertIn("Spotify", str(error.exception))
        self.assertEqual(self.application.store.settings()["spotify_client_id"], "a" * 32)

    def test_equal_numeric_value_cannot_bypass_boolean_validation(self):
        with self.background(), self.assertRaises(AppError):
            self.application.change_settings({"auto_store_lookup": 1})
        self.assertIs(self.application.store.settings()["auto_store_lookup"], True)

    def test_automatic_backup_is_visible_even_without_manual_activity(self):
        with patch.object(self.application.backups, "status", return_value={"running": True, "message": "Copying database"}):
            status = self.application.activity_status()
        self.assertTrue(status["settings_blocked"])
        self.assertEqual(status["active"][0]["key"], "backup")
        self.assertEqual(status["active"][0]["phase"], "Copying database")

    def test_recorded_price_survives_sync_restart_and_rejects_invalid_values(self):
        self.application.edit_track(self.identity, {"purchase_price": "0.00", "purchase_currency": "GBP", "owned": True, "prep_status": "bought"})
        self.application.store.upsert([track(title="Refreshed title")], SOURCE)
        saved = Store(self.root / "data").rows("real")[0]
        self.assertEqual((saved["purchase_price"], saved["purchase_currency"], saved["prep_status"], saved["owned"]), ("0.00", "GBP", "bought", True))
        for field, value in [("purchase_price", "-1"), ("purchase_price", "1.234"), ("purchase_price", "NaN"), ("purchase_currency", "gbp")]:
            with self.subTest(field=field, value=value), self.assertRaises(AppError):
                self.application.edit_track(self.identity, {field: value})
        self.assertEqual(self.current()["purchase_price"], "0.00")


if __name__ == "__main__":
    unittest.main()
