#!/usr/bin/env python3
"""Exercise a temporary DJList process over real loopback HTTP; no vendor calls."""
from http.client import HTTPConnection
import json
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlencode


def main():
    source = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="djlist-smoke-") as temporary:
        root = Path(temporary)
        app = root / "app"
        app.mkdir()
        for name in ("app.py", "spotify_api.py", "catalog.py"):
            shutil.copy2(source / name, app / name)
        shutil.copytree(source / "web", app / "web")
        # Keep stalled startup diagnostics in the temporary test process only.
        runner = app / "smoke_runner.py"
        runner.write_text(
            "import faulthandler, sys\n"
            "faulthandler.dump_traceback_later(8)\n"
            "import app\n"
            "try:\n"
            "    sys.exit(app.main())\n"
            "finally:\n"
            "    faulthandler.cancel_dump_traceback_later()\n",
            encoding="utf-8",
        )
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        origin = "http://127.0.0.1:%d" % port
        command = [sys.executable, "-u", str(runner), "--port", str(port),
                   "--data-dir", str(root / "data"), "--no-browser"]
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

        def request(path, method="GET", body=None, token=None):
            connection = HTTPConnection("127.0.0.1", port, timeout=5)
            headers = {"Host": "127.0.0.1:%d" % port}
            payload = None
            if body is not None:
                payload = json.dumps(body).encode("utf-8")
                headers.update({"Content-Type": "application/json", "Origin": origin})
            if token:
                headers["X-CSRF-Token"] = token
            try:
                connection.request(method, path, body=payload, headers=headers)
                response = connection.getresponse()
                return response.status, response.read()
            finally:
                connection.close()

        try:
            deadline = time.monotonic() + 10
            while True:
                if process.poll() is not None:
                    raise AssertionError("DJList exited during startup: " + process.stdout.read())
                try:
                    status, body = request("/api/health")
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise AssertionError("DJList did not start within 10 seconds")
                    time.sleep(.05)
            assert status == 200, (status, body)
            health = json.loads(body)
            assert health["app"] == "DJList" and health["api_version"] == 2
            assert health["static_snapshot"] and health["build_id"]
            status, body = request("/api/state")
            assert status == 200
            state = json.loads(body)
            token = state["csrf_token"]
            assert state["version"] == health["version"]
            assert request("/api/demo", "POST", {"enabled": True}, token)[0] == 200
            state = json.loads(request("/api/state")[1])
            track_id = state["tracks"][0]["id"]
            assert request("/api/tracks/%s" % track_id, "PATCH", {"notes": "smoke record"}, token)[0] == 200
            assert json.loads(request("/api/state")[1])["tracks"][0]["notes"] == "smoke record"
            query = urlencode({"q": "Example Artist - Song (Extended Mix)"})
            status, body = request("/api/provider-search?" + query)
            assert status == 200 and len(json.loads(body)["providers"]) == 3
            status, body = request("/api/catalog-search", "POST", {"query": ""}, token)
            assert status == 400, (status, body)
            status, original = request("/app.js")
            assert status == 200 and original
            with (app / "web/app.js").open("ab") as file:
                file.write(b"\n// changed on disk during the test\n")
            assert request("/app.js")[1] == original, "Running process served assets from a different build"
            collision = subprocess.run(command, capture_output=True, text=True, timeout=8)
            assert collision.returncode != 0
            output = collision.stdout + collision.stderr
            assert "DJList" in output and "Control+C" in output, output
            print("PASS: live HTTP health, demo edits, provider route, catalog validation, startup asset snapshot and occupied-port guidance")
        except Exception:
            if process.poll() is None:
                process.terminate()
            try:
                output, _ = process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                output, _ = process.communicate(timeout=5)
            if output:
                print("Temporary DJList process output:\n" + output, file=sys.stderr)
            raise
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            if process.stdout:
                process.stdout.close()


if __name__ == "__main__":
    main()
