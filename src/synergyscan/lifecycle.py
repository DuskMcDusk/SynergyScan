"""Restart and update from inside the app.

An end user should never need Task Manager or a command line. The launcher
already restarts the app whenever it exits with RESTART_EXIT_CODE, and the
updater already installs a release and then asks for exactly that, so the UI
only has to ask the app to stop on purpose.

Two actions, both safe to offer to anyone at the keyboard:

    restart   stop cleanly, exit 75, the launcher starts us again. Startup
              also checks for an update, so a restart picks one up for free.
    update    check the channel, install in a background thread (download,
              verify, build, self-test, migrate - the same code as startup),
              then restart into it. The old release keeps serving the whole
              time and stays installed if anything fails.

Neither does anything in a source checkout: there is no launcher to bring a
developer's process back, and "updating" a checkout makes no sense.

BOOT_ID changes on every start. The UI compares it to know the app really
went away and came back, instead of reloading into the process that was
already shutting down.
"""

from __future__ import annotations

import logging
import threading
import uuid

from . import selfupdate
from .selfupdate import check
from .selfupdate import version as relver

log = logging.getLogger(__name__)

BOOT_ID = uuid.uuid4().hex


class Unavailable(RuntimeError):
    """The action cannot be done here. The message is shown to the user."""


_lock = threading.Lock()
_status: dict = {"phase": "idle", "message": "", "version": None}
_server = None                    # the uvicorn.Server, registered by __main__
_restart_requested = False


def register_server(server) -> None:
    global _server
    _server = server


def restart_requested() -> bool:
    """True once a restart was asked for; __main__ then exits with code 75."""
    return _restart_requested


def status() -> dict:
    with _lock:
        return dict(_status)


def _set(phase: str, message: str = "", version: str | None = None) -> None:
    with _lock:
        _status.update(phase=phase, message=message, version=version)


def _require_installed() -> None:
    if relver.is_source_checkout():
        raise Unavailable("This only works in an installed copy of SynergyScan, "
                          "not when running from source.")


# ------------------------------------------------------------------- restart
def request_restart(delay: float = 0.5) -> None:
    """Stop the server shortly, so the HTTP response that asked can get out."""
    _require_installed()
    if _server is None:
        raise Unavailable("The app was not started in a way that allows restarting.")

    def stop() -> None:
        global _restart_requested
        log.info("restart requested from the UI")
        _restart_requested = True
        _server.should_exit = True

    threading.Timer(delay, stop).start()


# -------------------------------------------------------------------- update
def check_for_update(channel_url: str) -> dict:
    """What the Update button needs to know. Never raises."""
    cur = relver.current()
    out = {
        "current": cur,
        "latest": None,
        "notes": "",
        "update_available": False,
        "installable": not relver.is_source_checkout(),
        "error": None,
    }
    try:
        rel = check.fetch_channel(channel_url, timeout=10.0)
    except check.CheckError as e:
        log.info("update check failed: %s", e)
        out["error"] = "Could not reach the update server. Check the internet connection."
        return out
    out["latest"] = rel.version
    out["notes"] = rel.notes
    try:
        if relver.newer(rel.version, cur):
            if rel.min_version and relver.newer(rel.min_version, cur):
                out["error"] = ("This update needs an intermediate version first. "
                                "Please contact support.")
            else:
                out["update_available"] = True
    except relver.BadVersion as e:
        log.warning("update check: %s", e)
        out["error"] = "The update server returned something unexpected."
    return out


def start_update(channel_url: str, timeout: int) -> None:
    """Begin installing in the background. Poll status() for progress."""
    _require_installed()
    with _lock:
        if _status["phase"] in ("installing", "restarting"):
            raise Unavailable("An update is already in progress.")
        _status.update(phase="installing", message="Downloading the update…", version=None)
    threading.Thread(target=_run_update, args=(channel_url, timeout),
                     name="update", daemon=True).start()


def _run_update(channel_url: str, timeout: int) -> None:
    try:
        rel = selfupdate.available(channel_url)
        if rel is None:
            _set("uptodate", "SynergyScan is already up to date.")
            return
        _set("installing", f"Installing {rel.version}…", rel.version)
        applied = selfupdate.apply_from_paths(rel, download_timeout=timeout)
    except selfupdate.ApplyError as e:
        log.error("update from the UI was not installed: %s", e)
        _set("failed", f"The update could not be installed and nothing was changed. {e}")
        return
    except Exception:
        log.exception("unexpected failure installing an update from the UI")
        _set("failed", "The update could not be installed and nothing was changed. "
                       "Details are in the log.")
        return
    _set("restarting", f"Installed {applied.version}. Restarting…", applied.version)
    try:
        request_restart()
    except Unavailable as e:
        _set("failed", str(e))
