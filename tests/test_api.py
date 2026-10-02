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
    area = client.post("/api/areas", json={"name": "Battery Testing"}).json()
    category = client.post("/api/categories",
                           json={"area_id": area["id"], "name": "Electrodes"}).json()
    item = client.post("/api/items", json={
        "sku": "FOAM-001", "name": "Fe foam", "category_id": category["id"],
        "supplier": "Supplier A", "lead_time_days": 30, "min_qty": 5,
        "low_qty": 8,
    }).json()
    assert item["category_id"] == category["id"]
    assert item["supplier"] == "Supplier A"

    full = client.get(f"/api/items/{item['id']}").json()
    assert full["availability"] == "reorder"       # qty 0, min_qty 5
    assert "lots" in full


def test_creating_an_item_with_an_unknown_category_is_a_404(client):
    r = client.post("/api/items", json={"sku": "A-1", "name": "Widget",
                                        "category_id": 999})
    assert r.status_code == 404


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


def test_items_can_be_filtered_by_category(client):
    area = client.post("/api/areas", json={"name": "Battery Testing"}).json()
    category = client.post("/api/categories",
                           json={"area_id": area["id"], "name": "Electrodes"}).json()
    client.post("/api/items", json={"sku": "A-1", "name": "In category",
                                    "category_id": category["id"]})
    client.post("/api/items", json={"sku": "B-1", "name": "Uncategorised"})
    rows = client.get(f"/api/items?category_id={category['id']}").json()
    assert [x["sku"] for x in rows] == ["A-1"]


# -------------------------------------------------------------------- areas
def test_create_and_list_areas(client):
    r = client.post("/api/areas", json={"name": "Battery Testing"})
    assert r.status_code == 201
    assert [a["name"] for a in client.get("/api/areas").json()] == ["Battery Testing"]


def test_duplicate_area_name_is_a_conflict(client):
    client.post("/api/areas", json={"name": "Chemistry"})
    r = client.post("/api/areas", json={"name": "Chemistry"})
    assert r.status_code == 409


def test_patching_a_missing_area_is_a_404(client):
    assert client.patch("/api/areas/999", json={"name": "x"}).status_code == 404


# ---------------------------------------------------------------- categories
def test_create_and_list_categories(client):
    area = client.post("/api/areas", json={"name": "Battery Testing"}).json()
    r = client.post("/api/categories", json={"area_id": area["id"], "name": "Electrodes"})
    assert r.status_code == 201
    assert [c["name"] for c in client.get("/api/categories").json()] == ["Electrodes"]


def test_creating_a_category_needs_a_real_area(client):
    r = client.post("/api/categories", json={"area_id": 999, "name": "Electrodes"})
    assert r.status_code == 404


def test_list_categories_filtered_by_area(client):
    a = client.post("/api/areas", json={"name": "Battery Testing"}).json()
    b = client.post("/api/areas", json={"name": "Chemistry"}).json()
    client.post("/api/categories", json={"area_id": a["id"], "name": "Electrodes"})
    client.post("/api/categories", json={"area_id": b["id"], "name": "Solvents"})
    rows = client.get(f"/api/categories?area_id={a['id']}").json()
    assert [c["name"] for c in rows] == ["Electrodes"]


# ----------------------------------------------------------------- locations
def _area(client, name="Area A"):
    return client.post("/api/areas", json={"name": name}).json()


def test_create_and_list_locations(client):
    a = _area(client)
    r = client.post("/api/locations", json={"area_id": a["id"], "name": "Shelf D1"})
    assert r.status_code == 201
    assert r.json()["area_id"] == a["id"]
    assert [l["name"] for l in client.get("/api/locations").json()] == ["Shelf D1"]


def test_a_location_needs_an_area(client):
    assert client.post("/api/locations", json={"name": "Shelf"}).status_code == 422
    r = client.post("/api/locations", json={"area_id": 999, "name": "Shelf"})
    assert r.status_code == 404


def test_patch_updates_a_location(client):
    a = _area(client)
    loc = client.post("/api/locations", json={"area_id": a["id"], "name": "Shelf D1"}).json()
    r = client.patch(f"/api/locations/{loc['id']}", json={"name": "Shelf D1 (top)"})
    assert r.status_code == 200
    assert r.json()["name"] == "Shelf D1 (top)"


