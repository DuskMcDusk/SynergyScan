"""Supervisor. Lives outside versions\\ and is the one piece an update cannot
replace, so it is deliberately as dumb as it can be.

    read current.txt  ->  run that release  ->  exit 75 means "start me again"

That is the whole job. Every temptation to add logic here should go into
synergyscan.selfupdate instead, where it ships inside a release, is versioned,
and is covered by tests. Changing this file means touching every installed
machine by hand.

Two exceptions earn their place, because both handle the case where the app
will not start at all - which is exactly when nobody on site can help:

* If current.txt names a release that is missing or has no interpreter, fall
  back to the newest release that does.
* If the current release crashes immediately, three times running, roll the
  pointer back to the previous release.

Standard library only, and no imports from synergyscan: this has to keep working
when the release it points at is broken.

Usage:
    uv run --no-project --python 3.12 launcher.py [--no-browser] [--no-update]
"""

from __future__ import annotations

import argparse
import logging
import logging.handlers
import os
import re
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

RESTART_EXIT_CODE = 75            # must match synergyscan.selfupdate
VERSION_RE = re.compile(r"^\d{4}\.\d{1,2}\.\d{1,2}-\d+$")

CRASH_WINDOW_S = 30               # "immediately" means within this many seconds
CRASH_LIMIT = 3                   # this many fast crashes triggers a rollback
RESTART_PAUSE_S = 3

ROOT = Path(__file__).resolve().parent
VERSIONS = ROOT / "versions"
POINTER = ROOT / "current.txt"
DATA = ROOT / "data"

log = logging.getLogger("launcher")


def setup_logging() -> None:
    (DATA / "logs").mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s launcher: %(message)s",
                            datefmt="%Y-%m-%d %H:%M:%S")
    fh = logging.handlers.RotatingFileHandler(
        DATA / "logs" / "launcher.log", maxBytes=1 << 20, backupCount=3,
        encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(fmt)
    logging.basicConfig(level=logging.INFO, handlers=[fh, sh])


def version_key(name: str) -> tuple[int, ...]:
    return tuple(int(p) for p in re.split(r"[.-]", name))


def installed() -> list[str]:
    """Release directories that have a usable interpreter, newest first."""
    if not VERSIONS.is_dir():
        return []
    ok = [d.name for d in VERSIONS.iterdir()
          if d.is_dir() and VERSION_RE.match(d.name) and interpreter(d.name).exists()]
    return sorted(ok, key=version_key, reverse=True)


def interpreter(version: str) -> Path:
    sub = "Scripts" if os.name == "nt" else "bin"
    exe = "python.exe" if os.name == "nt" else "python"
    return VERSIONS / version / ".venv" / sub / exe


def read_pointer() -> str | None:
    try:
        text = POINTER.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return text if VERSION_RE.match(text) else None


def write_pointer(version: str) -> None:
    tmp = POINTER.with_suffix(".txt.tmp")
    tmp.write_text(version + "\n", encoding="utf-8")
    os.replace(tmp, POINTER)


def resolve() -> str | None:
    """Which release to run, healing a pointer that names a broken release."""
    available = installed()
    if not available:
        return None
    want = read_pointer()
    if want and interpreter(want).exists():
        return want
    newest = available[0]
    log.error("current.txt says %r, which is not usable; falling back to %s",
              want, newest)
    write_pointer(newest)
    return newest


def rollback(from_version: str) -> str | None:
    """Point at the newest release older than `from_version`."""
    older = [v for v in installed() if version_key(v) < version_key(from_version)]
    if not older:
        return None
    target = older[0]
    write_pointer(target)
    log.critical("release %s failed to start %d times; rolled back to %s",
                 from_version, CRASH_LIMIT, target)
    return target


def run_once(version: str, extra: list[str]) -> int:
    py = interpreter(version)
    env = dict(os.environ)
    env["SYNERGYSCAN_DATA"] = str(DATA)     # data always lives outside the release
    env["PYTHONIOENCODING"] = "utf-8"
    cmd = [str(py), "-m", "synergyscan", *extra]
    log.info("starting release %s", version)
    return subprocess.run(cmd, cwd=VERSIONS / version, env=env).returncode


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="launcher.py")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--no-update", action="store_true",
                    help="passed through to the app")
    a = ap.parse_args(argv)

    DATA.mkdir(parents=True, exist_ok=True)
    setup_logging()

    extra = ["--no-update"] if a.no_update else []
    opened = a.no_browser
    fast_crashes = 0

    while True:
        version = resolve()
        if version is None:
            log.critical("no usable release found under %s. Run install.ps1 again.",
                         VERSIONS)
            return 1

        started = time.monotonic()
        if not opened:
            # Give the server a moment to bind before the browser asks for it.
            _open_browser_soon()
            opened = True

        try:
            code = run_once(version, extra)
        except OSError as e:
            log.critical("could not start release %s: %s", version, e)
            code = 1

        elapsed = time.monotonic() - started

        if code == RESTART_EXIT_CODE:
            log.info("release %s installed an update; restarting", version)
            fast_crashes = 0
            continue

        if code == 0:
            log.info("stopped normally")
            return 0

        log.error("release %s exited with code %s after %.1fs", version, code, elapsed)

        if elapsed < CRASH_WINDOW_S:
            fast_crashes += 1
            if fast_crashes >= CRASH_LIMIT:
                if rollback(version):
                    fast_crashes = 0
                    continue
                log.critical(
                    "release %s will not start and there is no earlier release to "
                    "fall back to. Send data\\logs\\ to support.", version)
                return 1
        else:
            fast_crashes = 0

        time.sleep(RESTART_PAUSE_S)


def _open_browser_soon(port: int = 8000, delay: float = 2.5) -> None:
    import threading
    threading.Timer(
        delay, lambda: webbrowser.open(f"http://127.0.0.1:{port}/")
    ).start()


if __name__ == "__main__":
    raise SystemExit(main())
