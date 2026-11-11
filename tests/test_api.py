"""API and print-queue tests.

All of these run with the printer stubbed out (the `no_printer` fixture), so
they behave the same on a developer's desk - which on this project has a real
T50M Pro attached - as they do in CI.
"""

from __future__ import annotations

import time

import pytest

from synergyscan.printer import PrintService
from synergyscan.printer.errors import PrinterError, from_flags
from synergyscan.printer.spec import Barcode, LabelSize, LabelSpec


# ---------------------------------------------------------------------- meta
def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_version_reports_the_schema_and_data_dir(client):
    body = client.get("/api/version").json()
    assert body["source_checkout"] is True
    assert body["schema"] >= 1
    assert "data" in body["data_dir"]


def test_index_and_assets_are_served(client):
    assert client.get("/").status_code == 200
    assert client.get("/app.js").status_code == 200
    assert client.get("/app.css").status_code == 200


# --------------------------------------------------------------------- items
def test_create_and_list_items(client):
    r = client.post("/api/items", json={"sku": "A-1", "name": "Widget",
                                        "min_qty": 5})
    assert r.status_code == 201
    assert r.json()["sku"] == "A-1"

    rows = client.get("/api/items").json()
    assert [x["sku"] for x in rows] == ["A-1"]
    assert rows[0]["qty"] == 0


def test_duplicate_sku_is_a_conflict_not_a_crash(client):
    client.post("/api/items", json={"sku": "A-1", "name": "Widget"})
    r = client.post("/api/items", json={"sku": "A-1", "name": "Other"})
    assert r.status_code == 409
    assert "already in use" in r.json()["detail"]


def test_creating_an_item_without_a_name_is_rejected(client):
    assert client.post("/api/items", json={"sku": "A-1", "name": ""}).status_code == 422


def test_patch_updates_fields(client):
    item = client.post("/api/items", json={"sku": "A-1", "name": "Widget"}).json()
    r = client.patch(f"/api/items/{item['id']}", json={"name": "Renamed",
                                                      "min_qty": 12})
    assert r.status_code == 200
    assert r.json()["name"] == "Renamed"
    assert r.json()["min_qty"] == 12


def test_patching_a_missing_item_is_a_404(client):
    assert client.patch("/api/items/999", json={"name": "x"}).status_code == 404


def test_low_stock_filter(client):
    a = client.post("/api/items", json={"sku": "LOW", "name": "Low",
                                        "min_qty": 10}).json()
    client.post("/api/items", json={"sku": "OK", "name": "Fine", "min_qty": 0})
    client.post("/api/movements", json={"item_id": a["id"], "delta": 2,
                                        "reason": "receive"})
    rows = client.get("/api/items?low=true").json()
    assert [x["sku"] for x in rows] == ["LOW"]


def test_item_response_includes_the_new_master_fields_and_availability(client):
    sub = _node(client, "subcategories", "FOM")
    item = client.post("/api/items", json={
        "name": "Fe foam", "subcategory_id": sub["id"], "tech_ref": "V-99",
        "supplier": "Supplier A", "lead_time_days": 30, "min_qty": 5,
        "low_qty": 8,
    }).json()
    assert (item["sku"], item["subcategory_id"]) == ("CH-FOM-0001", sub["id"])
    assert item["tech_ref"] == "V-99"
    assert item["supplier"] == "Supplier A"

    full = client.get(f"/api/items/{item['id']}").json()
    assert full["availability"] == "reorder"       # qty 0, min_qty 5
    assert "lots" in full


@pytest.mark.parametrize("field", ["subcategory_id", "family_id", "position_id"])
def test_creating_an_item_with_an_unknown_reference_is_a_404(client, field):
    r = client.post("/api/items", json={"sku": "A-1", "name": "Widget", field: 999})
    assert r.status_code == 404


def test_a_family_from_another_subcategory_is_refused(client):
    r = client.post("/api/items", json={
        "name": "x", "family_id": _node(client, "families", "MN")["id"],
        "subcategory_id": _node(client, "subcategories", "TUB")["id"]})
    assert r.status_code == 400


