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
    sub, pos = _sub(con, "CH", "FOM"), _pos(con, "RnD-WH-A-0.1")
    item_id = db.create_item(
        con, sku="FOAM-001", name="Fe foam", subcategory_id=sub,
        position_id=pos, supplier="Supplier A", lead_time_days=30,
        min_qty=5, low_qty=8, tech_ref=" V-1234 ",
    )
    row = db.get_item(con, item_id)
    assert row["subcategory_id"] == sub
    assert row["position_id"] == pos
    assert row["tech_ref"] == "V-1234"
    assert row["supplier"] == "Supplier A"
    assert row["lead_time_days"] == 30
    assert row["low_qty"] == 8


def test_create_item_leaves_new_fields_null_by_default(con, item):
    row = db.get_item(con, item)
    assert row["subcategory_id"] is None
    assert row["family_id"] is None
    assert row["position_id"] is None
    assert row["tech_ref"] is None
    assert row["supplier"] is None
    assert row["lead_time_days"] is None
    assert row["low_qty"] is None


def test_update_item_can_set_the_new_fields(con, item):
    sub = _sub(con, "CH", "REA")
    db.update_item(con, item, subcategory_id=sub, supplier="New Supplier",
                   low_qty=20)
    row = db.get_item(con, item)
    assert row["subcategory_id"] == sub
    assert row["supplier"] == "New Supplier"
    assert row["low_qty"] == 20


def test_low_qty_below_min_qty_is_refused_on_create(con):
    with pytest.raises(ValueError, match="low_qty"):
        db.create_item(con, sku="A-1", name="Widget", min_qty=10, low_qty=5)


def test_low_qty_below_min_qty_is_refused_on_update(con, item):
    # item's min_qty is 10 (see the `item` fixture)
    with pytest.raises(ValueError, match="low_qty"):
        db.update_item(con, item, low_qty=1)


# ------------------------------------------------- classification & locations
def _macro(con, code):
    return next(r["id"] for r in db.list_nodes(con, "macro") if r["code"] == code)


def _sub(con, macro, code):
    return next(r["id"] for r in db.list_nodes(con, "subcategory", _macro(con, macro))
                if r["code"] == code)


def _pos(con, location_code):
    return db.find_position(con, location_code)["id"]


def test_the_proposal_lists_are_preloaded(con):
    """Slides 17-27: five macro-categories, 56 subcategories, the CH-REA families,
    three facilities and the 44 positions of the RnD warehouse shelves."""
    per_macro = {m["code"]: len(db.list_nodes(con, "subcategory", m["id"]))
                 for m in db.list_nodes(con, "macro")}
    assert per_macro == {"EL": 8, "ME": 14, "CH": 6, "HY": 14, "CI": 14}
    assert [f["code"] for f in db.list_nodes(con, "family", _sub(con, "CH", "REA"))] == \
        ["IS", "MN", "MP", "OS", "PA", "SV"]
    assert len(db.list_nodes(con, "family")) == 6
    assert {f["code"] for f in db.list_nodes(con, "facility")} == {"LsT", "HQ", "RnD"}
    codes = [p["location_code"] for p in db.list_nodes(con, "position")]
    assert len(codes) == 44
    assert sum(c.startswith("RnD-WH-A-") for c in codes) == 14
    assert codes[:4] == ["RnD-WH-A-0.1", "RnD-WH-A-0.2", "RnD-WH-A-0.3", "RnD-WH-A-1.1"]


def test_classification_codes_follow_the_2_3_2_format(con):
    m = db.create_node(con, "macro", "pn", "Pneumatics")
    assert db.get_node(con, "macro", m)["code"] == "PN"           # capitals
    for kind, parent, bad in (("macro", None, "PNE"), ("subcategory", m, "VA"),
                              ("subcategory", m, "V4L"), ("family", None, "A")):
        with pytest.raises(ValueError):
            db.create_node(con, kind, bad, "x", parent)
    s = db.create_node(con, "subcategory", "val", "Valves", m)
    db.create_node(con, "family", "ss", "Solenoid", s)


