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


def test_create_item_accepts_the_new_master_fields(con):
    area = db.create_area(con, "Battery Testing")
    category = db.create_category(con, area, "Electrodes")
    location = db.create_location(con, "D1", "Shelf D1")
    item_id = db.create_item(
        con, sku="FOAM-001", name="Fe foam", category_id=category,
        location_id=location, supplier="Supplier A", lead_time_days=30,
        min_qty=5, low_qty=8,
    )
    row = db.get_item(con, item_id)
    assert row["category_id"] == category
    assert row["location_id"] == location
    assert row["supplier"] == "Supplier A"
    assert row["lead_time_days"] == 30
    assert row["low_qty"] == 8


def test_create_item_leaves_new_fields_null_by_default(con, item):
    row = db.get_item(con, item)
    assert row["category_id"] is None
    assert row["location_id"] is None
    assert row["supplier"] is None
    assert row["lead_time_days"] is None
    assert row["low_qty"] is None


def test_update_item_can_set_the_new_fields(con, item):
    category = db.create_category(con, db.create_area(con, "Chemistry"), "Solvents")
    db.update_item(con, item, category_id=category, supplier="New Supplier",
                   low_qty=20)
    row = db.get_item(con, item)
    assert row["category_id"] == category
    assert row["supplier"] == "New Supplier"
    assert row["low_qty"] == 20


def test_low_qty_below_min_qty_is_refused_on_create(con):
    with pytest.raises(ValueError, match="low_qty"):
        db.create_item(con, sku="A-1", name="Widget", min_qty=10, low_qty=5)


def test_low_qty_below_min_qty_is_refused_on_update(con, item):
    # item's min_qty is 10 (see the `item` fixture)
    with pytest.raises(ValueError, match="low_qty"):
        db.update_item(con, item, low_qty=1)


# ------------------------------------------------------------------- areas
def test_create_and_list_areas(con):
    db.create_area(con, "Battery Testing")
    db.create_area(con, "Chemistry")
    assert [a["name"] for a in db.list_areas(con)] == ["Battery Testing", "Chemistry"]


def test_area_name_is_unique_ignoring_case(con):
    db.create_area(con, "Chemistry")
    with pytest.raises(sqlite3.IntegrityError):
        db.create_area(con, "chemistry")


def test_archiving_an_area_hides_it_but_does_not_delete_it(con):
    area = db.create_area(con, "Chemistry")
    db.update_area(con, area, archived=True)
    assert db.list_areas(con) == []
    assert db.get_area(con, area) is not None


# -------------------------------------------------------------- categories
def test_create_and_list_categories_for_an_area(con):
    area = db.create_area(con, "Battery Testing")
    db.create_category(con, area, "Electrodes")
    db.create_category(con, area, "Membranes")
    other = db.create_area(con, "Chemistry")
    db.create_category(con, other, "Solvents")
    assert [c["name"] for c in db.list_categories(con, area_id=area)] == \
        ["Electrodes", "Membranes"]
    assert len(db.list_categories(con)) == 3


def test_category_requires_a_real_area(con):
    with pytest.raises(sqlite3.IntegrityError):
        db.create_category(con, 9999, "Electrodes")


def test_category_name_is_unique_within_an_area_but_not_across_areas(con):
    a = db.create_area(con, "Battery Testing")
    b = db.create_area(con, "Chemistry")
    db.create_category(con, a, "Solvents")
    with pytest.raises(sqlite3.IntegrityError):
        db.create_category(con, a, "solvents")
    db.create_category(con, b, "Solvents")            # different area: fine


# --------------------------------------------------------------- locations
def test_create_and_list_locations(con):
    db.create_location(con, "D1", "Shelf D1")
    db.create_location(con, "C4", "Drawer C4")
    assert [l["code"] for l in db.list_locations(con)] == ["C4", "D1"]


def test_location_code_is_unique(con):
    db.create_location(con, "D1", "Shelf D1")
    with pytest.raises(sqlite3.IntegrityError):
        db.create_location(con, "d1", "Somewhere else")


