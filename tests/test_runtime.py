"""Runtime/update regressions. Uses temporary files and in-memory HTTP streams."""
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import (API_VERSION, VERSION, Application, Handler, LocalServer, main,
                 occupied_port_message, running_instance)


class StreamSocket:
    def __init__(self, request):
        self.input = io.BytesIO(request)
        self.output = io.BytesIO()

    def makefile(self, mode, *args):
        return self.input if "r" in mode else self.output

    def sendall(self, data):
        self.output.write(data)


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.web = self.root / "web"
        self.web.mkdir()
        (self.web / "index.html").write_text("<!doctype html><title>Old interface</title>")
        (self.web / "app.js").write_text("const oldInterface = true;")
        self.app = Application(self.root / "data", web_dir=self.web)

    def tearDown(self):
        self.app.close()
        self.temp.cleanup()

    def request(self, path="/api/health", headers=None, application=None, method="GET", payload=None):
        values = {"Host": "127.0.0.1:8888", **(headers or {})}
        data = json.dumps(payload).encode("utf-8") if payload is not None else b""
        if payload is not None:
            values["Content-Type"] = "application/json"
        values["Content-Length"] = str(len(data))
        request = (method + " " + path + " HTTP/1.1\r\n" +
                   "".join(name + ": " + value + "\r\n" for name, value in values.items()) + "\r\n")
        socket = StreamSocket(request.encode("ascii") + data)
        server = type("Server", (), {"app": application or self.app})()
        Handler(socket, ("127.0.0.1", 10001), server)
        head, body = socket.output.getvalue().split(b"\r\n\r\n", 1)
        return int(head.split()[1]), head, body

    def test_replacing_files_cannot_mix_new_interface_with_old_runtime(self):
        original_build = self.app.build_id
        (self.web / "index.html").write_text("<!doctype html><title>New interface</title>")
        (self.web / "app.js").write_text("const newEndpoint = '/api/new-feature';")
        (self.web / "later.js").write_text("const addedAfterStartup = true;")
        self.assertIn(b"Old interface", self.request("/")[2])
        self.assertIn(b"oldInterface", self.request("/app.js")[2])
        self.assertEqual(self.request("/later.js")[0], 404)
        self.assertEqual(self.app.build_id, original_build)
        fresh = Application(self.root / "data", web_dir=self.web)
        self.assertIn(b"New interface", self.request("/", application=fresh)[2])
        self.assertIn(b"newEndpoint", self.request("/app.js", application=fresh)[2])
        self.assertNotEqual(fresh.build_id, original_build)

    def test_snapshot_excludes_symlinks_and_static_traversal(self):
        secret = self.root / "outside.txt"
        secret.write_text("not a web asset")
        (self.web / "outside.txt").symlink_to(secret)
        fresh = Application(self.root / "data", web_dir=self.web)
        for path in ("/outside.txt", "/../outside.txt", "/%2e%2e/outside.txt", "/%2foutside.txt"):
            self.assertEqual(self.request(path, application=fresh)[0], 404)

    def test_health_contract_identifies_runtime_without_collection_or_credentials(self):
        self.app.tokens = {"access_token": "PRIVATE_ACCESS_SENTINEL", "refresh_token": "PRIVATE_REFRESH_SENTINEL"}
        status, headers, payload = self.request()
        self.assertEqual(status, 200)
        health = json.loads(payload)
        self.assertEqual(health["app"], "DJList")
        self.assertEqual((health["version"], health["api_version"]), (VERSION, API_VERSION))
        self.assertEqual(health["build_id"], self.app.state()["runtime"]["build_id"])
        self.assertTrue(health["static_snapshot"])
        self.assertNotIn(b"PRIVATE_", payload)
        self.assertNotIn("csrf_token", health)
        self.assertNotIn("tracks", health)
        self.assertNotIn("settings", health)
        self.assertIn(("X-DJList-Version: " + VERSION).encode(), headers)
        for rejected in ({"Host": "other.example:8888"}, {"Origin": "https://other.example"}, {"Sec-Fetch-Site": "cross-site"}):
            self.assertEqual(self.request(headers=rejected)[0], 403)

    def test_public_defaults_do_not_replace_saved_configuration(self):
        self.assertEqual(self.app.store.settings()["spotify_client_id"], "")
        self.assertEqual(self.app.store.settings()["rekordbox_db"], "")
        self.app.change_settings({"spotify_client_id": "a" * 32, "music_root": str(self.root / "Music"), "rekordbox_db": ""})
        fresh = Application(self.root / "data", web_dir=self.web)
        self.assertEqual(fresh.store.settings()["spotify_client_id"], "a" * 32)
        self.assertEqual(fresh.store.settings()["music_root"], str(self.root / "Music"))
        self.assertFalse(fresh.state()["drive"]["rekordbox_db_exists"])
        fresh.close()

    def test_local_audio_http_ranges_head_and_invalid_range(self):
        from library_inventory import _identity
        # macOS temporary paths can use /var as an alias for /private/var.
        # The media guard deliberately requires the actual selected directory.
        music = (self.root / "Music").resolve()
        music.mkdir()
        audio = music / "sample.mp3"
        audio.write_bytes(b"0123456789")
        self.app.change_settings({"music_root": str(music)})
        self.app.scan()
        url = "/api/library-audio?entry_id=" + _identity(str(audio.resolve()))
        status, headers, body = self.request(url, headers={"Range": "bytes=2-5"})
        self.assertEqual((status, body), (206, b"2345"))
        self.assertIn(b"Content-Range: bytes 2-5/10", headers)
        self.assertIn(b"Cross-Origin-Resource-Policy: same-origin", headers)
        status, headers, body = self.request(url, method="HEAD")
        self.assertEqual((status, body), (200, b""))
        self.assertIn(b"Content-Length: 10", headers)
        status, headers, _ = self.request(url, headers={"Range": "bytes=10-"})
        self.assertEqual(status, 416)
        self.assertIn(b"Content-Range: bytes */10", headers)
        for suffix in ("&path=/outside", "&entry_id=" + "a" * 24):
            self.assertEqual(self.request(url + suffix)[0], 400)
        self.app.store.put_settings({"mode": "demo"})
        self.assertEqual(self.request(url)[0], 409)

    def test_catalogue_route_is_present_and_requires_local_csrf_guard(self):
        self.assertTrue(self.app.state()["capabilities"]["catalog_search"])
        payload = {"query": "Example Artist Song", "reference": {"artists": "Example Artist", "title": "Song"}}
        result = {"provider": "itunes", "query": payload["query"], "offers": [], "errors": []}
        with patch.object(self.app.catalog, "search", return_value=result) as search:
            self.assertEqual(self.request("/api/catalog-search", method="POST", payload=payload)[0], 403)
            search.assert_not_called()
            status, headers, body = self.request("/api/catalog-search", method="POST", payload=payload,
                                                 headers={"X-CSRF-Token": self.app.csrf_token})
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body), result)
            search.assert_called_once_with(payload["query"], payload["reference"])
            self.assertIn(b"media-src 'self' blob: https://audio-ssl.itunes.apple.com;", headers)
        self.assertEqual(self.request("/api/catalog-search", method="POST", payload={**payload, "url": "https://other.example"},
                                      headers={"X-CSRF-Token": self.app.csrf_token})[0], 400)

    def test_occupied_port_probe_recognises_current_and_legacy_instances(self):
        for status, body, expected in (
            (200, json.dumps({"app": "DJList", "app_dir": "/example/DJList"}).encode(), "/example/DJList"),
            (404, b'{"error":"This endpoint does not exist."}', None),
        ):
            connection = Mock()
            response = connection.getresponse.return_value
            response.status = status
            response.getheader.return_value = "DJList/0.1.1 Python/3.12"
            response.read.return_value = body
            with patch("app.HTTPConnection", return_value=connection) as factory:
                result = running_instance(8888)
            factory.assert_called_once_with("127.0.0.1", 8888, timeout=2)
            connection.request.assert_called_once_with("GET", "/api/health", headers={"Host": "127.0.0.1:8888"})
            connection.close.assert_called_once()
            self.assertEqual(result["version"], "0.1.1")
            self.assertEqual(result["app_dir"], expected)
            message = occupied_port_message(8888, result)
            self.assertIn("Control+C", message)
            self.assertIn("Closing only the browser tab does not stop DJList", message)

    def test_non_djlist_listener_is_not_treated_as_an_existing_app(self):
        connection = Mock()
        response = connection.getresponse.return_value
        response.status = 302
        response.getheader.return_value = "AnotherServer"
        response.read.return_value = b""
        with patch("app.HTTPConnection", return_value=connection):
            self.assertIsNone(running_instance(8888))
        connection.request.assert_called_once()
        self.assertIn("already in use or unavailable", occupied_port_message(8888, None))

    def test_server_binds_numeric_loopback_without_reverse_dns(self):
        socket = Mock()
        socket.getsockname.return_value = ("127.0.0.1", 43210)
        with patch("socketserver.socket.socket", return_value=socket), \
             patch("http.server.socket.getfqdn", side_effect=AssertionError("Reverse DNS must not delay startup")) as lookup:
            server = LocalServer(("127.0.0.1", 0), Handler)
            try:
                socket.bind.assert_called_once_with(("127.0.0.1", 0))
                socket.getsockname.assert_called_once()
                socket.listen.assert_called_once_with(server.request_queue_size)
                self.assertEqual(server.server_address, ("127.0.0.1", 43210))
                self.assertEqual((server.server_name, server.server_port), ("127.0.0.1", 43210))
                lookup.assert_not_called()
            finally:
                server.server_close()

    def test_port_collision_does_not_initialise_data_or_open_the_old_browser(self):
        output = io.StringIO()
        with patch("app.LocalServer", side_effect=OSError("in use")), \
             patch("app.running_instance", return_value={"version": "0.1.0", "app_dir": None}), \
             patch("app.Application") as application, \
             patch("app.webbrowser.open") as browser, \
             patch("sys.stderr", output):
            result = main(["--data-dir", str(self.root / "other")])
        self.assertEqual(result, 1)
        application.assert_not_called()
        browser.assert_not_called()
        self.assertIn("DJList 0.1.0 is already running", output.getvalue())
        self.assertFalse((self.root / "other").exists())


if __name__ == "__main__":
    unittest.main()
