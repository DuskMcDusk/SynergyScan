"""One command that answers "what is wrong with this machine?".

The single most valuable thing you can give a site with no technical staff is a
button that produces one file to email. Without it, every support call starts
with twenty minutes of asking someone to read things off a screen.

    python -m synergyscan --doctor > doctor.txt

Deliberately plain text, ordered most-useful-first, and it never raises: a
diagnostic that crashes on the broken machine is worthless. Each section
catches its own failures and says so in place.
"""

from __future__ import annotations

import json
import os
import platform
import sqlite3
import sys
from datetime import datetime, timezone
from typing import Callable

from . import config, db, logs, paths
from .selfupdate import version as relver


def _safe(fn: Callable[[], object]) -> object:
    try:
        return fn()
    except Exception as e:
        return f"<failed: {type(e).__name__}: {e}>"


def _versions() -> dict:
    return {
        "release": relver.current(),
        "source_checkout": relver.is_source_checkout(),
        "package": __import__("synergyscan").__version__,
        "python": sys.version.split()[0],
        "executable": sys.executable,
        "release_dir": str(paths.release_dir()),
        "install_root": str(paths.install_root() or "-"),
        "pointer": str(paths.current_pointer() or "-"),
        "pointer_says": _safe(
            lambda: (paths.current_pointer().read_text(encoding="utf-8").strip()
                     if paths.current_pointer() else "-")
        ),
        "installed_versions": _safe(
            lambda: sorted(d.name for d in (paths.versions_dir() or paths.release_dir())
                           .iterdir() if d.is_dir())
            if paths.versions_dir() else []
        ),
    }


def _machine() -> dict:
    return {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "hostname": platform.node(),
        "user": os.environ.get("USERNAME") or os.environ.get("USER") or "-",
        "now_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def _settings() -> dict:
    s = _safe(config.load)
    return s.model_dump() if hasattr(s, "model_dump") else {"error": s}


def _database(con: sqlite3.Connection | None) -> dict:
    p = paths.db_path()
    out: dict = {"path": str(p), "exists": p.exists(),
                 "size_bytes": p.stat().st_size if p.exists() else 0}
    own = con is None
    try:
        con = con or db.connect()
        out["schema_version"] = db.schema_version(con)
        out["counts"] = db.counts(con)
        out["integrity"] = con.execute("PRAGMA integrity_check").fetchone()[0]
        out["journal_mode"] = con.execute("PRAGMA journal_mode").fetchone()[0]
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
    finally:
        if own and con is not None:
            con.close()
    out["backups"] = _safe(
        lambda: sorted(f.name for f in paths.backups_dir().glob("inventory-*.db"))[-5:]
    )
    return out


def _printer() -> dict:
    from .printer import PrintService
    return _safe(lambda: PrintService().probe())      # type: ignore[return-value]


def report(con: sqlite3.Connection | None = None) -> dict:
    return {
        "versions": _safe(_versions),
        "machine": _safe(_machine),
        "printer": _printer(),
        "database": _database(con),
        "settings": _settings(),
        "paths": {"data_dir": _safe(lambda: str(paths.data_dir())),
                  "logs": _safe(lambda: str(paths.logs_dir()))},
    }


def report_text(con: sqlite3.Connection | None = None, log_lines: int = 200) -> str:
    r = report(con)
    parts = [
        "SynergyScan diagnostics",
        "=" * 60,
        "",
        "Send this whole file to support.",
        "",
    ]
    for section in ("versions", "machine", "printer", "database", "settings", "paths"):
        parts += [section.upper(), "-" * 60,
                  json.dumps(r[section], indent=2, default=str), ""]
    parts += [f"LAST {log_lines} LOG LINES", "-" * 60]
    parts += logs.tail(log_lines) or ["<no log file yet>"]
    return "\n".join(parts) + "\n"
