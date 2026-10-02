"""Forward-only SQL migrations.

Plain numbered .sql files in versions/, applied in order inside one
transaction each, tracked in schema_migrations. No Alembic: the schema is
small, and the update path benefits far more from being obvious than from
being clever.

The safety net is not in this module. selfupdate.migrate backs the database up
before calling apply_all and restores it if anything raises - see the comment
there for why that ordering matters.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from pathlib import Path

log = logging.getLogger(__name__)

VERSIONS = Path(__file__).parent / "versions"
NAME_RE = re.compile(r"^(\d{3})_[a-z0-9_]+\.sql$")


def available() -> list[tuple[int, Path]]:
    """Migration files, ordered, validated for name and contiguity."""
    found: list[tuple[int, Path]] = []
    for p in sorted(VERSIONS.glob("*.sql")):
        m = NAME_RE.match(p.name)
        if not m:
            raise ValueError(
                f"migration {p.name!r} is misnamed; expected NNN_lower_snake.sql"
            )
        found.append((int(m.group(1)), p))
    for i, (num, p) in enumerate(found, start=1):
        if num != i:
            raise ValueError(
                f"migration numbering has a gap or duplicate at {p.name} "
                f"(expected {i:03d})"
            )
    return found


def _ensure_table(con: sqlite3.Connection) -> None:
    con.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        " version INTEGER PRIMARY KEY,"
        " name TEXT NOT NULL,"
        " applied_at TEXT NOT NULL DEFAULT (datetime('now')))"
    )


def applied(con: sqlite3.Connection) -> set[int]:
    _ensure_table(con)
    return {r[0] for r in con.execute("SELECT version FROM schema_migrations")}


def current_version(con: sqlite3.Connection) -> int:
    done = applied(con)
    return max(done) if done else 0


def pending(con: sqlite3.Connection) -> list[tuple[int, Path]]:
    done = applied(con)
    return [(n, p) for n, p in available() if n not in done]


def apply_all(con: sqlite3.Connection) -> list[int]:
    """Apply every pending migration. Returns the versions applied.

    Each migration runs in one explicit transaction, together with the row that
    records it - so a migration is either fully applied and recorded, or not
    applied at all. SQLite makes DDL transactional, so this really does hold
    for CREATE TABLE and friends.

    The BEGIN/COMMIT have to be inside the script we hand to executescript:
    sqlite3.executescript commits any open transaction before it starts, so
    wrapping the call in `with con:` would quietly do nothing. Migration files
    must therefore not contain their own BEGIN, COMMIT or ROLLBACK.

    Foreign key enforcement is switched off while a migration runs, because
    rebuilding a table that other tables reference (drop + rename) is otherwise
    impossible - and then checked with PRAGMA foreign_key_check before the
    commit, so a migration that leaves a dangling reference is rolled back.
    """
    _ensure_table(con)
    fk_was_on = bool(con.execute("PRAGMA foreign_keys").fetchone()[0])
    if fk_was_on:
        con.execute("PRAGMA foreign_keys=OFF")     # a no-op inside a transaction
    try:
        return _apply_pending(con)
    finally:
        if fk_was_on:
            con.execute("PRAGMA foreign_keys=ON")


def _apply_pending(con: sqlite3.Connection) -> list[int]:
    done: list[int] = []
    for num, path in pending(con):
        body = path.read_text(encoding="utf-8")
        if re.search(r"^\s*(BEGIN|COMMIT|ROLLBACK)\b", body, re.I | re.M):
            raise ValueError(
                f"migration {path.name} contains transaction control; "
                "apply_all manages the transaction itself"
            )
        log.info("applying migration %s", path.name)
        script = (
            "BEGIN;\n"
            f"{body}\n"
            "INSERT INTO schema_migrations (version, name) VALUES "
            f"({num}, '{path.name}');\n"
        )
        try:
            con.executescript(script)          # leaves the transaction open
            broken = con.execute("PRAGMA foreign_key_check").fetchall()
            if broken:
                raise sqlite3.IntegrityError(
                    f"migration {path.name} left {len(broken)} dangling reference(s)")
            con.execute("COMMIT")
        except sqlite3.Error:
            log.exception("migration %s failed; rolling back", path.name)
            try:
                con.execute("ROLLBACK")
            except sqlite3.Error:
                pass          # nothing open to roll back
            raise
        done.append(num)
    return done
