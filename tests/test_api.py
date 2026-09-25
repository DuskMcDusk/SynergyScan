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


# -------------------------------------------------------------------- labels
def test_preview_returns_a_png_without_any_printer(client):
    item = client.post("/api/items", json={"sku": "SKU-0042",
                                           "name": "Pasta"}).json()
    r = client.post("/api/print/preview", json={"item_id": item["id"]})
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_preview_uses_the_item_name_and_sku_by_default(client):
    item = client.post("/api/items", json={"sku": "SKU-0042",
                                           "name": "Pasta"}).json()
    a = client.post("/api/print/preview", json={"item_id": item["id"]}).content
    b = client.post("/api/print/preview",
                    json={"item_id": item["id"],
                          "lines": ["Pasta", "SKU-0042"]}).content
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
