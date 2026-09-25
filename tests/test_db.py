"""Database and migration tests.

The ledger is the point: stock is derived from movements, never stored. These
tests exist to stop anyone "optimising" that into a quantity column.
"""

from __future__ import annotations

import sqlite3

import pytest

from synergyscan import db, migrations


# ---------------------------------------------------------------- migrations
def test_migrations_are_contiguous_and_well_named():
    avail = migrations.available()
    assert avail
    assert [n for n, _ in avail] == list(range(1, len(avail) + 1))


def test_apply_all_is_idempotent(con):
    assert migrations.pending(con) == []
    assert migrations.apply_all(con) == []


def test_schema_version_reflects_what_was_applied(con):
    assert db.schema_version(con) == len(migrations.available())


def test_a_failing_migration_leaves_the_schema_untouched(tmp_path, monkeypatch):
    """Each migration is one transaction, including the row that records it."""
    bad = tmp_path / "002_bad.sql"
    bad.write_text("CREATE TABLE ok_so_far (id INTEGER);\n"
                   "CREATE TABLE ok_so_far (id INTEGER);\n", encoding="utf-8")
    real = migrations.available()
    monkeypatch.setattr(migrations, "available", lambda: real + [(len(real) + 1, bad)])

    con = sqlite3.connect(":memory:", isolation_level=None)
    try:
        with pytest.raises(sqlite3.Error):
            migrations.apply_all(con)
        tables = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert "ok_so_far" not in tables
        assert migrations.current_version(con) == len(real)
    finally:
        con.close()


def test_a_migration_containing_its_own_transaction_is_rejected(tmp_path, monkeypatch):
    bad = tmp_path / "002_txn.sql"
    bad.write_text("BEGIN;\nCREATE TABLE x (id INTEGER);\nCOMMIT;\n", encoding="utf-8")
    real = migrations.available()
    monkeypatch.setattr(migrations, "available", lambda: real + [(len(real) + 1, bad)])
    con = sqlite3.connect(":memory:", isolation_level=None)
    try:
        with pytest.raises(ValueError, match="transaction control"):
            migrations.apply_all(con)
    finally:
        con.close()


# --------------------------------------------------------------------- items
def test_create_and_read_an_item(con):
    item_id = db.create_item(con, sku="A-1", name="Widget", min_qty=5)
    row = db.get_item(con, item_id)
    assert row["sku"] == "A-1"
    assert row["unit"] == "pcs"
    assert row["min_qty"] == 5


def test_sku_is_unique(con, item):
    with pytest.raises(sqlite3.IntegrityError):
        db.create_item(con, sku="SKU-0042", name="Duplicate")


def test_sku_matching_ignores_case(con, item):
    assert db.find_by_code(con, "sku-0042") is not None


def test_find_by_code_prefers_the_barcode_column(con):
    a = db.create_item(con, sku="SHARED", name="By SKU")
    b = db.create_item(con, sku="B-2", name="By barcode", barcode="SHARED")
    found = db.find_by_code(con, "SHARED")
    assert found["id"] == b
    assert found["id"] != a


def test_find_by_code_ignores_surrounding_whitespace(con, item):
    # Some scanners append a carriage return or a space before Enter.
    assert db.find_by_code(con, "  SKU-0042 \r\n") is not None


def test_find_by_code_misses_cleanly(con):
    assert db.find_by_code(con, "NOTHING") is None
    assert db.find_by_code(con, "") is None


def test_archived_items_are_not_found_by_scanning(con, item):
    db.update_item(con, item, archived=1)
    assert db.find_by_code(con, "SKU-0042") is None


# ----------------------------------------------------------------- movements
def test_stock_starts_at_zero(con, item):
    assert db.on_hand(con, item) == 0


def test_stock_is_the_sum_of_movements(con, item):
    db.add_movement(con, item, 100, "receive")
    db.add_movement(con, item, -30, "issue")
    db.add_movement(con, item, 5, "adjust")
    assert db.on_hand(con, item) == 75


def test_a_zero_movement_is_refused(con, item):
    with pytest.raises(ValueError, match="zero"):
        db.add_movement(con, item, 0, "receive")


