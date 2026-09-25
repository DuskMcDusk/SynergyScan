"""Backup-and-restore tests.

The ordering these protect - back up first, migrate second, restore on failure -
is what keeps a bad release from becoming a data-loss incident.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from synergyscan import db, migrations, paths
from synergyscan.selfupdate import migrate as M


@pytest.fixture
def seeded(isolated_data: Path) -> Path:
    """A database with real content and a committed WAL."""
    con = db.init(isolated_data / "inventory.db")
    item = db.create_item(con, sku="SKU-1", name="Thing")
    db.add_movement(con, item, 42, "receive")
    con.close()
    return isolated_data / "inventory.db"


def test_backup_captures_committed_wal_data(seeded: Path, isolated_data: Path):
    """A plain file copy can miss transactions still sitting in the -wal file.

    This is why backup() uses SQLite's online backup API. To prove the point,
    the connection is left open with WAL data pending.
    """
    con = db.connect(seeded)
    item = db.create_item(con, sku="SKU-2", name="Late arrival")
    db.add_movement(con, item, 7, "receive")

    dest = M.backup(seeded, paths.backups_dir())
    assert dest is not None and dest.exists()

    con.close()
    copy = sqlite3.connect(dest)
    try:
        names = {r[0] for r in copy.execute("SELECT name FROM items")}
        assert names == {"Thing", "Late arrival"}
    finally:
        copy.close()


def test_backup_returns_none_when_there_is_no_database(isolated_data: Path):
    assert M.backup(isolated_data / "nothing.db", paths.backups_dir()) is None


def test_restore_puts_the_data_back(seeded: Path, isolated_data: Path):
    saved = M.backup(seeded, paths.backups_dir())
    assert saved is not None

    con = db.connect(seeded)
    db.create_item(con, sku="SKU-9", name="Added after the backup")
    con.close()

    M.restore(saved, seeded)

    con = db.connect(seeded)
    try:
        names = {r[0] for r in con.execute("SELECT name FROM items")}
        assert names == {"Thing"}
    finally:
        con.close()


def test_restore_removes_stale_wal_sidecars(seeded: Path):
    """Leaving a -wal behind lets SQLite mix the restored file with newer pages."""
    saved = M.backup(seeded, paths.backups_dir())
    wal = seeded.with_name(seeded.name + "-wal")
    wal.write_bytes(b"stale")
    M.restore(saved, seeded)
    assert not wal.exists()


def test_run_reports_success_and_records_the_backup(seeded: Path):
    report = M.run()
    assert report["ok"] is True
    assert report["pending"] == []
    assert Path(report["backup"]).exists()


def test_run_on_a_fresh_machine_creates_the_schema(isolated_data: Path):
    report = M.run()
    assert report["ok"] is True
    assert report["backup"] is None            # nothing existed to back up
    assert report["applied"] == [n for n, _ in migrations.available()]


def test_a_failed_migration_restores_the_backup(seeded: Path, monkeypatch):
    """The property the whole ordering exists for."""
    def explode(con):
        # Destroy real data, then fail - exactly the shape of a half-applied
        # migration that renames or rewrites a table.
        con.execute("DELETE FROM stock_movements")
        con.execute("UPDATE items SET name = 'CLOBBERED'")
        raise sqlite3.OperationalError("migration blew up")

    monkeypatch.setattr(migrations, "apply_all", explode)

    report = M.run()
    assert report["ok"] is False
    assert report["restored"] is True
    assert "blew up" in report["error"]

    con = db.connect(seeded)
    try:
        names = {r[0] for r in con.execute("SELECT name FROM items")}
        assert names == {"Thing"}              # the data survived
        item = con.execute("SELECT id FROM items").fetchone()["id"]
        assert db.on_hand(con, item) == 42     # and so did the ledger
    finally:
        con.close()


def test_migration_is_refused_when_the_backup_cannot_be_taken(seeded, monkeypatch):
    """Refusing an update is recoverable; migrating with no way back is not."""
    applied = []
    monkeypatch.setattr(M, "backup", lambda *a, **k: (_ for _ in ()).throw(
        OSError("disk full")))
    monkeypatch.setattr(migrations, "apply_all", lambda con: applied.append(1))

    report = M.run()
    assert report["ok"] is False
    assert "refusing to migrate" in report["error"]
    assert applied == []                       # nothing was attempted


def test_prune_keeps_only_the_most_recent_backups(isolated_data: Path):
    d = paths.backups_dir()
    for i in range(15):
        (d / f"inventory-preupdate-2026092{i:02d}T000000Z.db").write_bytes(b"x")
    M.prune(d, keep=10)
    assert len(list(d.glob("inventory-*.db"))) == 10


def test_prune_keeps_the_newest_by_name(isolated_data: Path):
    d = paths.backups_dir()
    names = [f"inventory-preupdate-2026{m:02d}01T000000Z.db" for m in range(1, 6)]
    for n in names:
        (d / n).write_bytes(b"x")
    M.prune(d, keep=2)
    assert sorted(p.name for p in d.glob("*.db")) == names[-2:]
