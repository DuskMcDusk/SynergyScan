"""SQLite access.

Autocommit (`isolation_level=None`) with explicit transactions where more than
one statement has to land together. That keeps the migration runner's explicit
BEGIN/COMMIT honest and makes it obvious at each call site whether a write is
atomic.

WAL is on so a long read (a stocktake report) never blocks a scan from writing.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from . import migrations, paths

REASONS = ("receive", "issue", "adjust", "stocktake", "move_in", "move_out")


def connect(path: Path | None = None) -> sqlite3.Connection:
    con = sqlite3.connect(
        path or paths.db_path(),
        isolation_level=None,
        timeout=10.0,            # wait rather than fail if the worker holds a write
        check_same_thread=False,  # the print worker logs job results
    )
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA synchronous=NORMAL")
    return con


@contextmanager
def tx(con: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Explicit transaction. Rolls back on any exception."""
    con.execute("BEGIN")
    try:
        yield con
    except Exception:
        con.execute("ROLLBACK")
        raise
    else:
        con.execute("COMMIT")


def init(path: Path | None = None) -> sqlite3.Connection:
    """Open the database and bring the schema up to date.

    In the deployed flow selfupdate.migrate calls this behind a backup. Calling
    it directly is for development and tests.
    """
    con = connect(path)
    migrations.apply_all(con)
    return con


def schema_version(con: sqlite3.Connection) -> int:
    return migrations.current_version(con)


# ------------------------------------------------------------------- items
ITEM_MASTER_FIELDS = ("category_id", "location_id", "supplier", "lead_time_days",
                     "low_qty")


def _check_thresholds(min_qty: float | None, low_qty: float | None) -> None:
    """low_qty is a soft warning threshold above the hard reorder point.

    Not a CHECK constraint: SQLite forbids one that references another column
    on ALTER TABLE ADD COLUMN, so the ordering rule lives here instead - the
    same way add_movement() enforces its own invariants in Python.
    """
    if min_qty is not None and low_qty is not None and low_qty < min_qty:
        raise ValueError(
            f"low_qty ({low_qty}) must not be below min_qty ({min_qty})"
        )


def create_item(con: sqlite3.Connection, sku: str, name: str, **kw: Any) -> int:
    _check_thresholds(kw.get("min_qty", 0), kw.get("low_qty"))
    cur = con.execute(
        "INSERT INTO items (sku, name, description, unit, min_qty, barcode, "
        "category_id, location_id, supplier, lead_time_days, "
        "low_qty) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (sku.strip(), name.strip(), kw.get("description"), kw.get("unit", "pcs"),
         kw.get("min_qty", 0), (kw.get("barcode") or None),
         kw.get("category_id"), kw.get("location_id"), kw.get("supplier"),
         kw.get("lead_time_days"), kw.get("low_qty")),
    )
    return int(cur.lastrowid)


def update_item(con: sqlite3.Connection, item_id: int, **kw: Any) -> None:
    fields = [k for k in ("name", "description", "unit", "min_qty", "barcode",
                          "archived", *ITEM_MASTER_FIELDS) if k in kw]
    if not fields:
        return
    if "min_qty" in kw or "low_qty" in kw:
        current = get_item(con, item_id)
        min_qty = kw.get("min_qty", current["min_qty"] if current else 0)
        low_qty = kw.get("low_qty", current["low_qty"] if current else None)
        _check_thresholds(min_qty, low_qty)
    sets = ", ".join(f"{f} = ?" for f in fields)
    con.execute(
        f"UPDATE items SET {sets}, updated_at = datetime('now') WHERE id = ?",
        [*(kw[f] for f in fields), item_id],
    )


def get_item(con: sqlite3.Connection, item_id: int) -> sqlite3.Row | None:
    return con.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()


def find_by_code(con: sqlite3.Connection, code: str) -> sqlite3.Row | None:
    """Resolve a scanned code to an item.

    Scanners send whatever is on the label, so try the dedicated barcode column
    first and fall back to the SKU - which is what most labels actually carry.
    """
    code = code.strip()
    if not code:
        return None
    return con.execute(
        "SELECT * FROM items WHERE archived = 0 AND (barcode = ? OR sku = ?) "
        "ORDER BY (barcode = ?) DESC LIMIT 1",
        (code, code, code),
    ).fetchone()


def list_items(con: sqlite3.Connection, search: str | None = None,
               low_only: bool = False, limit: int = 500,
               category_id: int | None = None,
               area_id: int | None = None) -> list[sqlite3.Row]:
    sql = [("SELECT s.*, i.barcode, i.supplier, i.lead_time_days "
           "FROM stock_on_hand s JOIN items i ON i.id = s.item_id")]
    if area_id is not None:
        sql.append("LEFT JOIN categories c ON c.id = s.category_id")
    args: list[Any] = []
    where = []
    if search:
        where.append("(s.sku LIKE ? OR s.name LIKE ? OR i.barcode LIKE ?)")
        args += [f"%{search}%"] * 3
    if low_only:
        # min_qty > 0 matters: an item with no reorder point set is not low, it
        # is unconfigured. Without this every newly created item (qty 0,
        # min_qty 0) shows up as needing reordering and the list is useless.
        where.append("s.min_qty > 0 AND s.qty <= s.min_qty")
    if category_id is not None:
        where.append("s.category_id = ?")
        args.append(category_id)
    if area_id is not None:
        where.append("c.area_id = ?")
        args.append(area_id)
    if where:
        sql.append("WHERE " + " AND ".join(where))
    sql.append("ORDER BY s.name LIMIT ?")
    args.append(limit)
    return con.execute(" ".join(sql), args).fetchall()