def test_an_unknown_reason_is_refused(con, item):
    with pytest.raises(ValueError, match="unknown reason"):
        db.add_movement(con, item, 1, "shrinkage")


def test_the_database_also_rejects_a_bad_reason(con, item):
    """Belt and braces: the CHECK constraint backs up the Python guard."""
    with pytest.raises(sqlite3.IntegrityError):
        con.execute(
            "INSERT INTO stock_movements (item_id, delta, reason) VALUES (?, ?, ?)",
            (item, 1, "nonsense"))


def test_movements_reference_a_real_item(con):
    with pytest.raises(sqlite3.IntegrityError):
        db.add_movement(con, 9999, 1, "receive")


def test_history_is_preserved_in_order(con, item):
    db.add_movement(con, item, 10, "receive", note="first")
    db.add_movement(con, item, -2, "issue", note="second")
    rows = db.movements(con, item)
    assert [r["note"] for r in rows] == ["second", "first"]


# ----------------------------------------------------------------- stocktake
def test_stocktake_appends_the_difference_rather_than_overwriting(con, item):
    db.add_movement(con, item, 100, "receive")
    db.set_stocktake(con, item, 92)
    assert db.on_hand(con, item) == 92
    rows = db.movements(con, item)
    assert len(rows) == 2                       # the receipt is still there
    assert rows[0]["reason"] == "stocktake"
    assert rows[0]["delta"] == -8
    assert "counted 92, was 100" in rows[0]["note"]


def test_stocktake_that_matches_records_nothing(con, item):
    db.add_movement(con, item, 50, "receive")
    assert db.set_stocktake(con, item, 50) is None
    assert len(db.movements(con, item)) == 1


def test_stocktake_upwards_works(con, item):
    db.add_movement(con, item, 10, "receive")
    db.set_stocktake(con, item, 14)
    assert db.on_hand(con, item) == 14


# --------------------------------------------------------------------- views
def test_stock_on_hand_view_lists_derived_quantities(con, item):
    db.add_movement(con, item, 7, "receive")
    rows = db.list_items(con)
    assert len(rows) == 1
    assert rows[0]["qty"] == 7
    assert rows[0]["sku"] == "SKU-0042"


def test_low_stock_filter(con):
    a = db.create_item(con, sku="LOW", name="Low", min_qty=10)
    b = db.create_item(con, sku="FINE", name="Fine", min_qty=1)
    db.add_movement(con, a, 3, "receive")
    db.add_movement(con, b, 50, "receive")
    low = db.list_items(con, low_only=True)
    assert [r["sku"] for r in low] == ["LOW"]


def test_an_item_with_no_movements_still_appears(con, item):
    rows = db.list_items(con)
    assert rows[0]["qty"] == 0


def test_search_matches_name_sku_and_barcode(con):
    db.create_item(con, sku="X-1", name="Pasta", barcode="8001234")
    assert len(db.list_items(con, search="Past")) == 1
    assert len(db.list_items(con, search="X-1")) == 1
    assert len(db.list_items(con, search="800123")) == 1
    assert len(db.list_items(con, search="zzz")) == 0


# ---------------------------------------------------------------- print jobs
def test_print_job_log_upserts(con, item):
    db.log_print_job(con, "job1", {"lines": ["x"]}, "queued",
                     queued_at="2026-09-25T10:00:00Z", item_id=item)
    db.log_print_job(con, "job1", {"lines": ["x"]}, "done",
                     queued_at="2026-09-25T10:00:00Z", item_id=item,
                     finished_at="2026-09-25T10:00:03Z")
    row = con.execute("SELECT * FROM print_jobs WHERE id = 'job1'").fetchone()
    assert row["state"] == "done"
    assert row["finished_at"] == "2026-09-25T10:00:03Z"
    assert con.execute("SELECT COUNT(*) c FROM print_jobs").fetchone()["c"] == 1


# -------------------------------------------------------------------- pragma
def test_wal_and_foreign_keys_are_on(con):
    assert con.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    assert con.execute("PRAGMA foreign_keys").fetchone()[0] == 1
