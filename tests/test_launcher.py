"""bootstrap/launcher.py is stdlib-only and lives outside the package, so load it by path."""

import importlib.util
import json
import shutil
import subprocess
import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

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


def test_rollback_blocks_the_failed_release_and_moves_the_pointer(tmp_path, monkeypatch):
    versions = tmp_path / "versions"
    for v in ("2026.10.01-1", "2026.10.02-1"):
        (versions / v).mkdir(parents=True)
    monkeypatch.setattr(launcher, "VERSIONS", versions)
    monkeypatch.setattr(launcher, "POINTER", tmp_path / "current.txt")
    monkeypatch.setattr(launcher, "DATA", tmp_path / "data")
    (tmp_path / "data").mkdir(exist_ok=True)
    monkeypatch.setattr(launcher, "interpreter", lambda v: versions / v)  # exists() is True for made dirs
    assert launcher.rollback("2026.10.02-1") == "2026.10.01-1"
    assert launcher.read_pointer() == "2026.10.01-1"
    version, until = (tmp_path / "data" / "blocked_versions.txt").read_text().split()
    assert version == "2026.10.02-1"
    # A crash-loop block expires: the cause may have been the machine, not the release.
    hours = (datetime.fromisoformat(until) - datetime.now(UTC)).total_seconds() / 3600
    assert 23 < hours <= 24


# ------------------------------------------------------------- rollback.ps1
ROLLBACK = Path(__file__).resolve().parents[1] / "bootstrap" / "rollback.ps1"
POWERSHELL = shutil.which("powershell.exe") or shutil.which("pwsh")
needs_powershell = pytest.mark.skipif(POWERSHELL is None, reason="no PowerShell")


def run_rollback(root: Path, pointer: str) -> subprocess.CompletedProcess:
    """rollback.ps1 against a throwaway install: two releases, answering y."""
    for v in ("2026.10.01-1", "2026.10.02-1"):
        (root / "versions" / v / ".venv" / "Scripts").mkdir(parents=True, exist_ok=True)
        (root / "versions" / v / ".venv" / "Scripts" / "python.exe").touch()
    (root / "current.txt").write_text(pointer, encoding="ascii")
    return subprocess.run(
        [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ROLLBACK),
         "-InstallRoot", str(root), "-NoStart"],
        input="y" + "\n" * 2, capture_output=True, text=True, timeout=120, check=False)


@needs_powershell
def test_rollback_script_goes_back_and_blocks_the_release_it_left(tmp_path, monkeypatch):
    r = run_rollback(tmp_path, "2026.10.02-1")
    assert r.returncode == 0, r.stdout + r.stderr
    assert (tmp_path / "current.txt").read_text().strip() == "2026.10.01-1"
    blocked = (tmp_path / "data" / "blocked_versions.txt").read_bytes()
    assert blocked.decode("utf-8-sig").split() == ["2026.10.02-1"]   # bare: no expiry
    # ...and the update check reads what PowerShell wrote (BOM, CRLF).
    from synergyscan.selfupdate import check as C
    monkeypatch.setenv("SYNERGYSCAN_DATA", str(tmp_path / "data"))
    assert C.blocked_versions() == {"2026.10.02-1"}


@needs_powershell
def test_rollback_script_survives_a_junk_pointer(tmp_path):
    """Junk in current.txt used to throw after the app had been stopped."""
    r = run_rollback(tmp_path, "2026.10.02-1-rc")
    assert r.returncode == 0, r.stdout + r.stderr
    assert (tmp_path / "current.txt").read_text().strip() == "2026.10.02-1"
    assert not (tmp_path / "data" / "blocked_versions.txt").exists()


# ------------------------------------------------------- shutdown vs crashes
def run_loop(monkeypatch, tmp_path, codes):
    """Drive launcher.main with run_once returning `codes` in turn."""
    monkeypatch.setattr(launcher, "DATA", tmp_path / "data")
    monkeypatch.setattr(launcher, "setup_logging", lambda: None)
    monkeypatch.setattr(launcher, "already_running", lambda port: False)
    monkeypatch.setattr(launcher, "resolve", lambda: "2026.10.08-2")
    monkeypatch.setattr(launcher.time, "sleep", lambda s: None)
    rollbacks = []
    monkeypatch.setattr(launcher, "rollback", lambda v: rollbacks.append(v) or None)
    it = iter(codes)
    monkeypatch.setattr(launcher, "run_once", lambda v, extra: next(it))
    return launcher.main(["--no-browser"]), rollbacks


@pytest.mark.parametrize("code", [0x40010004, 0xC000013A, 0xC000026B])
def test_windows_ending_the_session_is_not_a_crash(monkeypatch, tmp_path, code):
    """What happened on a real PC: shutdown killed the app, the restarts failed
    while Windows logged off, and the launcher rolled back a working release."""
    result, rollbacks = run_loop(monkeypatch, tmp_path, [code])
    assert result == 0
    assert rollbacks == []


def test_real_fast_crashes_still_roll_back(monkeypatch, tmp_path):
    result, rollbacks = run_loop(monkeypatch, tmp_path, [1, 1, 1])
    assert rollbacks == ["2026.10.08-2"]
    assert result == 1                     # no earlier release in this stub


def test_configured_port_reads_a_config_saved_with_a_bom(tmp_path, monkeypatch):
    monkeypatch.setattr(launcher, "DATA", tmp_path)
    (tmp_path / "config.json").write_bytes(b'\xef\xbb\xbf{"port": 8123}')
    assert launcher.configured_port() == 8123


@needs_powershell
@pytest.mark.parametrize("script", sorted(p.name for p in ROLLBACK.parent.glob("*.ps1")))
def test_every_bootstrap_script_parses(script):
    """install.ps1 downloads things, so it cannot run here - but a syntax error in
    it would only show up on a customer PC. PowerShell's own parser checks it."""
    path = ROLLBACK.parent / script
    cmd = ("$e = $null; [void][System.Management.Automation.Language.Parser]::ParseFile("
           f"'{path}', [ref]$null, [ref]$e); $e | ForEach-Object {{ $_.ToString() }}; "
           "exit $e.Count")
    r = subprocess.run([POWERSHELL, "-NoProfile", "-Command", cmd],
                       capture_output=True, text=True, timeout=60, check=False)
    assert r.returncode == 0, r.stdout + r.stderr