def test_codes_are_unique_within_their_parent_only(con):
    """SPR is both ME Springs and CI Spare equipment, as in the slides."""
    assert _sub(con, "ME", "SPR") != _sub(con, "CI", "SPR")
    with pytest.raises(sqlite3.IntegrityError):
        db.create_node(con, "subcategory", "spr", "Again", _macro(con, "ME"))


def test_a_node_needs_its_parent(con):
    with pytest.raises(ValueError):
        db.create_node(con, "subcategory", "ABC", "No macro")
    with pytest.raises(sqlite3.IntegrityError):
        db.create_node(con, "subcategory", "ABC", "Bad macro", 9999)


def test_location_codes_keep_their_case(con):
    f = db.create_node(con, "facility", "Lab", "Laboratory")
    assert db.get_node(con, "facility", f)["code"] == "Lab"
    with pytest.raises(sqlite3.IntegrityError):
        db.create_node(con, "facility", "rnd", "Clash ignoring case")


def test_a_position_stores_its_full_scannable_code(con):
    hq = next(f["id"] for f in db.list_nodes(con, "facility") if f["code"] == "HQ")
    area = db.create_node(con, "area", "OF", "Office", hq)
    unit = db.create_node(con, "unit", "D", "Drawer unit", area)
    pos = db.create_node(con, "position", "1.2", "", unit, note="Spare fuses")
    assert db.get_node(con, "position", pos)["location_code"] == "HQ-OF-D-1.2"
    assert db.find_position(con, "hq-of-d-1.2")["id"] == pos
    with pytest.raises(ValueError):
        db.create_node(con, "position", "top", "", unit)


def test_renaming_a_location_code_rewrites_the_rack_codes_below(con):
    unit_a = db.get_node(con, "position", _pos(con, "RnD-WH-A-0.1"))["unit_id"]
    db.update_node(con, "unit", unit_a, code="AA")
    assert db.find_position(con, "RnD-WH-A-0.1") is None
    assert db.find_position(con, "RnD-WH-AA-0.1") is not None
    assert db.find_position(con, "RnD-WH-B-0.1") is not None      # other units untouched


def test_archiving_a_node_hides_its_whole_branch_and_restoring_brings_it_back(con):
    ch = _macro(con, "CH")
    rea = _sub(con, "CH", "REA")
    db.update_node(con, "family", db.list_nodes(con, "family", rea)[0]["id"], archived=1)
    db.update_node(con, "macro", ch, archived=1)
    assert db.list_nodes(con, "subcategory", ch) == []
    assert db.list_nodes(con, "family", rea) == []
    assert len(db.list_nodes(con, "family", rea, include_archived=True)) == 6
    db.update_node(con, "macro", ch, archived=0)
    assert len(db.list_nodes(con, "subcategory", ch)) == 6
    assert len(db.list_nodes(con, "family", rea)) == 5             # the one archived on its own stays


def test_an_archived_position_is_not_found_by_scanning(con):
    pos = _pos(con, "RnD-WH-A-0.1")
    db.update_node(con, "position", pos, archived=1)
    assert db.find_position(con, "RnD-WH-A-0.1") is None


def test_moving_an_item_never_changes_its_code(con):
    item = db.create_item(con, sku="", name="Tube", subcategory_id=_sub(con, "HY", "TUB"),
                          position_id=_pos(con, "RnD-WH-C-1.1"))
    db.update_item(con, item, position_id=_pos(con, "RnD-WH-A-2.3"))
    row = db.get_item(con, item)
    assert (row["sku"], row["position_id"]) == ("HY-TUB-0001", _pos(con, "RnD-WH-A-2.3"))


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
    sub, pos = _sub(con, "CH", "FOM"), _pos(con, "RnD-WH-B-2.2")
    db.create_item(con, sku="FOAM-001", name="Fe foam", subcategory_id=sub,
                   position_id=pos, low_qty=8, min_qty=5,
                   supplier="Supplier A", lead_time_days=30)
    row = db.list_items(con)[0]
    assert row["subcategory_id"] == sub
    assert row["position_id"] == pos
    assert row["location_code"] == "RnD-WH-B-2.2"
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