def test_the_same_shelf_name_can_exist_in_two_areas(client):
    a, b = _area(client, "Area A"), _area(client, "Area B")
    assert client.post("/api/locations", json={"area_id": a["id"], "name": "Shelf 1"}).status_code == 201
    assert client.post("/api/locations", json={"area_id": b["id"], "name": "Shelf 1"}).status_code == 201


def test_duplicate_location_name_in_one_area_is_a_conflict(client):
    a = _area(client)
    client.post("/api/locations", json={"area_id": a["id"], "name": "Shelf D1"})
    r = client.post("/api/locations", json={"area_id": a["id"], "name": "shelf d1"})
    assert r.status_code == 409


def test_an_item_cannot_sit_in_another_areas_shelf(client):
    a, b = _area(client, "Area A"), _area(client, "Area B")
    cat = client.post("/api/categories", json={"area_id": a["id"], "name": "Foam"}).json()
    other = client.post("/api/locations", json={"area_id": b["id"], "name": "Shelf 1"}).json()
    own = client.post("/api/locations", json={"area_id": a["id"], "name": "Shelf 1"}).json()
    bad = client.post("/api/items", json={"name": "X", "category_id": cat["id"], "location_id": other["id"]})
    assert bad.status_code == 400
    good = client.post("/api/items", json={"name": "X", "category_id": cat["id"], "location_id": own["id"]})
    assert good.status_code == 201


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
    area = client.post("/api/areas", json={"name": "Chemistry"}).json()
    cat = client.post("/api/categories",
                      json={"area_id": area["id"], "name": "Solvents"}).json()
    url = f"/api/next-sku?category_id={cat['id']}"
    assert client.get(url).json() == {"sku": "SOLV-0001"}
    assert client.get(url).json() == {"sku": "SOLV-0001"}     # still unused
    item = client.post("/api/items", json={"name": "Acetone", "category_id": cat["id"]}).json()
    assert item["sku"] == "SOLV-0001"
    assert client.get(url).json() == {"sku": "SOLV-0002"}
    assert client.get("/api/next-sku").json() == {"sku": "ITM-0001"}
    assert client.get("/api/next-sku?category_id=9999").status_code == 404


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
    assert client.patch(f"/api/items/{b['id']}", json={"sku": "  "}).status_code == 400


# ------------------------------------------------------------------- archiving
def _tree(client):
    a = client.post("/api/areas", json={"name": "Area A"}).json()
    c1 = client.post("/api/categories", json={"area_id": a["id"], "name": "Foam"}).json()
    c2 = client.post("/api/categories", json={"area_id": a["id"], "name": "Felt"}).json()
    loc = client.post("/api/locations", json={"area_id": a["id"], "name": "Shelf 1"}).json()
    return a, c1, c2, loc


def test_archiving_an_area_archives_its_categories_and_locations(client):
    a, c1, c2, loc = _tree(client)
    client.patch(f"/api/areas/{a['id']}", json={"archived": True})
    assert client.get("/api/categories").json() == []
    assert client.get("/api/locations").json() == []


def test_restoring_an_area_brings_back_only_what_it_archived(client):
    a, c1, c2, loc = _tree(client)
    client.patch(f"/api/categories/{c2['id']}", json={"archived": True})   # on its own first
    client.patch(f"/api/areas/{a['id']}", json={"archived": True})
    client.patch(f"/api/areas/{a['id']}", json={"archived": False})
    assert [c["name"] for c in client.get("/api/categories").json()] == ["Foam"]
    assert [l["name"] for l in client.get("/api/locations").json()] == ["Shelf 1"]


def test_a_category_cannot_be_restored_while_its_area_is_archived(client):
    a, c1, c2, loc = _tree(client)
    client.patch(f"/api/areas/{a['id']}", json={"archived": True})
    assert client.patch(f"/api/categories/{c1['id']}", json={"archived": False}).status_code == 400
    assert client.patch(f"/api/locations/{loc['id']}", json={"archived": False}).status_code == 400


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


def test_a_name_held_by_an_archived_row_says_so(client):
    a = client.post("/api/areas", json={"name": "Old area"}).json()
    client.patch(f"/api/areas/{a['id']}", json={"archived": True})
    r = client.post("/api/areas", json={"name": "old area"})
    assert r.status_code == 409 and "archived" in r.json()["detail"]
    item = client.post("/api/items", json={"sku": "AR-3", "name": "X"}).json()
    client.patch(f"/api/items/{item['id']}", json={"archived": True})
    r = client.post("/api/items", json={"sku": "AR-3", "name": "Y"})
    assert r.status_code == 409 and "archived" in r.json()["detail"]
