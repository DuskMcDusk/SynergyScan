"""The local HTTP API and UI.

Bound to 127.0.0.1 by default. Setting allow_lan in config.json binds 0.0.0.0
instead, which lets a phone or tablet on the same network do a stocktake while
this machine keeps the printer - the reason the UI is a web app rather than a
desktop window.

A fresh SQLite connection per request. Connecting is measured in microseconds
and it sidesteps every cross-thread question that one shared handle would
create, given FastAPI runs sync endpoints in a thread pool.
"""

from __future__ import annotations

import io
import json
import logging
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any, Iterator, Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Response
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

from . import config, db, logs, paths
from .printer import LabelSize, PrintService
from .printer.errors import PrinterError
from .printer.service import Job
from .printer.spec import Barcode, LabelSpec
from .selfupdate import version as relver

log = logging.getLogger(__name__)

WEB = Path(__file__).parent / "web"


# --------------------------------------------------------------- app state
class State:
    def __init__(self) -> None:
        self.settings = config.load()
        self.printer = PrintService(
            fallback_size=LabelSize(width_mm=self.settings.label_width_mm,
                                    height_mm=self.settings.label_height_mm),
            on_complete=self._record_job,
        )

    def _record_job(self, job: Job) -> None:
        """Persist the outcome of a print job for the audit trail."""
        try:
            con = db.connect()
            try:
                db.log_print_job(
                    con, job.id, job.spec.model_dump(), job.state,
                    queued_at=job.queued_at, copies=job.spec.copies,
                    error=(job.result.error if job.result else None),
                    finished_at=job.finished_at,
                )
            finally:
                con.close()
        except sqlite3.Error:
            # Losing the audit row must not turn a successful print into a failure.
            log.exception("could not record print job %s", job.id)


state = State()


@asynccontextmanager
async def lifespan(app: FastAPI):
    state.printer.start()
    log.info("release %s listening on %s:%s", relver.current(),
             state.settings.host, state.settings.port)
    try:
        yield
    finally:
        state.printer.stop()


app = FastAPI(title="SynergyScan", version=relver.current(), lifespan=lifespan,
              docs_url="/api/docs", redoc_url=None)


def get_db() -> Iterator[sqlite3.Connection]:
    con = db.connect()
    try:
        yield con
    finally:
        con.close()


Db = Annotated[sqlite3.Connection, Depends(get_db)]


def rows(rs) -> list[dict[str, Any]]:
    return [dict(r) for r in rs]


# ------------------------------------------------------------------ schemas
class ItemIn(BaseModel):
    sku: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    unit: str = "pcs"
    min_qty: float = 0
    barcode: str | None = None


class ItemPatch(BaseModel):
    name: str | None = None
    description: str | None = None
    unit: str | None = None
    min_qty: float | None = None
    barcode: str | None = None
    archived: bool | None = None


class MovementIn(BaseModel):
    item_id: int
    delta: float
    reason: Literal["receive", "issue", "adjust", "stocktake", "move_in", "move_out"]
    note: str | None = None
    actor: str | None = None


class StocktakeIn(BaseModel):
    item_id: int
    counted: float = Field(ge=0)
    actor: str | None = None


class ScanIn(BaseModel):
    code: str = Field(min_length=1, max_length=128)


class PrintIn(BaseModel):
    """Print either a free-form label or the label for an item."""
    item_id: int | None = None
    lines: list[str] = Field(default_factory=list, max_length=6)
    barcode_value: str | None = None
    symbology: Literal["code128", "ean13", "code39", "qr"] = "code128"
    show_text: bool = False
    copies: int = Field(default=1, ge=1, le=50)
    density: int | None = Field(default=None, ge=0, le=15)


