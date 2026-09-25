"""Logging setup.

Logs go to data/logs/synergyscan.log, which survives updates and rollbacks, and
to stderr so the launcher's console shows what is happening during an install.

Every startup logs the release version and the update decision, including "no
update available". When a site reports that something broke last Tuesday the
first question is always which version was running and when it changed, and
that has to be answerable from the log alone.
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path

from . import paths

FMT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
DATEFMT = "%Y-%m-%d %H:%M:%S"
MAX_BYTES = 2 * 1024 * 1024
BACKUPS = 5

_configured = False


def log_file() -> Path:
    return paths.logs_dir() / "synergyscan.log"


def setup(verbose: bool = False) -> Path:
    """Configure root logging once. Returns the log file path."""
    global _configured
    path = log_file()
    if _configured:
        return path

    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)

    fmt = logging.Formatter(FMT, datefmt=DATEFMT)

    fh = logging.handlers.RotatingFileHandler(
        path, maxBytes=MAX_BYTES, backupCount=BACKUPS, encoding="utf-8",
    )
    fh.setFormatter(fmt)
    root.addHandler(fh)

    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(fmt)
    root.addHandler(sh)

    # uvicorn's access log is noise on a single-user machine; the app logs the
    # things that matter (scans, prints, updates) itself.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

    _configured = True
    return path


def tail(lines: int = 200) -> list[str]:
    """Last N log lines, for the doctor report."""
    p = log_file()
    if not p.exists():
        return []
    try:
        content = p.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    return content[-lines:]
