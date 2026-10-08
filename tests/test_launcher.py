"""bootstrap/launcher.py is stdlib-only and lives outside the package, so load it by path."""

import importlib.util
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "launcher", Path(__file__).resolve().parents[1] / "bootstrap" / "launcher.py")
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


def serve(body: bytes):
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_already_running_detects_synergyscan():
    srv = serve(json.dumps({"ok": True, "version": "1", "boot": "abc"}).encode())
    try:
        assert launcher.already_running(srv.server_port)
    finally:
        srv.shutdown()


def test_other_listener_or_nothing_is_not_synergyscan():
    srv = serve(b"<html>not us</html>")
    port = srv.server_port
    try:
        assert not launcher.already_running(port)
    finally:
        srv.shutdown()
        srv.server_close()
    assert not launcher.already_running(port)


def test_configured_port(tmp_path, monkeypatch):
    monkeypatch.setattr(launcher, "DATA", tmp_path)
    assert launcher.configured_port() == 8000
    (tmp_path / "config.json").write_text('{"port": 8123}')
    assert launcher.configured_port() == 8123
    (tmp_path / "config.json").write_text('{"port": "x"}')
    assert launcher.configured_port() == 8000