def test_low_and_reorder_tiers_appear_in_the_items_list(client):
    item = client.post("/api/items", json={"sku": "A-1", "name": "Widget",
                                           "min_qty": 10, "low_qty": 15}).json()
    client.post("/api/movements", json={"item_id": item["id"], "delta": 5,
                                        "reason": "receive"})
    assert client.get("/api/items").json()[0]["availability"] == "reorder"

    client.post("/api/movements", json={"item_id": item["id"], "delta": 7,
                                        "reason": "receive"})      # qty 12
    assert client.get("/api/items").json()[0]["availability"] == "low"

    client.post("/api/movements", json={"item_id": item["id"], "delta": 10,
                                        "reason": "receive"})      # qty 22
    assert client.get("/api/items").json()[0]["availability"] == "sufficient"


def test_items_can_be_filtered_by_classification_and_location(client):
    tub, pmp = _node(client, "subcategories", "TUB"), _node(client, "subcategories", "PMP")
    pos_a = _position(client, "RnD-WH-A-0.1")
    pos_c = _position(client, "RnD-WH-C-1.1")
    client.post("/api/items", json={"name": "Tube", "subcategory_id": tub["id"],
                                    "position_id": pos_c["id"]})
    client.post("/api/items", json={"name": "Pump", "subcategory_id": pmp["id"],
                                    "position_id": pos_a["id"]})
    client.post("/api/items", json={"sku": "B-1", "name": "Unclassified"})

    def names(query):
        return sorted(x["name"] for x in client.get(f"/api/items?{query}").json())

    assert names(f"macro_id={tub['macro_id']}") == ["Pump", "Tube"]
    assert names(f"subcategory_id={tub['id']}") == ["Tube"]
    assert names(f"unit_id={pos_a['unit_id']}") == ["Pump"]
    assert names(f"macro_id={tub['macro_id']}&unit_id={pos_c['unit_id']}") == ["Tube"]
    row = client.get(f"/api/items?position_id={pos_c['id']}").json()[0]
    assert row["location_code"] == "RnD-WH-C-1.1"


# --------------------------------------------- classification and locations
def _node(client, plural, code):
    return next(n for n in client.get(f"/api/{plural}").json() if n["code"] == code)


def _position(client, location_code):
    return next(p for p in client.get("/api/positions").json()
                if p["location_code"] == location_code)


def test_the_preloaded_lists_are_served(client):
    assert len(client.get("/api/macros").json()) == 5
    assert len(client.get("/api/subcategories").json()) == 56
    assert len(client.get("/api/families").json()) == 6
    assert len(client.get("/api/facilities").json()) == 3
    assert len(client.get("/api/positions").json()) == 44
    hy = _node(client, "macros", "HY")
    assert len(client.get(f"/api/subcategories?parent_id={hy['id']}").json()) == 14


def test_setup_can_add_and_edit_subcategories_and_families(client):
    """The lists are refined in Setup, not by a new release."""
    me = _node(client, "macros", "ME")
    r = client.post("/api/subcategories", json={"parent_id": me["id"], "code": "brg",
                                                "name": "Bearings", "note": "Ball bearings"})
    assert r.status_code == 201 and r.json()["code"] == "BRG"
    fam = client.post("/api/families", json={"parent_id": r.json()["id"], "code": "BB",
                                             "name": "Ball"}).json()
    r = client.patch(f"/api/families/{fam['id']}", json={"name": "Ball bearings", "note": None})
    assert r.status_code == 200 and r.json()["name"] == "Ball bearings"


@pytest.mark.parametrize("plural,body,status", [
    ("subcategories", {"code": "ABC", "name": "No parent"}, 400),
    ("subcategories", {"parent_id": 999, "code": "ABC", "name": "Bad parent"}, 404),
    ("macros", {"code": "ABC", "name": "Too long"}, 400),
    ("macros", {"code": "EL", "name": "Taken"}, 409),
    ("facilities", {"code": "rnd", "name": "Taken, ignoring case"}, 409),
    ("macros", {"code": "XY"}, 400),                       # no name
])
def test_bad_nodes_are_refused_cleanly(client, plural, body, status):
    assert client.post(f"/api/{plural}", json=body).status_code == status


def test_patching_a_missing_node_is_a_404(client):
    assert client.patch("/api/units/999", json={"name": "x"}).status_code == 404


