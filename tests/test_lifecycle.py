"""In-app restart and update.

Nothing here touches a real release directory, network or process: the channel,
the installer and the server are all stand-ins. What is being tested is the
decision logic - when the actions are refused, what the user is told, and that
a restart is only ever requested after an install has really succeeded.
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from synergyscan import lifecycle, selfupdate
from synergyscan.selfupdate import check
from synergyscan.selfupdate import version as relver


@pytest.fixture(autouse=True)
def fresh_state(monkeypatch):
    monkeypatch.setattr(lifecycle, "_server", None)
    monkeypatch.setattr(lifecycle, "_restart_requested", False)
    monkeypatch.setitem(lifecycle._status, "phase", "idle")
    monkeypatch.setitem(lifecycle._status, "message", "")


@pytest.fixture
def installed(monkeypatch):
    """Pretend to be an installed release, with a server we can watch stop."""
    monkeypatch.setattr(relver, "is_source_checkout", lambda *a, **k: False)
    monkeypatch.setattr(relver, "current", lambda *a, **k: "2026.09.25-3")
    server = SimpleNamespace(should_exit=False)
    lifecycle.register_server(server)
    return server


def release(version="2026.10.01-1", **kw):
    return check.Release(version=version, url="https://example.invalid/x.zip",
                         sha256="0" * 64, notes=kw.pop("notes", "New dashboard"), **kw)


def wait_for(pred, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.01)
    return False


# --------------------------------------------------------------------- check
def test_check_reports_a_newer_release(client, installed, monkeypatch):
    monkeypatch.setattr(check, "fetch_channel", lambda *a, **k: release())
    body = client.get("/api/update").json()
    assert body["update_available"] is True
    assert body["latest"] == "2026.10.01-1"
    assert body["current"] == "2026.09.25-3"
    assert body["notes"] == "New dashboard"
    assert body["installable"] is True


def test_check_says_nothing_to_do_when_current(client, installed, monkeypatch):
    monkeypatch.setattr(check, "fetch_channel", lambda *a, **k: release("2026.09.25-3"))
    assert client.get("/api/update").json()["update_available"] is False


def test_check_offline_is_a_sentence_not_an_error(client, installed, monkeypatch):
    def offline(*a, **k):
        raise check.CheckError("no route to host")
    monkeypatch.setattr(check, "fetch_channel", offline)
    r = client.get("/api/update")
    assert r.status_code == 200
    body = r.json()
    assert body["update_available"] is False
    assert "internet" in body["error"].lower()


def test_check_refuses_to_skip_a_required_step(client, installed, monkeypatch):
    monkeypatch.setattr(check, "fetch_channel",
                        lambda *a, **k: release("2026.10.01-1", min_version="2026.09.30-1"))
    body = client.get("/api/update").json()
    assert body["update_available"] is False
    assert "intermediate" in body["error"]


# ------------------------------------------------------ refused from source
def test_restart_and_update_are_refused_in_a_source_checkout(client):
    assert relver.is_source_checkout()
    assert client.post("/api/restart").status_code == 409
    r = client.post("/api/update")
    assert r.status_code == 409
    assert "installed copy" in r.json()["detail"]


def test_restart_needs_a_registered_server(client, monkeypatch):
    monkeypatch.setattr(relver, "is_source_checkout", lambda *a, **k: False)
    assert client.post("/api/restart").status_code == 409


# ------------------------------------------------------------------- restart
def test_restart_stops_the_server_and_flags_the_exit_code(installed):
    lifecycle.request_restart(delay=0)
    assert wait_for(lambda: installed.should_exit)
    assert lifecycle.restart_requested() is True


def test_restart_endpoint_is_accepted_when_installed(client, installed):
    assert client.post("/api/restart").status_code == 202
    assert wait_for(lambda: installed.should_exit)


def test_health_carries_a_boot_id(client):
    assert client.get("/api/health").json()["boot"] == lifecycle.BOOT_ID


# -------------------------------------------------------------------- update
def test_update_installs_then_restarts(installed, monkeypatch):
    monkeypatch.setattr(selfupdate, "available", lambda url: release())
    monkeypatch.setattr(selfupdate, "apply_from_paths",
                        lambda rel, download_timeout: SimpleNamespace(version=rel.version))
    monkeypatch.setattr(lifecycle.threading, "Timer",
                        lambda delay, fn: SimpleNamespace(start=fn))
    lifecycle.start_update("https://example.invalid/stable.json", 5)
    assert wait_for(lambda: installed.should_exit)
    st = lifecycle.status()
    assert st["phase"] == "restarting"
    assert st["version"] == "2026.10.01-1"
    assert lifecycle.restart_requested() is True


def test_failed_install_leaves_the_app_running(installed, monkeypatch):
    def boom(rel, download_timeout):
        raise selfupdate.ApplyError("the download was corrupt")
    monkeypatch.setattr(selfupdate, "available", lambda url: release())
    monkeypatch.setattr(selfupdate, "apply_from_paths", boom)
    lifecycle.start_update("https://example.invalid/stable.json", 5)
    assert wait_for(lambda: lifecycle.status()["phase"] == "failed")
    assert "nothing was changed" in lifecycle.status()["message"]
    assert installed.should_exit is False
    assert lifecycle.restart_requested() is False


def test_update_with_nothing_waiting_does_not_restart(installed, monkeypatch):
    monkeypatch.setattr(selfupdate, "available", lambda url: None)
    lifecycle.start_update("https://example.invalid/stable.json", 5)
    assert wait_for(lambda: lifecycle.status()["phase"] == "uptodate")
    assert installed.should_exit is False


def test_only_one_update_at_a_time(installed, monkeypatch):
    monkeypatch.setitem(lifecycle._status, "phase", "installing")
    with pytest.raises(lifecycle.Unavailable):
        lifecycle.start_update("https://example.invalid/stable.json", 5)
