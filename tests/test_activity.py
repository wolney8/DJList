import sys
from pathlib import Path
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from activity import ActivityRegistry
from spotify_api import AppError


class ActivityTests(unittest.TestCase):
    def test_nested_steps_keep_one_visible_task_with_measured_progress(self):
        clock = [0]
        ledger = ActivityRegistry(clock=lambda: clock[0])
        with ledger.track("refresh", "Refreshing library"):
            with ledger.track("scan", "Scanning media"):
                ledger.update(completed=25, unit="files")
                clock[0] = 4
                visible = ledger.snapshot()["active"]
                self.assertEqual(len(visible), 1)
                self.assertEqual(visible[0]["label"], "Refreshing library")
                self.assertEqual(visible[0]["phase"], "Scanning media")
                self.assertEqual(visible[0]["completed"], 25)
                self.assertIsNone(visible[0]["total"])
                self.assertEqual(visible[0]["elapsed_seconds"], 4)
                self.assertNotIn("_started", visible[0])
            self.assertEqual(ledger.snapshot()["active"][0]["phase"], "Refreshing library")
        self.assertFalse(ledger.snapshot()["active"])
        self.assertEqual(ledger.snapshot()["recent"][0]["status"], "finished")

    def test_failure_clears_busy_and_preserves_useful_app_error(self):
        ledger = ActivityRegistry()
        with self.assertRaises(AppError):
            with ledger.track("scan", "Scanning media"):
                raise AppError("The drive disconnected.", 409)
        result = ledger.snapshot()
        self.assertEqual(result["active"], [])
        self.assertEqual(result["recent"][0]["message"], "The drive disconnected.")
        with self.assertRaises(RuntimeError):
            with ledger.track("scan", "Scanning media"):
                raise RuntimeError("secret internal detail")
        self.assertNotIn("secret", ledger.snapshot()["recent"][0]["message"])

    def test_other_thread_can_read_progress_without_waiting_for_work(self):
        ledger = ActivityRegistry()
        started = threading.Event()
        release = threading.Event()
        def work():
            with ledger.track("spotify", "Refreshing Spotify", False):
                started.set()
                release.wait(3)
        thread = threading.Thread(target=work)
        thread.start()
        try:
            self.assertTrue(started.wait(1))
            self.assertTrue(ledger.running("spotify"))
            self.assertFalse(ledger.snapshot()["active"][0]["blocks_settings"])
        finally:
            release.set()
            thread.join(2)
        self.assertFalse(ledger.running("spotify"))