def test_list_items_filters_by_either_tree(con):
    """Classification and location are independent: each filters on its own."""
    a = db.create_item(con, sku="", name="Pump", subcategory_id=_sub(con, "HY", "PMP"),
                       position_id=_pos(con, "RnD-WH-A-3.3"))
    b = db.create_item(con, sku="", name="Tube", subcategory_id=_sub(con, "HY", "TUB"),
                       position_id=_pos(con, "RnD-WH-C-1.1"))
    c = db.create_item(con, sku="", name="KOH", subcategory_id=_sub(con, "CH", "REA"))

    def ids(**f):
        return {r["item_id"] for r in db.list_items(con, **f)}

    unit_a = db.get_node(con, "position", _pos(con, "RnD-WH-A-0.1"))["unit_id"]
    wh = db.get_node(con, "unit", unit_a)["area_id"]
    rnd = db.get_node(con, "area", wh)["facility_id"]
    assert ids(macro_id=_macro(con, "HY")) == {a, b}
    assert ids(subcategory_id=_sub(con, "HY", "TUB")) == {b}
    assert ids(facility_id=rnd) == ids(area_id=wh) == {a, b}
    assert ids(unit_id=unit_a) == {a}
    assert ids(macro_id=_macro(con, "HY"), unit_id=unit_a) == {a}
    assert ids(position_id=_pos(con, "RnD-WH-C-1.1")) == {b}
    assert c not in ids(facility_id=rnd)


def test_an_item_with_no_movements_still_appears(con, item):
    rows = db.list_items(con)
    assert rows[0]["qty"] == 0


def test_search_matches_name_sku_barcode_tech_ref_and_location(con):
    db.create_item(con, sku="X-1", name="Pasta", barcode="8001234", tech_ref="VLT-77",
                   position_id=_pos(con, "RnD-WH-B-0.3"))
    assert len(db.list_items(con, search="Past")) == 1
    assert len(db.list_items(con, search="X-1")) == 1
    assert len(db.list_items(con, search="800123")) == 1
    assert len(db.list_items(con, search="vlt-7")) == 1
    assert len(db.list_items(con, search="WH-B-0.3")) == 1
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


# --------------------------------------------------------- generated codes
def _family(con, sub, code):
    return next(f["id"] for f in db.list_nodes(con, "family", sub) if f["code"] == code)


def test_codes_are_built_from_the_chosen_levels(con):
    tub, rea, frm = _sub(con, "HY", "TUB"), _sub(con, "CH", "REA"), _sub(con, "ME", "FRM")
    fl = db.create_node(con, "family", "FL", "Flowframe", frm)
    assert db.next_sku(con, tub) == "HY-TUB-0001"
    assert db.next_sku(con, rea, _family(con, rea, "MN")) == "CH-REA-MN-0001"
    assert db.next_sku(con, frm, fl) == "ME-FRM-FL-0001"


def test_the_serial_counts_per_full_prefix(con):
    rea = _sub(con, "CH", "REA")
    mn, sv = _family(con, rea, "MN"), _family(con, rea, "SV")
    made = [db.create_item(con, sku="", name=n, subcategory_id=rea, family_id=f)
            for n, f in (("a", mn), ("b", mn), ("c", sv), ("d", None))]
    assert [db.get_item(con, i)["sku"] for i in made] == \
        ["CH-REA-MN-0001", "CH-REA-MN-0002", "CH-REA-SV-0001", "CH-REA-0001"]


def test_a_family_alone_implies_its_subcategory(con):
    rea = _sub(con, "CH", "REA")
    item = db.create_item(con, sku="", name="KOH", family_id=_family(con, rea, "MN"))
    assert db.get_item(con, item)["subcategory_id"] == rea


def test_a_family_must_belong_to_the_subcategory(con):
    rea = _sub(con, "CH", "REA")
    with pytest.raises(ValueError, match="different subcategory"):
        db.create_item(con, sku="", name="x", subcategory_id=_sub(con, "HY", "TUB"),
                       family_id=_family(con, rea, "MN"))


def test_preview_does_not_consume_the_number(con):
    tub = _sub(con, "HY", "TUB")
    assert db.next_sku(con, tub, reserve=False) == db.next_sku(con, tub, reserve=False)
    assert db.next_sku(con, tub) == "HY-TUB-0001"
    assert db.next_sku(con, tub, reserve=False) == "HY-TUB-0002"