def _spec_for(con: sqlite3.Connection, body: PrintIn) -> tuple[LabelSpec, int | None]:
    """Build a LabelSpec, filling it from the item when one is given."""
    lines, value = list(body.lines), body.barcode_value
    item_id = body.item_id
    if item_id is not None:
        item = db.get_item(con, item_id)
        if item is None:
            raise HTTPException(404, f"no item with id {item_id}")
        if not lines:
            lines = [item["name"], item["sku"]]
        if not value:
            value = item["barcode"] or item["sku"]
    spec = LabelSpec(
        lines=lines,
        barcode=Barcode(symbology=body.symbology, value=value,
                        show_text=body.show_text) if value else None,
        copies=body.copies,
        density=body.density if body.density is not None else state.settings.density,
    )
    if spec.is_empty():
        raise HTTPException(400, "nothing to print: give an item, some text or a barcode")
    return spec, item_id


# --------------------------------------------------------------------- meta
@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "version": relver.current()}


@app.get("/api/version")
def api_version(con: Db) -> dict:
    return {
        "release": relver.current(),
        "source_checkout": relver.is_source_checkout(),
        "schema": db.schema_version(con),
        "data_dir": str(paths.data_dir()),
        "channel_url": state.settings.channel_url,
        "auto_update": state.settings.auto_update,
    }


@app.get("/api/settings")
def api_settings() -> dict:
    return state.settings.model_dump()


# -------------------------------------------------------------------- items
@app.get("/api/items")
def api_items(con: Db, search: str | None = None,
              low: bool = Query(False, description="only items at or below min_qty"),
              limit: int = Query(500, le=2000)) -> list[dict]:
    return rows(db.list_items(con, search=search, low_only=low, limit=limit))


@app.post("/api/items", status_code=201)
def api_create_item(body: ItemIn, con: Db) -> dict:
    try:
        item_id = db.create_item(con, **body.model_dump())
    except sqlite3.IntegrityError as e:
        raise HTTPException(409, f"that SKU or barcode is already in use ({e})") from e
    return dict(db.get_item(con, item_id))        # type: ignore[arg-type]


@app.get("/api/items/{item_id}")
def api_item(item_id: int, con: Db) -> dict:
    item = db.get_item(con, item_id)
    if item is None:
        raise HTTPException(404, f"no item with id {item_id}")
    return {**dict(item), "qty": db.on_hand(con, item_id),
            "movements": rows(db.movements(con, item_id, limit=50))}


@app.patch("/api/items/{item_id}")
def api_patch_item(item_id: int, body: ItemPatch, con: Db) -> dict:
    if db.get_item(con, item_id) is None:
        raise HTTPException(404, f"no item with id {item_id}")
    fields = {k: v for k, v in body.model_dump(exclude_unset=True).items()
              if v is not None}
    if "archived" in fields:
        fields["archived"] = int(bool(fields["archived"]))
    try:
        db.update_item(con, item_id, **fields)
    except sqlite3.IntegrityError as e:
        raise HTTPException(409, f"that barcode is already in use ({e})") from e
    return dict(db.get_item(con, item_id))        # type: ignore[arg-type]


# --------------------------------------------------------------------- scan
@app.post("/api/scan")
def api_scan(body: ScanIn, con: Db) -> dict:
    """Resolve a scanned code to an item.

    A miss is a normal outcome, not an error: the UI offers to create the item,
    which is how a new product gets into the system at the receiving bench.
    """
    item = db.find_by_code(con, body.code)
    if item is None:
        log.info("scan miss: %r", body.code)
        return {"found": False, "code": body.code.strip()}
    return {"found": True, "item": dict(item), "qty": db.on_hand(con, item["id"])}


# ---------------------------------------------------------------- movements
@app.get("/api/movements")
def api_movements(con: Db, item_id: int | None = None,
                  limit: int = Query(100, le=1000)) -> list[dict]:
    return rows(db.movements(con, item_id, limit=limit))


@app.post("/api/movements", status_code=201)
def api_add_movement(body: MovementIn, con: Db) -> dict:
    if db.get_item(con, body.item_id) is None:
        raise HTTPException(404, f"no item with id {body.item_id}")
    try:
        mid = db.add_movement(con, **body.model_dump())
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"id": mid, "qty": db.on_hand(con, body.item_id)}