def test_update_location_fields(con):
    loc = db.create_location(con, "D1", "Shelf D1")
    db.update_location(con, loc, name="Shelf D1 (top)")
    assert db.get_location(con, loc)["name"] == "Shelf D1 (top)"


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


def test_a_movement_without_a_lot_id_still_works(con, item):
    db.add_movement(con, item, 5, "receive")
    assert db.on_hand(con, item) == 5


# --------------------------------------------------------------------- lots
def test_create_and_list_lots_for_an_item(con, item):
    db.create_lot(con, item, "Supplier Lot 4582")
    db.create_lot(con, item, "Supplier Lot 9001")
    assert [l["code"] for l in db.list_lots(con, item)] == \
        ["Supplier Lot 4582", "Supplier Lot 9001"]


def test_lot_requires_a_real_item(con):
    with pytest.raises(sqlite3.IntegrityError):
        db.create_lot(con, 9999, "Some lot")


def test_lot_code_is_unique_per_item_but_not_across_items(con, item):
    other = db.create_item(con, sku="B-2", name="Other")
    db.create_lot(con, item, "L1")
    with pytest.raises(sqlite3.IntegrityError):
        db.create_lot(con, item, "l1")
    db.create_lot(con, other, "L1")                   # different item: fine


def test_lot_on_hand_is_the_sum_of_its_movements(con, item):
    lot = db.create_lot(con, item, "Supplier Lot 4582")
    db.add_movement(con, item, 12, "receive", lot_id=lot)
    db.add_movement(con, item, -3, "issue", lot_id=lot)
    db.add_movement(con, item, 100, "receive")         # unrelated to the lot
    assert db.lot_on_hand(con, lot) == 9
    assert db.on_hand(con, item) == 109


def test_list_lots_carries_each_lots_derived_qty(con, item):
    lot = db.create_lot(con, item, "Supplier Lot 4582")
    db.add_movement(con, item, 12, "receive", lot_id=lot)
    rows = db.list_lots(con, item)
    assert rows[0]["qty"] == 12


def test_archiving_a_lot_hides_it_but_does_not_delete_it(con, item):
    lot = db.create_lot(con, item, "L1")
    db.update_lot(con, lot, archived=True)
    assert db.list_lots(con, item) == []
    assert db.get_lot(con, lot) is not None


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


def test_list_items_exposes_the_new_item_master_columns(con):
    area = db.create_area(con, "Battery Testing")
    category = db.create_category(con, area, "Electrodes")
    location = db.create_location(con, "D1", "Shelf D1")
    db.create_item(con, sku="FOAM-001", name="Fe foam", category_id=category,
                   location_id=location, low_qty=8, min_qty=5,
                   supplier="Supplier A", lead_time_days=30)
    row = db.list_items(con)[0]
    assert row["category_id"] == category
    assert row["location_id"] == location
    assert row["low_qty"] == 8
    assert row["supplier"] == "Supplier A"
    assert row["lead_time_days"] == 30


def test_low_stock_filter(con):
    a = db.create_item(con, sku="LOW", name="Low", min_qty=10)
    b = db.create_item(con, sku="FINE", name="Fine", min_qty=1)
    db.add_movement(con, a, 3, "receive")
    db.add_movement(con, b, 50, "receive")
    low = db.list_items(con, low_only=True)
    assert [r["sku"] for r in low] == ["LOW"]


def test_list_items_can_be_filtered_by_category_and_area(con):
    area = db.create_area(con, "Battery Testing")
    category = db.create_category(con, area, "Electrodes")
    other_area = db.create_area(con, "Chemistry")
    other_category = db.create_category(con, other_area, "Solvents")
    db.create_item(con, sku="A-1", name="In category", category_id=category)
    db.create_item(con, sku="B-1", name="Elsewhere", category_id=other_category)
    db.create_item(con, sku="C-1", name="Uncategorised")

    assert [r["sku"] for r in db.list_items(con, category_id=category)] == ["A-1"]
    assert [r["sku"] for r in db.list_items(con, area_id=area)] == ["A-1"]
    assert [r["sku"] for r in db.list_items(con, area_id=other_area)] == ["B-1"]


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