def test_a_new_position_gets_its_location_code(client):
    unit = _position(client, "RnD-WH-B-0.1")["unit_id"]
    r = client.post("/api/positions", json={"parent_id": unit, "code": "5.1",
                                            "note": "Top shelf, new"})
    assert r.status_code == 201
    assert r.json()["location_code"] == "RnD-WH-B-5.1"
    again = client.post("/api/positions", json={"parent_id": unit, "code": "5.1"})
    assert again.status_code == 409


def test_a_classification_code_locks_once_an_item_uses_it(client):
    tub = _node(client, "subcategories", "TUB")
    client.post("/api/items", json={"name": "Tube", "subcategory_id": tub["id"]})
    r = client.patch(f"/api/subcategories/{tub['id']}", json={"code": "TBE"})
    assert r.status_code == 400 and "locked" in r.json()["detail"]
    r = client.patch(f"/api/subcategories/{tub['id']}", json={"name": "Tubing"})
    assert r.status_code == 200


def test_moving_an_item_keeps_its_code(client):
    tub = _node(client, "subcategories", "TUB")
    item = client.post("/api/items", json={
        "name": "Tube", "subcategory_id": tub["id"],
        "position_id": _position(client, "RnD-WH-C-1.1")["id"]}).json()
    to = _position(client, "RnD-WH-A-2.3")
    r = client.patch(f"/api/items/{item['id']}", json={"position_id": to["id"]})
    assert (r.json()["sku"], r.json()["position_id"]) == ("HY-TUB-0001", to["id"])


# ---------------------------------------------------------------------- scan
def test_scan_finds_an_item(client):
    client.post("/api/items", json={"sku": "SKU-0042", "name": "Pasta"})
    body = client.post("/api/scan", json={"code": "SKU-0042"}).json()
    assert body["found"] is True
    assert body["item"]["name"] == "Pasta"
    assert body["qty"] == 0


def test_a_scan_miss_is_a_normal_answer_not_an_error(client):
    """The UI turns this into "add it?", which is how new stock gets entered."""
    r = client.post("/api/scan", json={"code": "UNKNOWN-1"})
    assert r.status_code == 200
    assert r.json() == {"found": False, "code": "UNKNOWN-1"}


def test_scanning_a_rack_label_finds_the_position(client):
    body = client.post("/api/scan", json={"code": "rnd-wh-a-0.1"}).json()
    assert body["found"] is False
    assert body["position"]["location_code"] == "RnD-WH-A-0.1"


def test_scan_tolerates_scanner_whitespace(client):
    client.post("/api/items", json={"sku": "SKU-0042", "name": "Pasta"})
    assert client.post("/api/scan", json={"code": " SKU-0042\r"}).json()["found"]


# ----------------------------------------------------------------- movements
def test_receive_and_issue_change_the_derived_quantity(client):
    item = client.post("/api/items", json={"sku": "A-1", "name": "Widget"}).json()
    client.post("/api/movements", json={"item_id": item["id"], "delta": 100,
                                        "reason": "receive"})
    r = client.post("/api/movements", json={"item_id": item["id"], "delta": -40,
                                            "reason": "issue"})
    assert r.status_code == 201
    assert r.json()["qty"] == 60


def test_movement_against_a_missing_item_is_a_404(client):
    r = client.post("/api/movements", json={"item_id": 999, "delta": 1,
                                            "reason": "receive"})
    assert r.status_code == 404


def test_an_unknown_reason_is_rejected_by_validation(client):
    item = client.post("/api/items", json={"sku": "A-1", "name": "W"}).json()
    r = client.post("/api/movements", json={"item_id": item["id"], "delta": 1,
                                            "reason": "shrinkage"})
    assert r.status_code == 422


def test_stocktake_records_the_difference(client):
    item = client.post("/api/items", json={"sku": "A-1", "name": "W"}).json()
    client.post("/api/movements", json={"item_id": item["id"], "delta": 100,
                                        "reason": "receive"})
    r = client.post("/api/stocktake", json={"item_id": item["id"], "counted": 92})
    assert r.json()["qty"] == 92
    assert r.json()["changed"] is True

    history = client.get(f"/api/movements?item_id={item['id']}").json()
    assert history[0]["reason"] == "stocktake"
    assert history[0]["delta"] == -8
    assert len(history) == 2                  # the original receipt is intact