def availability(qty: float, min_qty: float, low_qty: float | None) -> str:
    """Classify on-hand stock into sufficient / low / reorder.

    min_qty is the existing hard reorder point: 0 (unconfigured) always means
    "sufficient", matching list_items()'s low_only rule. low_qty, when set, is
    a softer warning threshold above it.
    """
    if not min_qty or min_qty <= 0:
        return "sufficient"
    if qty <= min_qty:
        return "reorder"
    if low_qty is not None and qty <= low_qty:
        return "low"
    return "sufficient"


def on_hand(con: sqlite3.Connection, item_id: int) -> float:
    r = con.execute(
        "SELECT COALESCE(SUM(delta), 0) AS q FROM stock_movements WHERE item_id = ?",
        (item_id,),
    ).fetchone()
    return float(r["q"])


# ------------------------------------------------------------------- areas
def create_area(con: sqlite3.Connection, name: str, **kw: Any) -> int:
    cur = con.execute(
        "INSERT INTO areas (name, archived) VALUES (?, ?)",
        (name.strip(), int(bool(kw.get("archived", 0)))),
    )
    return int(cur.lastrowid)


def update_area(con: sqlite3.Connection, area_id: int, **kw: Any) -> None:
    fields = [k for k in ("name", "archived") if k in kw]
    if not fields:
        return
    sets = ", ".join(f"{f} = ?" for f in fields)
    con.execute(f"UPDATE areas SET {sets} WHERE id = ?",
               [*(kw[f] for f in fields), area_id])


def get_area(con: sqlite3.Connection, area_id: int) -> sqlite3.Row | None:
    return con.execute("SELECT * FROM areas WHERE id = ?", (area_id,)).fetchone()


def list_areas(con: sqlite3.Connection, include_archived: bool = False) -> list[sqlite3.Row]:
    sql = "SELECT * FROM areas"
    if not include_archived:
        sql += " WHERE archived = 0"
    return con.execute(sql + " ORDER BY name").fetchall()


# -------------------------------------------------------------- categories
def create_category(con: sqlite3.Connection, area_id: int, name: str, **kw: Any) -> int:
    cur = con.execute(
        "INSERT INTO categories (area_id, name, archived) VALUES (?, ?, ?)",
        (area_id, name.strip(), int(bool(kw.get("archived", 0)))),
    )
    return int(cur.lastrowid)


def update_category(con: sqlite3.Connection, category_id: int, **kw: Any) -> None:
    fields = [k for k in ("area_id", "name", "archived") if k in kw]
    if not fields:
        return
    sets = ", ".join(f"{f} = ?" for f in fields)
    con.execute(f"UPDATE categories SET {sets} WHERE id = ?",
               [*(kw[f] for f in fields), category_id])


def get_category(con: sqlite3.Connection, category_id: int) -> sqlite3.Row | None:
    return con.execute("SELECT * FROM categories WHERE id = ?", (category_id,)).fetchone()


def list_categories(con: sqlite3.Connection, area_id: int | None = None,
                    include_archived: bool = False) -> list[sqlite3.Row]:
    sql = ["SELECT * FROM categories"]
    where = []
    args: list[Any] = []
    if area_id is not None:
        where.append("area_id = ?")
        args.append(area_id)
    if not include_archived:
        where.append("archived = 0")
    if where:
        sql.append("WHERE " + " AND ".join(where))
    sql.append("ORDER BY name")
    return con.execute(" ".join(sql), args).fetchall()


# --------------------------------------------------------------- locations
def create_location(con: sqlite3.Connection, code: str, name: str, **kw: Any) -> int:
    cur = con.execute(
        "INSERT INTO locations (code, name, archived) VALUES (?, ?, ?)",
        (code.strip(), name.strip(), int(bool(kw.get("archived", 0)))),
    )
    return int(cur.lastrowid)


def update_location(con: sqlite3.Connection, location_id: int, **kw: Any) -> None:
    fields = [k for k in ("code", "name", "archived") if k in kw]
    if not fields:
        return
    sets = ", ".join(f"{f} = ?" for f in fields)
    con.execute(f"UPDATE locations SET {sets} WHERE id = ?",
               [*(kw[f] for f in fields), location_id])


def get_location(con: sqlite3.Connection, location_id: int) -> sqlite3.Row | None:
    return con.execute("SELECT * FROM locations WHERE id = ?", (location_id,)).fetchone()


def list_locations(con: sqlite3.Connection, include_archived: bool = False) -> list[sqlite3.Row]:
    sql = "SELECT * FROM locations"
    if not include_archived:
        sql += " WHERE archived = 0"
    return con.execute(sql + " ORDER BY code").fetchall()