def test_numbers_are_never_reused_and_taken_codes_are_skipped(con):
    tub = _sub(con, "HY", "TUB")
    db.create_item(con, sku="hy-tub-0001", name="typed by hand", subcategory_id=tub)
    a = db.create_item(con, sku="", name="a", subcategory_id=tub)
    db.update_item(con, a, archived=1)
    b = db.create_item(con, sku="", name="b", subcategory_id=tub)
    assert [db.get_item(con, i)["sku"] for i in (a, b)] == ["HY-TUB-0002", "HY-TUB-0003"]


def test_unclassified_items_use_the_fallback_prefix(con):
    a = db.create_item(con, sku="", name="a")
    b = db.create_item(con, sku="", name="b")
    assert [db.get_item(con, i)["sku"] for i in (a, b)] == ["ITM-0001", "ITM-0002"]


def test_a_blank_sku_on_edit_asks_for_a_new_code(con):
    item = db.create_item(con, sku="", name="a")
    db.update_item(con, item, subcategory_id=_sub(con, "HY", "PIP"))
    assert db.get_item(con, item)["sku"] == "ITM-0001"        # reclassifying keeps the code
    db.update_item(con, item, sku="")
    assert db.get_item(con, item)["sku"] == "HY-PIP-0001"


def test_classification_codes_lock_once_items_use_them(con):
    hy, tub = _macro(con, "HY"), _sub(con, "HY", "TUB")
    db.update_node(con, "subcategory", tub, code="TBE")           # still free to change
    db.create_item(con, sku="", name="a", subcategory_id=tub)
    for kind, node, code in (("subcategory", tub, "ZZZ"), ("macro", hy, "ZZ")):
        with pytest.raises(ValueError, match="locked"):
            db.update_node(con, kind, node, code=code)
    db.update_node(con, "subcategory", tub, code="TBE", name="Tubes")   # same code is fine


def test_a_new_subcategory_drops_the_old_family(con):
    rea, tub = _sub(con, "CH", "REA"), _sub(con, "HY", "TUB")
    item = db.create_item(con, sku="", name="x", family_id=_family(con, rea, "MN"))
    db.update_item(con, item, subcategory_id=tub)
    row = db.get_item(con, item)
    assert (row["subcategory_id"], row["family_id"]) == (tub, None)


def test_clearing_the_subcategory_clears_the_family_too(con):
    rea = _sub(con, "CH", "REA")
    item = db.create_item(con, sku="", name="x", family_id=_family(con, rea, "MN"))
    db.update_item(con, item, subcategory_id=None)
    row = db.get_item(con, item)
    assert (row["subcategory_id"], row["family_id"]) == (None, None)


def test_the_same_subcategory_keeps_the_family(con):
    rea = _sub(con, "CH", "REA")
    mn = _family(con, rea, "MN")
    item = db.create_item(con, sku="", name="x", family_id=mn)
    db.update_item(con, item, subcategory_id=rea)
    assert db.get_item(con, item)["family_id"] == mn


def test_a_family_alone_on_edit_implies_its_subcategory(con):
    rea = _sub(con, "CH", "REA")
    item = db.create_item(con, sku="", name="x", subcategory_id=_sub(con, "HY", "TUB"))
    db.update_item(con, item, family_id=_family(con, rea, "SV"))
    assert db.get_item(con, item)["subcategory_id"] == rea


def test_a_location_code_change_only_rewrites_the_positions_below(con):
    hq = next(f["id"] for f in db.list_nodes(con, "facility") if f["code"] == "HQ")
    unit = db.create_node(con, "unit", "D", "Drawers", db.create_node(con, "area", "OF", "Office", hq))
    pos = db.create_node(con, "position", "1.1", "", unit)
    # Corrupt one stored code elsewhere: a scoped rewrite must leave it alone.
    other = _pos(con, "RnD-WH-A-0.1")
    con.execute("UPDATE positions SET location_code = 'STALE' WHERE id = ?", (other,))
    db.update_node(con, "facility", hq, code="HQX")
    assert db.get_node(con, "position", pos)["location_code"] == "HQX-OF-D-1.1"
    assert db.get_node(con, "position", other)["location_code"] == "STALE"


def test_filtering_a_top_level_by_parent_is_refused(con):
    with pytest.raises(ValueError):
        db.list_nodes(con, "macro", parent_id=1)