def test_a_matching_stocktake_records_nothing(client):
    item = client.post("/api/items", json={"sku": "A-1", "name": "W"}).json()
    client.post("/api/movements", json={"item_id": item["id"], "delta": 5,
                                        "reason": "receive"})
    r = client.post("/api/stocktake", json={"item_id": item["id"], "counted": 5})
    assert r.json()["changed"] is False


# ---------------------------------------------------------------------- lots
def test_create_and_list_lots_for_an_item(client):
    item = client.post("/api/items", json={"sku": "A-1", "name": "W"}).json()
    r = client.post("/api/lots", json={"item_id": item["id"], "code": "Supplier Lot 4582"})
    assert r.status_code == 201
    rows = client.get(f"/api/lots?item_id={item['id']}").json()
    assert [l["code"] for l in rows] == ["Supplier Lot 4582"]


def test_creating_a_lot_for_a_missing_item_is_a_404(client):
    r = client.post("/api/lots", json={"item_id": 999, "code": "L1"})
    assert r.status_code == 404


def test_listing_lots_for_a_missing_item_is_a_404(client):
    assert client.get("/api/lots?item_id=999").status_code == 404


def test_a_movement_can_reference_a_lot(client):
    item = client.post("/api/items", json={"sku": "A-1", "name": "W"}).json()
    lot = client.post("/api/lots", json={"item_id": item["id"], "code": "L1"}).json()
    r = client.post("/api/movements", json={"item_id": item["id"], "delta": 12,
                                            "reason": "receive", "lot_id": lot["id"]})
    assert r.status_code == 201
    lots = client.get(f"/api/lots?item_id={item['id']}").json()
    assert lots[0]["qty"] == 12


def test_a_movement_against_an_unknown_lot_is_a_404(client):
    item = client.post("/api/items", json={"sku": "A-1", "name": "W"}).json()
    r = client.post("/api/movements", json={"item_id": item["id"], "delta": 1,
                                            "reason": "receive", "lot_id": 999})
    assert r.status_code == 404


# -------------------------------------------------------------------- labels
def test_preview_returns_a_png_without_any_printer(client):
    item = client.post("/api/items", json={"sku": "SKU-0042",
                                           "name": "Pasta"}).json()
    r = client.post("/api/print/preview", json={"item_id": item["id"]})
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_preview_uses_only_the_sku_by_default(client):
    item = client.post("/api/items", json={"sku": "SKU-0042",
                                           "name": "Pasta"}).json()
    a = client.post("/api/print/preview", json={"item_id": item["id"]}).content
    b = client.post("/api/print/preview",
                    json={"item_id": item["id"],
                          "lines": ["SKU-0042"]}).content
    assert a == b


def test_preview_of_an_over_long_barcode_explains_itself(client):
    r = client.post("/api/print/preview",
                    json={"lines": ["x"], "barcode_value": "A" * 80})
    assert r.status_code == 400
    assert "shorter code" in r.json()["detail"]


def test_printing_nothing_is_rejected(client):
    r = client.post("/api/print", json={})
    assert r.status_code == 400


def test_print_for_a_missing_item_is_a_404(client):
    assert client.post("/api/print", json={"item_id": 999}).status_code == 404


def test_printer_status_reports_absence_in_plain_language(client):
    body = client.get("/api/printer").json()
    assert body["reachable"] is False
    assert "USB cable" in body["error"]


def test_a_queued_print_fails_cleanly_with_no_printer(client):
    item = client.post("/api/items", json={"sku": "A-1", "name": "W"}).json()
    r = client.post("/api/print", json={"item_id": item["id"]})
    assert r.status_code == 202
    job_id = r.json()["job_id"]

    for _ in range(100):
        body = client.get(f"/api/print/{job_id}").json()
        if body["state"] in ("done", "failed"):
            break
    assert body["state"] == "failed"
    assert body["result"]["retryable"] is True
    assert "USB cable" in body["result"]["error"]


def test_print_jobs_are_recorded_for_audit(client):
    item = client.post("/api/items", json={"sku": "A-1", "name": "W"}).json()
    job_id = client.post("/api/print", json={"item_id": item["id"]}).json()["job_id"]
    for _ in range(100):
        if client.get(f"/api/print/{job_id}").json()["state"] in ("done", "failed"):
            break
    rows = client.get("/api/print/recent").json()
    assert rows and rows[0]["job_id"] == job_id