# --------------------------------------------------------------- movements
def add_movement(con: sqlite3.Connection, item_id: int, delta: float, reason: str,
                 location_id: int | None = None, lot_id: int | None = None,
                 note: str | None = None, actor: str | None = None) -> int:
    """Append one movement. The only way stock ever changes."""
    if reason not in REASONS:
        raise ValueError(f"unknown reason {reason!r}; expected one of {REASONS}")
    if delta == 0:
        raise ValueError("a movement of zero would record nothing")
    cur = con.execute(
        "INSERT INTO stock_movements (item_id, location_id, lot_id, delta, reason, "
        "note, actor) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (item_id, location_id, lot_id, delta, reason, note, actor),
    )
    return int(cur.lastrowid)


def set_stocktake(con: sqlite3.Connection, item_id: int, counted: float,
                  location_id: int | None = None,
                  actor: str | None = None) -> int | None:
    """Reconcile to a counted figure by appending the difference.

    Deliberately not an UPDATE: the ledger keeps the correction visible, which
    is the whole point of counting.
    """
    with tx(con):
        current = on_hand(con, item_id)
        delta = counted - current
        if delta == 0:
            return None
        return add_movement(
            con, item_id, delta, "stocktake", location_id=location_id,
            note=f"counted {counted:g}, was {current:g}", actor=actor,
        )


def movements(con: sqlite3.Connection, item_id: int | None = None,
              limit: int = 100) -> list[sqlite3.Row]:
    if item_id is None:
        return con.execute(
            "SELECT m.*, i.sku, i.name FROM stock_movements m "
            "JOIN items i ON i.id = m.item_id "
            "ORDER BY m.id DESC LIMIT ?", (limit,),
        ).fetchall()
    return con.execute(
        "SELECT m.*, i.sku, i.name FROM stock_movements m "
        "JOIN items i ON i.id = m.item_id WHERE m.item_id = ? "
        "ORDER BY m.id DESC LIMIT ?", (item_id, limit),
    ).fetchall()


# --------------------------------------------------------------------- lots
def create_lot(con: sqlite3.Connection, item_id: int, code: str, **kw: Any) -> int:
    cur = con.execute(
        "INSERT INTO lots (item_id, code, received_at, expires_at, note) "
        "VALUES (?, ?, ?, ?, ?)",
        (item_id, code.strip(), kw.get("received_at"), kw.get("expires_at"),
         kw.get("note")),
    )
    return int(cur.lastrowid)


def update_lot(con: sqlite3.Connection, lot_id: int, **kw: Any) -> None:
    fields = [k for k in ("code", "received_at", "expires_at", "note", "archived")
             if k in kw]
    if not fields:
        return
    sets = ", ".join(f"{f} = ?" for f in fields)
    con.execute(f"UPDATE lots SET {sets} WHERE id = ?",
               [*(kw[f] for f in fields), lot_id])


def get_lot(con: sqlite3.Connection, lot_id: int) -> sqlite3.Row | None:
    return con.execute("SELECT * FROM lots WHERE id = ?", (lot_id,)).fetchone()


def list_lots(con: sqlite3.Connection, item_id: int,
             include_archived: bool = False) -> list[sqlite3.Row]:
    """Lots for one item, each carrying its on-hand qty derived from movements."""
    sql = [("SELECT l.*, COALESCE(SUM(m.delta), 0) AS qty FROM lots l "
           "LEFT JOIN stock_movements m ON m.lot_id = l.id "
           "WHERE l.item_id = ?")]
    args: list[Any] = [item_id]
    if not include_archived:
        sql.append("AND l.archived = 0")
    sql.append("GROUP BY l.id ORDER BY l.created_at")
    return con.execute(" ".join(sql), args).fetchall()


def lot_on_hand(con: sqlite3.Connection, lot_id: int) -> float:
    r = con.execute(
        "SELECT COALESCE(SUM(delta), 0) AS q FROM stock_movements WHERE lot_id = ?",
        (lot_id,),
    ).fetchone()
    return float(r["q"])


# -------------------------------------------------------------- print jobs
def log_print_job(con: sqlite3.Connection, job_id: str, spec: dict, state: str,
                  queued_at: str, item_id: int | None = None, copies: int = 1,
                  error: str | None = None, finished_at: str | None = None) -> None:
    con.execute(
        "INSERT INTO print_jobs (id, item_id, spec_json, state, error, copies, "
        "queued_at, finished_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET state = excluded.state, "
        "error = excluded.error, finished_at = excluded.finished_at",
        (job_id, item_id, json.dumps(spec), state, error, copies, queued_at,
         finished_at),
    )


def counts(con: sqlite3.Connection) -> dict[str, int]:
    """Row counts for the doctor report."""
    out = {}
    for table in ("items", "locations", "areas", "categories", "lots",
                 "stock_movements", "print_jobs"):
        try:
            out[table] = con.execute(f"SELECT COUNT(*) c FROM {table}").fetchone()["c"]
        except sqlite3.Error:
            out[table] = -1
    return out