@app.post("/api/stocktake", status_code=201)
def api_stocktake(body: StocktakeIn, con: Db) -> dict:
    if db.get_item(con, body.item_id) is None:
        raise HTTPException(404, f"no item with id {body.item_id}")
    mid = db.set_stocktake(con, body.item_id, body.counted, actor=body.actor)
    return {"id": mid, "qty": db.on_hand(con, body.item_id),
            "changed": mid is not None}


# -------------------------------------------------------------------- print
@app.post("/api/print", status_code=202)
def api_print(body: PrintIn, con: Db) -> dict:
    """Enqueue a label. Returns immediately; poll /api/print/{id} for the result.

    Printing takes a second or more per label because the protocol polls the
    printer between blocks, so no request ever waits on it.
    """
    spec, item_id = _spec_for(con, body)
    job = state.printer.submit(spec)
    if item_id is not None:
        try:
            db.log_print_job(con, job.id, spec.model_dump(), job.state,
                             queued_at=job.queued_at, item_id=item_id,
                             copies=spec.copies)
        except sqlite3.Error:
            log.exception("could not record queued print job %s", job.id)
    return {"job_id": job.id, "state": job.state}


# Declared before /api/print/{job_id}: FastAPI matches in declaration order, so
# the literal path has to come first or "recent" arrives as a job id.
@app.get("/api/print/recent")
def api_print_recent(limit: int = Query(20, le=100)) -> list[dict]:
    return [{"job_id": j.id, "state": j.state, "queued_at": j.queued_at,
             "lines": j.spec.lines,
             "error": j.result.error if j.result else None}
            for j in state.printer.recent(limit)]


@app.get("/api/print/{job_id}")
def api_print_status(job_id: str) -> dict:
    job = state.printer.get(job_id)
    if job is None:
        raise HTTPException(404, "no such print job (they are kept for a while, not forever)")
    return {"job_id": job.id, "state": job.state, "queued_at": job.queued_at,
            "finished_at": job.finished_at,
            "result": job.result.model_dump() if job.result else None}


@app.post("/api/print/preview")
def api_preview(body: PrintIn, con: Db) -> Response:
    """PNG of exactly what would be printed. Needs no printer."""
    spec, _ = _spec_for(con, body)
    try:
        img = state.printer.preview(spec)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    buf = io.BytesIO()
    # Scale up for the screen: at 203 dpi a 40 mm label is only 320 px wide.
    img.convert("L").resize((img.width * 2, img.height * 2)).save(buf, "PNG")
    return Response(buf.getvalue(), media_type="image/png",
                    headers={"Cache-Control": "no-store"})


@app.get("/api/printer")
def api_printer() -> dict:
    """Printer status, in plain language when something needs doing."""
    probe = state.printer.probe()
    st = probe.get("status") or {}
    from .printer.errors import from_flags
    from .printer.protocol import status_errors
    flags = status_errors(st) if st else []
    return {
        **probe,
        "needs_attention": bool(flags),
        "message": str(from_flags(flags)) if flags else None,
    }


# ------------------------------------------------------------------- doctor
@app.get("/api/doctor", response_class=PlainTextResponse)
def api_doctor(con: Db) -> str:
    from .doctor import report_text
    return report_text(con)


@app.get("/api/logs", response_class=PlainTextResponse)
def api_logs(lines: int = Query(200, le=5000)) -> str:
    return "\n".join(logs.tail(lines))


# ----------------------------------------------------------------------- UI
@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB / "index.html")


@app.get("/app.js")
def app_js() -> FileResponse:
    return FileResponse(WEB / "static" / "app.js", media_type="text/javascript")


@app.get("/app.css")
def app_css() -> FileResponse:
    return FileResponse(WEB / "static" / "app.css", media_type="text/css")


@app.exception_handler(PrinterError)
def printer_error_handler(request, exc: PrinterError) -> JSONResponse:
    """Printer faults reach the UI as a sentence, never as a traceback."""
    return JSONResponse(
        {"error": str(exc), "flag": exc.flag, "retryable": exc.retryable},
        status_code=503,
    )


@app.exception_handler(json.JSONDecodeError)
def json_error_handler(request, exc) -> JSONResponse:
    return JSONResponse({"error": "malformed request body"}, status_code=400)