def test_unknown_print_job_is_a_404(client):
    assert client.get("/api/print/nope").status_code == 404


# ------------------------------------------------------------------- doctor
def test_doctor_report_is_plain_text_and_covers_the_essentials(client):
    text = client.get("/api/doctor").text
    for heading in ("VERSIONS", "MACHINE", "PRINTER", "DATABASE", "SETTINGS"):
        assert heading in text
    assert "Send this whole file to support." in text


def test_doctor_survives_a_broken_printer_probe(client, monkeypatch):
    """A diagnostic that crashes on the broken machine is worthless."""
    from synergyscan.printer import service as S

    monkeypatch.setattr(S.PrintService, "probe",
                        lambda self: (_ for _ in ()).throw(RuntimeError("boom")))
    text = client.get("/api/doctor").text
    assert "PRINTER" in text
    assert "failed" in text


def test_logs_endpoint_returns_text(client):
    assert client.get("/api/logs").status_code == 200


# ------------------------------------------------------------ error messages
@pytest.mark.parametrize("flag,fragment", [
    ("cover_open", "cover is open"),
    ("label_end", "run out"),
    ("label_not_installed", "No label roll"),
    ("head_temp_high", "too hot"),
    ("ribbon_end", "ribbon has run out"),
])
def test_every_fault_names_a_physical_thing_to_do(flag, fragment):
    err = from_flags([flag])
    assert fragment in str(err)
    assert err.retryable is True
    assert err.flag == flag


def test_an_unknown_flag_still_produces_a_sentence():
    err = from_flags(["something_new"])
    assert "something_new" in str(err)
    assert err.retryable is False


def test_multiple_faults_lead_with_the_most_actionable():
    err = from_flags(["label_not_installed", "cover_open"])
    assert "No label roll" in str(err)
    assert "cover_open" in str(err)


def test_no_flags_does_not_crash():
    assert isinstance(from_flags([]), PrinterError)


# ------------------------------------------------------------- print service
def test_submit_rejects_an_empty_label(no_printer):
    svc = PrintService()
    with pytest.raises(ValueError, match="nothing to print"):
        svc.submit(LabelSpec())


def test_preview_works_with_no_printer(no_printer):
    svc = PrintService()
    img = svc.preview(LabelSpec(lines=["Test"], barcode=Barcode(value="X-1")))
    assert img.size == (320, 240)             # the 40x30 fallback


def test_probe_size_falls_back_when_no_printer(no_printer):
    svc = PrintService(fallback_size=LabelSize(width_mm=50, height_mm=25))
    assert svc.probe_size().width_mm == 50


def test_the_worker_survives_a_job_that_raises(no_printer, monkeypatch):
    """One bad label must not take the queue down for the rest of the day."""
    from synergyscan.printer import service as S

    monkeypatch.setattr(S.PrintService, "_print",
                        lambda self, spec: (_ for _ in ()).throw(
                            RuntimeError("unexpected")))
    svc = PrintService()
    svc.start()
    try:
        job = svc.submit(LabelSpec(lines=["boom"]))
        for _ in range(200):
            if job.state in ("done", "failed"):
                break
            time.sleep(0.01)          # yield, or the worker never gets scheduled
        assert job.state == "failed"
        assert svc._worker is not None and svc._worker.is_alive()
    finally:
        svc.stop()


def test_next_sku_previews_without_consuming_the_number(client):
    rea = _node(client, "subcategories", "REA")
    mn = _node(client, "families", "MN")
    url = f"/api/next-sku?subcategory_id={rea['id']}&family_id={mn['id']}"
    assert client.get(url).json() == {"sku": "CH-REA-MN-0001"}
    assert client.get(url).json() == {"sku": "CH-REA-MN-0001"}     # still unused
    item = client.post("/api/items", json={"name": "KOH", "subcategory_id": rea["id"],
                                           "family_id": mn["id"]}).json()
    assert item["sku"] == "CH-REA-MN-0001"
    assert client.get(url).json() == {"sku": "CH-REA-MN-0002"}
    sub_only = client.get(f"/api/next-sku?subcategory_id={rea['id']}").json()
    assert sub_only == {"sku": "CH-REA-0001"}
    assert client.get("/api/next-sku").json() == {"sku": "ITM-0001"}
    assert client.get("/api/next-sku?subcategory_id=9999").status_code == 404


