"""Back up, migrate, and restore if it goes wrong.

Run as a subprocess with the **new** release's interpreter, after the self-test
and before the pointer moves:

    <new>/.venv/Scripts/python -m synergyscan.selfupdate.migrate

Ordering is the whole design. The backup is taken *before* any schema change,
and a failure restores it and exits non-zero, which leaves the pointer where it
was and the old release running against a database it still understands. Getting
this backwards - migrating first, backing up after - is how a bad release
becomes a data-loss incident instead of a logged failure.

The backup uses SQLite's online backup API rather than copying the file. With
WAL enabled there can be committed transactions sitting in inventory.db-wal
that a plain file copy would silently miss.
"""

from __future__ import annotations

import json
import logging
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

KEEP_BACKUPS = 10


def backup(db_path: Path, backups_dir: Path, tag: str = "preupdate") -> Path | None:
    """Consistent copy of the database. None if there is no database yet."""
    if not db_path.exists():
        log.info("no database at %s yet; nothing to back up", db_path)
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = backups_dir / f"inventory-{tag}-{stamp}.db"
    src = sqlite3.connect(db_path, timeout=30.0)
    try:
        dst = sqlite3.connect(dest)
        try:
            src.backup(dst)          # online backup: includes anything in the WAL
        finally:
            dst.close()
    finally:
        src.close()
    log.info("backed up database to %s (%d bytes)", dest, dest.stat().st_size)
    return dest


def restore(backup_path: Path, db_path: Path) -> None:
    """Put a backup back. Removes stale WAL sidecars so SQLite cannot mix them."""
    for sidecar in (db_path.with_name(db_path.name + "-wal"),
                    db_path.with_name(db_path.name + "-shm")):
        sidecar.unlink(missing_ok=True)
    shutil.copy2(backup_path, db_path)
    log.warning("restored database from %s", backup_path)


def prune(backups_dir: Path, keep: int = KEEP_BACKUPS) -> None:
    files = sorted(backups_dir.glob("inventory-*.db"))
    for f in files[:-keep] if len(files) > keep else []:
        try:
            f.unlink()
        except OSError:
            log.warning("could not remove old backup %s", f)


def run() -> dict:
    """Back up, migrate, restore on failure. Returns a report."""
    from .. import db as dbmod
    from .. import migrations, paths

    db_path = paths.db_path()
    report: dict = {"ok": False, "db": str(db_path)}

    try:
        saved = backup(db_path, paths.backups_dir())
    except Exception as e:
        # If we cannot take a backup we do not migrate. Refusing an update is
        # always recoverable; migrating without a way back is not.
        report["error"] = f"backup failed, refusing to migrate: {e}"
        log.exception("backup failed")
        return report
    report["backup"] = str(saved) if saved else None

    con = dbmod.connect(db_path)
    try:
        before = migrations.current_version(con)
        pending = [p.name for _, p in migrations.pending(con)]
        report.update(schema_before=before, pending=pending)
        applied = migrations.apply_all(con)
        report.update(applied=applied, schema_after=migrations.current_version(con))
        report["ok"] = True
    except Exception as e:
        report["error"] = f"{type(e).__name__}: {e}"
        log.exception("migration failed")
        con.close()
        con = None  # type: ignore[assignment]
        if saved:
            try:
                restore(saved, db_path)
                report["restored"] = True
            except Exception as re:
                # The worst case, and the one that needs a human: say so loudly
                # and name the file they need.
                report["restored"] = False
                report["restore_error"] = str(re)
                log.critical(
                    "COULD NOT RESTORE THE DATABASE. A good copy is at %s - "
                    "do not start the app until it has been put back.", saved
                )
        return report
    finally:
        if con is not None:
            con.close()

    prune(paths.backups_dir())
    return report


def main() -> int:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(levelname)s %(name)s: %(message)s")
    report = run()
    json.dump(report, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
