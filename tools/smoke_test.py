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
    sys.path.insert(0, str(source))
    sys.path.insert(0, str(source / "tests"))
    from app import Store
    from test_audio_metadata import flac_bytes
    with tempfile.TemporaryDirectory(prefix="djlist-smoke-") as temporary:
        root = Path(temporary)
        music = root / "Music"
        music.mkdir()
        audio = music / "Example Artist - Night Window (Original Mix).flac"
        audio.write_bytes(flac_bytes(title="Night Window (Original Mix)"))
        downloads = root / "Downloads"
        downloads.mkdir()
        purchase = downloads / "new-purchase.flac"
        purchase.write_bytes(flac_bytes(title="New Purchase (Extended Mix)"))
        original_bytes, purchased_bytes = audio.read_bytes(), purchase.read_bytes()
        xml_path = root / "library.xml"
        xml_path.write_text('<DJ_PLAYLISTS><COLLECTION Entries="1"><TRACK Name="Night Window (Original Mix)" Artist="Example Artist" '
                            'AverageBpm="172" Tonality="C minor" Genre="Jungle" TotalTime="193" Location="%s"/>'
                            '</COLLECTION></DJ_PLAYLISTS>' % audio.as_uri(), encoding="utf-8")
        store = Store(root / "data")
        store.put_settings({"music_root": str(music), "rekordbox_xml": str(xml_path), "auto_store_lookup": False,
                            "download_root": str(downloads), "auto_library_refresh": False})
        store.upsert([{"spotify_id": "fixture-track", "artists": "Example Artist", "title_and_version": "Night Window (Original Mix)",
                       "duration_ms": 193000, "album": "Fictional test release", "spotify_url": "", "isrc": "GBABC2600001"},
                      {"spotify_id": "fixture-purchase", "artists": "Example Artist", "title_and_version": "New Purchase (Extended Mix)",
                       "duration_ms": 193000, "album": "Fictional test release", "spotify_url": ""}], "fixture-source")
        real_id = store.rows("real")[0]["id"]
        purchase_id = store.rows("real")[1]["id"]
        store.patch(real_id, "real", {"notes": "Keep my notes", "tags": "my tag", "purchase_url": "https://example.test/manual-choice"})
        store.patch(purchase_id, "real", {"notes": "Keep inbox notes", "tags": "my inbox tag", "bpm": "170"})
        app = root / "app"
        app.mkdir()
        for module in source.glob("*.py"):
            shutil.copy2(module, app / module.name)
        shutil.copytree(source / "vendor", app / "vendor", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        shutil.copytree(source / "web", app / "web")
        # Keep stalled startup diagnostics in the temporary test process only.
        runner = app / "smoke_runner.py"
        runner.write_text(
            "import faulthandler, sys\n"
            "faulthandler.dump_traceback_later(8)\n"
            "import app\n"
            "from catalog import CatalogClient\n"
            "from musicbrainz import MusicBrainzClient, PURCHASE_RELATION\n"
            "sample = {'kind':'song','trackId':1,'trackName':'Night Window (Original Mix)','artistName':'Example Artist',"
            "'trackTimeMillis':193000,'trackPrice':0.99,'currency':'GBP','primaryGenreName':'Electronic',"
            "'trackViewUrl':'https://music.apple.com/gb/album/example/1?i=1'}\n"
            "app.CatalogClient = lambda: CatalogClient(fetcher=lambda query: [sample])\n"
            "recording = {'id':'00000000-0000-4000-8000-000000000001','title':'Night Window (Original Mix)','length':193000,"
            "'artist-credit':[{'name':'Example Artist','artist':{'name':'Example Artist'}}],"
            "'relations':[{'type-id':PURCHASE_RELATION,'target-type':'url','direction':'forward','ended':False,"
            "'url':{'resource':'https://example-label.bandcamp.com/track/night-window'}}]}\n"
            "app.MusicBrainzClient = lambda: MusicBrainzClient(fetcher=lambda isrc: {'isrc':isrc,'recordings':[recording]})\n"
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
            assert health["app"] == "DJList" and health["api_version"] == 5
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
            assert request("/api/demo", "POST", {"enabled": False}, token)[0] == 200
            status, body = request("/api/refresh-libraries", "POST", {"media": True, "xml": True}, token)
            libraries = json.loads(body)
            assert status == 200 and not libraries["errors"], (status, body)
            assert libraries["xml"]["entry_count"] == 1 and libraries["media"]["indexed"] == 1
            status, body = request("/api/library-review?page=1&page_size=10")
            inventory = json.loads(body)
            assert status == 200 and inventory["total"] == 1
            assert inventory["entries"][0]["bpm"] == "172"
            assert libraries["media"]["metadata_read"] == 1
            status, body = request("/api/setup-check")
            setup = json.loads(body)
            assert status == 200 and all(row["status"] == "ok" for row in setup["checks"]), (status, body)
            assert not json.loads(request("/api/library-watch")[1])["enabled"]
            assert request("/api/browse", "POST", {"kind": "music"})[0] == 403
            assert request("/api/inbox-preview", "POST", {})[0] == 403
            status, body = request("/api/inbox-preview", "POST", {}, token)
            inbox = json.loads(body)
            assert status == 200 and inbox["total"] == 1, (status, body)
            incoming = inbox["entries"][0]
            assert incoming["title"] == "New Purchase (Extended Mix)" and incoming["bpm"] == "172"
            assert incoming["candidates"][0]["track_id"] == purchase_id
            status, body = request("/api/inbox-apply", "POST", {"token": inbox["token"],
                                   "selections": [{"entry_id": incoming["id"], "track_id": purchase_id}]}, token)
            assert status == 200 and json.loads(body)["updated"] == 1, (status, body)
            saved_purchase = next(row for row in json.loads(request("/api/state")[1])["tracks"] if row["id"] == purchase_id)
            assert (saved_purchase["owned"], saved_purchase["prep_status"], saved_purchase["bpm"], saved_purchase["musical_key"]) == \
                (True, "downloaded", "170", "C minor")
            assert (saved_purchase["notes"], saved_purchase["tags"]) == ("Keep inbox notes", "my inbox tag")
            # macOS temporary folders can have a /var alias for /private/var.
            # DJList stores canonical file paths after its boundary checks.
            assert Path(saved_purchase["local_file"]) == purchase.resolve(), saved_purchase["local_file"]
            assert audio.read_bytes() == original_bytes and purchase.read_bytes() == purchased_bytes
            refresh_path = "/api/tracks/%s/refresh-store" % real_id
            assert request(refresh_path, "POST", {})[0] == 403
            status, body = request(refresh_path, "POST", {}, token)
            assert status == 200 and json.loads(body)["queued"] == 1, (status, body)
            deadline = time.monotonic() + 5
            while json.loads(request("/api/store-job")[1])["running"]:
                assert time.monotonic() < deadline, "Background store lookup did not finish"
                time.sleep(.05)
            real = json.loads(request("/api/state")[1])["tracks"][0]
            assert real["store_lookup"]["status"] == "matched"
            assert real["store_lookup"]["offer"]["price"]["currency"] == "GBP"
            assert (real["notes"], real["tags"], real["purchase_url"], real["owned"]) == \
                ("Keep my notes", "my tag", "https://example.test/manual-choice", False)
            more_path = "/api/tracks/%s/shop-links" % real_id
            assert request(more_path, "POST", {})[0] == 403
            status, body = request(more_path, "POST", {}, token)
            more = json.loads(body)
            assert status == 200 and more["status"] == "found" and more["links"][0]["provider"] == "bandcamp", (status, body)
            # Prepare a linked track, then model a renamed music folder.
            status, body = request("/api/tracks/%s" % real_id, "PATCH",
                                   {"local_file": str(audio), "owned": False, "prep_status": "reviewed"}, token)
            assert status == 200, (status, body)
            before_move = json.loads(request("/api/state")[1])["tracks"][0]
            music.rename(root / "Disconnected")
            status, body = request("/api/refresh-libraries", "POST", {"media": True, "xml": True}, token)
            offline = json.loads(body)
            assert status == 200 and offline["partial"] and offline["xml"]["entry_count"] == 1
            assert any(error["source"] == "media" for error in offline["errors"])
            assert offline["suggestions"] and not offline["suggestions"][0]["candidates"][0]["available"]
            new_music = root / "Disconnected"
            assert request("/api/settings", "PATCH", {"music_root": str(new_music)}, token)[0] == 200
            assert request("/api/relocation-preview", "POST", {"old_root": str(music)})[0] == 403
            status, body = request("/api/relocation-preview", "POST", {"old_root": str(music.resolve())}, token)
            plan = json.loads(body)
            assert status == 200 and plan["summary"]["eligible"] == 1, (status, body)
            status, body = request("/api/relocation-apply", "POST", {"token": plan["token"], "track_ids": [real_id]}, token)
            assert status == 200 and json.loads(body)["updated"] == 1, (status, body)
            after_move = json.loads(request("/api/state")[1])["tracks"][0]
            assert Path(after_move["local_file"]) == (new_music / audio.name).resolve() and after_move["file_available"]
            for field in ("owned", "prep_status", "tags", "notes", "musical_key", "bpm", "purchase_url"):
                assert after_move[field] == before_move[field], (field, before_move[field], after_move[field])
            assert request("/api/relocation-apply", "POST", {"token": plan["token"], "track_ids": [real_id]}, token)[0] == 409
            status, original = request("/app.js")
            assert status == 200 and original
            with (app / "web/app.js").open("ab") as file:
                file.write(b"\n// changed on disk during the test\n")
            assert request("/app.js")[1] == original, "Running process served assets from a different build"
            collision = subprocess.run(command, capture_output=True, text=True, timeout=8)
            assert collision.returncode != 0
            output = collision.stdout + collision.stderr
            assert "DJList" in output and "Control+C" in output, output
            print("PASS: real HTTP edits, setup checks, embedded metadata, reviewed inbox links, XML/offline inventory, simulated shop discovery, moved-file relinking, manual-field preservation, asset snapshot and occupied-port guidance")
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