def test_a_position_label_carries_its_location_code(client, con):
    from synergyscan import app as appmod

    pos = _position(client, "RnD-WH-A-0.1")
    spec, _ = appmod._spec_for(con, appmod.PrintIn(position_id=pos["id"], symbology="qr"))
    assert spec.lines == ["RnD-WH-A-0.1"]
    assert (spec.barcode.symbology, spec.barcode.value) == ("qr", "RnD-WH-A-0.1")
    assert client.post("/api/print/preview", json={"position_id": 999}).status_code == 404


def test_item_label_carries_the_sku_but_not_the_product_name(client, con):
    from synergyscan import app as appmod

    item = client.post("/api/items", json={"sku": "A-9", "name": "Widget"}).json()
    spec, _ = appmod._spec_for(con, appmod.PrintIn(item_id=item["id"]))
    assert spec.lines == ["A-9"]
    assert spec.barcode.value == "A-9"


# ------------------------------------------------------- lot as an item property
def test_lot_is_a_plain_property_of_the_item(client):
    r = client.post("/api/items", json={"sku": "L-1", "name": "Foam", "lot": "  Batch 4582 "})
    assert r.status_code == 201
    assert r.json()["lot"] == "Batch 4582"
    assert client.get(f"/api/items/{r.json()['id']}").json()["lot"] == "Batch 4582"


def test_lot_is_optional_and_editable(client):
    item = client.post("/api/items", json={"sku": "L-2", "name": "Felt"}).json()
    assert item["lot"] is None
    r = client.patch(f"/api/items/{item['id']}", json={"lot": "B-9"})
    assert r.json()["lot"] == "B-9"


# ------------------------------------------------------------------ editing items
def test_an_item_can_be_edited_including_its_sku(client):
    item = client.post("/api/items", json={"sku": "E-1", "name": "Old", "supplier": "ACME",
                                           "lot": "L1", "low_qty": 10, "min_qty": 2}).json()
    r = client.patch(f"/api/items/{item['id']}", json={"sku": "E-2", "name": "New", "unit": "kg"})
    assert r.status_code == 200
    assert (r.json()["sku"], r.json()["name"], r.json()["unit"]) == ("E-2", "New", "kg")
    assert client.post("/api/scan", json={"code": "E-2"}).json()["found"]


def test_optional_fields_can_be_cleared_when_editing(client):
    item = client.post("/api/items", json={"sku": "E-3", "name": "X", "supplier": "ACME",
                                           "lot": "L1", "low_qty": 10, "min_qty": 2,
                                           "lead_time_days": 5}).json()
    r = client.patch(f"/api/items/{item['id']}", json={
        "supplier": None, "lot": None, "low_qty": None, "lead_time_days": None})
    body = r.json()
    assert (body["supplier"], body["lot"], body["low_qty"], body["lead_time_days"]) == (None,) * 4
    assert body["name"] == "X" and body["min_qty"] == 2     # the rest is untouched


def test_editing_to_a_taken_sku_is_a_conflict(client):
    client.post("/api/items", json={"sku": "E-4", "name": "A"})
    b = client.post("/api/items", json={"sku": "E-5", "name": "B"}).json()
    assert client.patch(f"/api/items/{b['id']}", json={"sku": "E-4"}).status_code == 409


def test_a_blank_sku_on_edit_generates_a_new_one(client):
    item = client.post("/api/items", json={"sku": "E-6", "name": "A"}).json()
    pip = _node(client, "subcategories", "PIP")
    r = client.patch(f"/api/items/{item['id']}", json={"sku": "", "subcategory_id": pip["id"]})
    assert r.json()["sku"] == "HY-PIP-0001"


# ------------------------------------------------------------------- archiving
def test_archiving_a_node_hides_its_branch_and_restoring_brings_it_back(client):
    ch = _node(client, "macros", "CH")
    client.patch(f"/api/macros/{ch['id']}", json={"archived": True})
    assert all(s["macro_id"] != ch["id"] for s in client.get("/api/subcategories").json())
    assert len(client.get("/api/families").json()) == 0            # all six were under CH
    hidden = client.get("/api/subcategories?include_archived=true").json()
    assert sum(s["hidden"] for s in hidden) == 6
    client.patch(f"/api/macros/{ch['id']}", json={"archived": False})
    assert len(client.get("/api/families").json()) == 6