# --------------------------------------------------------------- availability
def test_availability_is_sufficient_when_no_min_qty_is_set():
    """An item with no reorder point configured is never low or needing reorder."""
    assert db.availability(qty=0, min_qty=0, low_qty=None) == "sufficient"


@pytest.mark.parametrize("qty,min_qty,low_qty,expected", [
    (2, 10, 15, "reorder"),      # at or below the hard threshold
    (10, 10, 15, "reorder"),
    (12, 10, 15, "low"),         # between the two thresholds
    (15, 10, 15, "low"),
    (16, 10, 15, "sufficient"),  # above both
])
def test_availability_tiers_progress_reorder_low_sufficient(qty, min_qty, low_qty, expected):
    assert db.availability(qty, min_qty, low_qty) == expected


def test_availability_without_a_low_qty_threshold_only_has_two_tiers():
    assert db.availability(qty=5, min_qty=10, low_qty=None) == "reorder"
    assert db.availability(qty=11, min_qty=10, low_qty=None) == "sufficient"


# ------------------------------------------------------- generated SKUs
def _category(con, name, **kw):
    return db.create_category(con, db.create_area(con, f"Area {name}"), name, **kw)


def test_prefix_is_suggested_from_the_category_name(con):
    assert db.get_category(con, _category(con, "Foam"))["prefix"] == "FOAM"
    assert db.get_category(con, _category(con, "Bipolar Plates"))["prefix"] == "BP"
    assert db.get_category(con, _category(con, "Membranes"))["prefix"] == "MEMB"


def test_taken_prefix_gets_a_numeric_suffix(con):
    _category(con, "Foam")
    assert db.get_category(con, _category(con, "Foam rolls"))["prefix"] == "FR"
    assert db.get_category(con, _category(con, "Foams"))["prefix"] == "FOAM2"


def test_manual_prefix_is_validated(con):
    _category(con, "Foam")
    for bad in ("foam", "ITM", "no way"):
        with pytest.raises(ValueError):
            _category(con, f"X{bad}", prefix=bad)


def test_blank_sku_is_generated_and_never_reused(con):
    cat = _category(con, "Fittings")
    a = db.create_item(con, sku="", name="a", category_id=cat)
    b = db.create_item(con, sku="", name="b", category_id=cat)
    assert db.get_item(con, a)["sku"] == "FITT-0001"
    assert db.get_item(con, b)["sku"] == "FITT-0002"
    db.update_item(con, b, archived=1)
    assert db.get_item(con, db.create_item(con, sku="", name="c", category_id=cat))["sku"] == "FITT-0003"


def test_generation_skips_skus_already_in_use(con):
    cat = _category(con, "Fittings")
    db.create_item(con, sku="fitt-0001", name="manual", category_id=cat)
    assert db.get_item(con, db.create_item(con, sku="", name="x", category_id=cat))["sku"] == "FITT-0002"


def test_uncategorised_items_use_the_fallback_prefix(con):
    a = db.create_item(con, sku="", name="a")
    b = db.create_item(con, sku="", name="b")
    assert [db.get_item(con, i)["sku"] for i in (a, b)] == ["ITM-0001", "ITM-0002"]


def test_prefix_locks_once_the_category_has_items(con):
    cat = _category(con, "Foam")
    db.update_category(con, cat, prefix="FM")          # still free to change
    db.create_item(con, sku="", name="a", category_id=cat)
    with pytest.raises(ValueError):
        db.update_category(con, cat, prefix="FOAM")
    db.update_category(con, cat, prefix="FM")          # unchanged value is fine


def test_existing_categories_are_backfilled(con):
    area = db.create_area(con, "Old")
    con.execute("INSERT INTO categories (area_id, name) VALUES (?, 'Tubing')", (area,))
    assert [r["prefix"] for r in db.list_categories(con)] == ["TUBI"]
