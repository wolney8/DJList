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
import xml.etree.ElementTree as ET


def main():
    source = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(source))
    sys.path.insert(0, str(source / "tests"))
    from app import API_VERSION, Store
    from library_inventory import _identity
    from test_audio_metadata import flac_bytes
    with tempfile.TemporaryDirectory(prefix="djlist-smoke-") as temporary:
        root = Path(temporary).resolve()
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
        xml_path.write_text('<DJ_PLAYLISTS><COLLECTION Entries="1"><TRACK TrackID="1" Name="Night Window (Original Mix)" Artist="Example Artist" '
                            'AverageBpm="172" Tonality="C minor" Genre="Jungle" TotalTime="193" Location="%s">'
                            '<TEMPO Inizio="0.5" Bpm="172" Metro="4/4" Battito="1"/>'
                            '<POSITION_MARK Name="Keep this cue" Type="0" Start="0.5" Num="0"/></TRACK>'
                            '</COLLECTION></DJ_PLAYLISTS>' % audio.as_uri(), encoding="utf-8")
        original_xml = xml_path.read_bytes()
        database = root / "Rekordbox" / "master.db"
        database.parent.mkdir()
        database_bytes = b"Synthetic opaque database bytes for read-only backup checks" * 1024
        database.write_bytes(database_bytes)
        store = Store(root / "data")
        store.put_settings({"music_root": str(music), "rekordbox_xml": str(xml_path), "auto_store_lookup": False,
                            "download_root": str(downloads), "auto_library_refresh": False,
                            "rekordbox_db": str(database), "auto_rekordbox_backup": False})
        store.upsert([{"spotify_id": "fixture-track", "artists": "Example Artist", "title_and_version": "Night Window (Original Mix)",
                       "duration_ms": 193000, "album": "Fictional test release", "spotify_url": "", "isrc": "GBABC2600001"},
                      {"spotify_id": "fixture-purchase", "artists": "Example Artist", "title_and_version": "New Purchase (Extended Mix)",
                       "duration_ms": 193000, "album": "Fictional test release", "spotify_url": ""}], "fixture-source")
        real_id = store.rows("real")[0]["id"]
        purchase_id = store.rows("real")[1]["id"]
        store.patch(real_id, "real", {"notes": "Keep my notes", "tags": "my tag", "purchase_url": "https://example.test/manual-choice",
                                     "local_file": str(music / "old-missing-name.flac"), "owned": False}, confirmed_file=True)
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
            "from rekordbox_backups import RekordboxBackups\n"
            "from file_operations import FileOperations\n"
            "app.FileOperations = lambda store, library: FileOperations(store, library, "
            "process_checker=lambda: {'known':True,'running':[]})\n"
            "app.RekordboxBackups = lambda store, lock: RekordboxBackups(store, lock, "
            "process_checker=lambda: {'known':True,'running':[]}, platform='darwin')\n"
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
            assert health["app"] == "DJList" and health["api_version"] == API_VERSION
            assert health["static_snapshot"] and health["build_id"]
            status, body = request("/api/state")
            assert status == 200
            state = json.loads(body)
            token = state["csrf_token"]
            assert state["version"] == health["version"]
            new_actions = ("/api/backups/create", "/api/backups/verify", "/api/duplicates/verify", "/api/missing/preview",
                           "/api/missing/apply", "/api/cues/plan", "/api/cues/export",
                           "/api/file-operations/preview", "/api/file-operations/apply", "/api/file-operations/undo-preview",
                           "/api/file-operations/undo", "/api/metadata/preview", "/api/metadata/apply",
                           "/api/metadata/reset-preview", "/api/metadata/reset")
            for endpoint in new_actions:
                assert request(endpoint, "POST", {})[0] == 403, endpoint
            assert request("/api/demo", "POST", {"enabled": True}, token)[0] == 200
            assert request("/api/library-audit")[0] == 409
            assert request("/api/backups/create", "POST", {}, token)[0] == 409
            assert json.loads(request("/api/backups")[1])["source_path"] == ""
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
            assert json.loads(request("/api/activity")[1])["recent"]
            assert request("/api/cuegen/status")[0] == 200
            assert json.loads(request("/api/file-operations")[1])["operations"] == []
            assert request("/api/library-audio?entry_id=" + inventory["entries"][0]["id"])[1] == original_bytes
            assert request("/api/library-review?filter=metadata")[0] == 200
            assert request("/api/metadata/preview", "POST", {"entry_ids": [inventory["entries"][0]["id"]]}, token)[0] == 200
            assert libraries["media"]["metadata_read"] == 1
            status, body = request("/api/setup-check")
            setup = json.loads(body)
            assert status == 200 and all(row["status"] == "ok" for row in setup["checks"]), (status, body)
            # Cross-module v0.6 checks use actual temporary files and HTTP.
            # Only macOS process detection is injected in the temporary runner.
            status, body = request("/api/backups/create", "POST", {}, token)
            backup = json.loads(body)
            assert status == 200 and backup["created"], (status, body)
            record = backup["backup"]
            assert record["verification"] == "verified"
            assert (Path(record["path"]) / "master.db").read_bytes() == database_bytes
            status, body = request("/api/backups/verify", "POST", {"backup_id": record["id"]}, token)
            assert status == 200 and json.loads(body)["verified"], (status, body)
            assert json.loads(request("/api/backups/create", "POST", {}, token)[1])["unchanged"]
            assert database.read_bytes() == database_bytes
            duplicate = music / "Another folder" / audio.name
            duplicate.parent.mkdir()
            duplicate.write_bytes(original_bytes)
            assert request("/api/refresh-libraries", "POST", {"media": True, "xml": False}, token)[0] == 200
            status, body = request("/api/library-audit")
            audit = json.loads(body)
            assert status == 200 and audit["summary"]["missing_files"] == 1, (status, body)
            group = next(row for row in audit["issues"] if row["kind"] == "duplicate")
            status, body = request("/api/duplicates/verify", "POST", {"group_id": group["group_id"]}, token)
            assert status == 200 and json.loads(body)["status"] == "byte_identical", (status, body)
            status, body = request("/api/missing/preview", "POST", {"track_id": real_id}, token)
            repair = json.loads(body)
            assert status == 200, (status, body)
            chosen = next(row for row in repair["candidates"] if Path(row["path"]) == audio)
            status, body = request("/api/missing/apply", "POST", {"token": repair["token"], "entry_id": chosen["entry_id"]}, token)
            assert status == 200 and json.loads(body)["updated"] == 1, (status, body)
            repaired = next(row for row in json.loads(request("/api/state")[1])["tracks"] if row["id"] == real_id)
            assert repaired["local_file"] == str(audio) and not repaired["owned"]
            assert (repaired["notes"], repaired["tags"]) == ("Keep my notes", "my tag")
            assert request("/api/missing/apply", "POST", {"token": repair["token"], "entry_id": chosen["entry_id"]}, token)[0] == 409
            status, body = request("/api/cues/plan", "POST", {"entry_id": inventory["entries"][0]["id"], "options": {}}, token)
            cue_plan = json.loads(body)
            assert status == 200 and cue_plan["suggestions"] and cue_plan["selected_count"] == 0, (status, body)
            status, body = request("/api/cues/export", "POST", {"token": cue_plan["token"], "selected_ids": [cue_plan["suggestions"][0]["id"]]}, token)
            assert status == 200, (status, body)
            exported = ET.fromstring(body).find("COLLECTION/TRACK")
            assert exported is not None and len(exported.findall("POSITION_MARK")) == 2
            assert exported.find("POSITION_MARK").get("Name") == "Keep this cue"
            assert len(exported.findall("TEMPO")) == 1
            assert xml_path.read_bytes() == original_xml and audio.read_bytes() == original_bytes
            assert request("/api/library-audit.csv")[0] == 200
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
            # Exercise reviewed native file moves and undo across HTTP. Process
            # checks are injected for these synthetic files, never production.
            operation_entry_id = _identity(str(audio.resolve()))
            for action, options in (("rename", {"names": {operation_entry_id: "renamed-review.flac"}}), ("remove", {})):
                status, body = request("/api/file-operations/preview", "POST", {"action": action, "entry_ids": [operation_entry_id], "options": options}, token)
                plan = json.loads(body)
                assert status == 200 and plan["items"][0]["selectable"], (status, body)
                changed_path = Path(plan["items"][0]["after"])
                status, body = request("/api/file-operations/apply", "POST", {"token": plan["token"], "selected_ids": [operation_entry_id]}, token)
                assert status == 200 and not audio.exists() and changed_path.read_bytes() == original_bytes, (status, body)
                changed_operation = json.loads(body)["operation_id"]
                operation = next(row for row in json.loads(request("/api/file-operations")[1])["operations"] if row["id"] == changed_operation)
                status, body = request("/api/file-operations/undo-preview", "POST", {"operation_id": operation["id"]}, token)
                undo = json.loads(body)
                assert status == 200, (status, body)
                status, body = request("/api/file-operations/undo", "POST", {"token": undo["token"], "selected_ids": [undo["items"][0]["entry_id"]]}, token)
                assert status == 200 and audio.read_bytes() == original_bytes and not changed_path.exists(), (status, body)
            assert database.read_bytes() == database_bytes and xml_path.read_bytes() == original_xml
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
            print("PASS: real HTTP edits, checked database copies, duplicate hashing, missing-file relinking, selected cue XML export, setup/metadata/inbox, offline inventory, simulated shops, moved-folder relinking, annotation preservation, asset snapshot and occupied-port guidance")
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