def test_a_node_cannot_be_restored_while_its_parent_is_archived(client):
    pos = _position(client, "RnD-WH-A-0.1")
    client.patch(f"/api/positions/{pos['id']}", json={"archived": True})
    client.patch(f"/api/units/{pos['unit_id']}", json={"archived": True})
    r = client.patch(f"/api/positions/{pos['id']}", json={"archived": False})
    assert r.status_code == 400 and "restore it first" in r.json()["detail"]
    # The rest of the unit is hidden with it, so its labels no longer resolve.
    body = client.post("/api/scan", json={"code": "RnD-WH-A-0.2"}).json()
    assert body == {"found": False, "code": "RnD-WH-A-0.2"}


def test_archived_items_are_listed_separately_and_can_be_restored(client):
    item = client.post("/api/items", json={"sku": "AR-1", "name": "Old thing"}).json()
    client.patch(f"/api/items/{item['id']}", json={"archived": True})
    assert all(i["sku"] != "AR-1" for i in client.get("/api/items").json())
    assert [i["sku"] for i in client.get("/api/items?archived=true").json()] == ["AR-1"]
    client.patch(f"/api/items/{item['id']}", json={"archived": False})
    assert any(i["sku"] == "AR-1" for i in client.get("/api/items").json())


def test_scanning_an_archived_item_points_at_it(client):
    item = client.post("/api/items", json={"sku": "AR-2", "name": "Old thing"}).json()
    client.patch(f"/api/items/{item['id']}", json={"archived": True})
    body = client.post("/api/scan", json={"code": "AR-2"}).json()
    assert body["found"] is False
    assert body["archived_item"]["id"] == item["id"]


def test_a_code_held_by_an_archived_row_says_so(client):
    hq = _node(client, "facilities", "HQ")
    client.patch(f"/api/facilities/{hq['id']}", json={"archived": True})
    r = client.post("/api/facilities", json={"code": "HQ", "name": "Again"})
    assert r.status_code == 409 and "archived" in r.json()["detail"]
    item = client.post("/api/items", json={"sku": "AR-3", "name": "X"}).json()
    client.patch(f"/api/items/{item['id']}", json={"archived": True})
    r = client.post("/api/items", json={"sku": "AR-3", "name": "Y"})
    assert r.status_code == 409 and "archived" in r.json()["detail"]


def test_changing_the_subcategory_through_the_api_drops_the_family(client):
    mn, tub = _node(client, "families", "MN"), _node(client, "subcategories", "TUB")
    item = client.post("/api/items", json={"name": "x", "family_id": mn["id"]}).json()
    r = client.patch(f"/api/items/{item['id']}", json={"subcategory_id": tub["id"]})
    assert r.status_code == 200
    assert (r.json()["subcategory_id"], r.json()["family_id"]) == (tub["id"], None)
    r = client.patch(f"/api/items/{item['id']}", json={"subcategory_id": None})
    assert r.json()["subcategory_id"] is None


@pytest.mark.parametrize("plural", ["macros", "facilities"])
def test_parent_id_on_a_top_level_list_is_a_400(client, plural):
    assert client.get(f"/api/{plural}?parent_id=1").status_code == 400


def test_an_archived_entry_cannot_be_newly_chosen_but_can_be_kept(client):
    pos = _position(client, "RnD-WH-A-0.1")
    item = client.post("/api/items", json={"name": "x", "position_id": pos["id"]}).json()
    client.patch(f"/api/positions/{pos['id']}", json={"archived": True})
    r = client.post("/api/items", json={"name": "y", "position_id": pos["id"]})
    assert r.status_code == 400 and "archived" in r.json()["detail"]
    # Editing the item that already sits there keeps working.
    r = client.patch(f"/api/items/{item['id']}", json={"name": "x2", "position_id": pos["id"]})
    assert r.status_code == 200
    other = client.post("/api/items", json={"name": "z"}).json()
    r = client.patch(f"/api/items/{other['id']}", json={"position_id": pos["id"]})
    assert r.status_code == 400
