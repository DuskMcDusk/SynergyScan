"""SQLite access.

Autocommit (`isolation_level=None`) with explicit transactions where more than
one statement has to land together. That keeps the migration runner's explicit
BEGIN/COMMIT honest and makes it obvious at each call site whether a write is
atomic.

WAL is on so a long read (a stocktake report) never blocks a scan from writing.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
ITEM_MASTER_FIELDS = ("subcategory_id", "family_id", "position_id", "supplier",
                      "lead_time_days", "low_qty", "lot", "tech_ref")


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
    sub, fam = classify(con, kw.get("subcategory_id"), kw.get("family_id"))
    sku = sku.strip() or next_sku(con, sub, fam)
    cur = con.execute(
        "INSERT INTO items (sku, name, description, unit, min_qty, barcode, "
        "subcategory_id, family_id, position_id, supplier, lead_time_days, "
        "low_qty, lot, tech_ref) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (sku, name.strip(), kw.get("description"), kw.get("unit", "pcs"),
         kw.get("min_qty", 0), (kw.get("barcode") or None),
         sub, fam, kw.get("position_id"), kw.get("supplier"),
         kw.get("lead_time_days"), kw.get("low_qty"),
         (kw.get("lot") or "").strip() or None,
         (kw.get("tech_ref") or "").strip() or None),
    )
    return int(cur.lastrowid or 0)


def update_item(con: sqlite3.Connection, item_id: int, **kw: Any) -> None:
    """Change an item. Its code stays as it is when the classification or the
    position changes; a blank sku asks for a new one from the classification."""
    fields = [k for k in ("sku", "name", "description", "unit", "min_qty", "barcode",
                          "archived", *ITEM_MASTER_FIELDS) if k in kw]
    if not fields:
        return
    current = get_item(con, item_id)
    if current is None:
        raise ValueError(f"no item with id {item_id}")
    if "subcategory_id" in kw:
        # The family belongs to the subcategory: a new (or cleared) one sent
        # without a family drops the old family rather than clashing with it.
        sub = kw["subcategory_id"]
        keep = current["family_id"] if sub == current["subcategory_id"] else None
        kw["subcategory_id"], kw["family_id"] = classify(con, sub, kw.get("family_id", keep))
    elif "family_id" in kw:           # a family alone implies its subcategory
        fam = kw["family_id"]
        kw["subcategory_id"], kw["family_id"] = classify(
            con, None if fam is not None else current["subcategory_id"], fam)
    if "subcategory_id" in kw:
        fields = list(dict.fromkeys([*fields, "subcategory_id", "family_id"]))
    if "sku" in kw and not kw["sku"].strip():
        kw["sku"] = next_sku(con, kw.get("subcategory_id", current["subcategory_id"]),
                             kw.get("family_id", current["family_id"]))
    for f in ("lot", "tech_ref"):
        if f in kw:
            kw[f] = (kw[f] or "").strip() or None
    if "min_qty" in kw or "low_qty" in kw:
        _check_thresholds(kw.get("min_qty", current["min_qty"]),
                          kw.get("low_qty", current["low_qty"]))
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


def find_archived_by_code(con: sqlite3.Connection, code: str) -> sqlite3.Row | None:
    """The archived item a scanned code belongs to, so the UI can offer to restore
    it instead of creating a duplicate."""
    code = code.strip()
    if not code:
        return None
    return con.execute(
        "SELECT * FROM items WHERE archived = 1 AND (barcode = ? OR sku = ?) "
        "ORDER BY (barcode = ?) DESC LIMIT 1",
        (code, code, code),
    ).fetchone()


def list_archived_items(con: sqlite3.Connection) -> list[sqlite3.Row]:
    return con.execute(
        "SELECT i.*, i.id AS item_id, COALESCE(SUM(m.delta), 0) AS qty FROM items i "
        "LEFT JOIN stock_movements m ON m.item_id = i.id "
        "WHERE i.archived = 1 GROUP BY i.id ORDER BY i.name"
    ).fetchall()


# Stock filters: the column each one compares, in the joins list_items makes.
ITEM_FILTERS = {
    "macro_id": "sc.macro_id", "subcategory_id": "s.subcategory_id",
    "family_id": "s.family_id", "facility_id": "sa.facility_id",
    "area_id": "su.area_id", "unit_id": "p.unit_id", "position_id": "s.position_id",
}


def list_items(con: sqlite3.Connection, search: str | None = None,
               low_only: bool = False, limit: int = 500,
               **filters: int | None) -> list[sqlite3.Row]:
    """Active items with their on-hand qty, filtered by any of ITEM_FILTERS
    (a classification or a location node), search text and low stock."""
    sql = [("SELECT s.*, i.barcode, i.supplier, i.lead_time_days, i.lot, i.tech_ref, "
            "p.location_code "
            "FROM stock_on_hand s JOIN items i ON i.id = s.item_id "
            "LEFT JOIN subcategories sc ON sc.id = s.subcategory_id "
            "LEFT JOIN positions p ON p.id = s.position_id "
            "LEFT JOIN storage_units su ON su.id = p.unit_id "
            "LEFT JOIN storage_areas sa ON sa.id = su.area_id")]
    args: list[Any] = []
    where = []
    if search:
        where.append("(s.sku LIKE ? OR s.name LIKE ? OR i.barcode LIKE ? "
                     "OR i.tech_ref LIKE ? OR p.location_code LIKE ?)")
        args += [f"%{search}%"] * 5
    if low_only:
        # min_qty > 0 matters: an item with no reorder point set is not low, it
        # is unconfigured. Without this every newly created item (qty 0,
        # min_qty 0) shows up as needing reordering and the list is useless.
        where.append("s.min_qty > 0 AND s.qty <= s.min_qty")
    for key, value in filters.items():
        if value is not None:
            where.append(f"{ITEM_FILTERS[key]} = ?")
            args.append(value)
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


# ------------------------------------------------- classification & locations
# Two fixed-depth trees (migration 009), handled by one set of functions:
#   macro > subcategory > family              -> item codes EL-DDC-0001, CH-REA-MN-0001
#   facility > area > unit > position         -> rack codes RnD-WH-A-0.1
# A row counts as archived when it or any ancestor is.
@dataclass(frozen=True)
class Kind:
    table: str
    label: str
    parent: str | None          # parent kind
    parent_col: str | None
    code_re: str
    code_hint: str
    upper: bool                 # classification codes are capitals; location codes keep case


KINDS: dict[str, Kind] = {
    "macro": Kind("macro_categories", "macro-category", None, None,
                  r"[A-Z]{2}", "2 letters", True),
    "subcategory": Kind("subcategories", "subcategory", "macro", "macro_id",
                        r"[A-Z]{3}", "3 letters", True),
    "family": Kind("item_families", "item family", "subcategory", "subcategory_id",
                   r"[A-Z]{2}", "2 letters", True),
    "facility": Kind("facilities", "facility", None, None,
                     r"[A-Za-z]{2,3}", "2-3 letters", False),
    "area": Kind("storage_areas", "area", "facility", "facility_id",
                 r"[A-Za-z0-9]{2,3}", "2-3 letters or digits", False),
    "unit": Kind("storage_units", "storage unit", "area", "area_id",
                 r"[A-Za-z0-9]{1,3}", "1-3 letters or digits", False),
    "position": Kind("positions", "position", "unit", "unit_id",
                     r"[0-9]{1,2}(\.[0-9]{1,2})?", "a number such as 4.2", False),
}
LOCATION_KINDS = ("facility", "area", "unit", "position")


def _ancestry(kind: str) -> list[str]:
    """The kind followed by its ancestors, up to the root."""
    out = [kind]
    while KINDS[out[-1]].parent:
        out.append(KINDS[out[-1]].parent)    # type: ignore[arg-type]
    return out


def _node_sql(kind: str) -> str:
    """SELECT for one kind, with parent_id and `hidden` (it or an ancestor is
    archived) worked out by joining up the tree."""
    chain = _ancestry(kind)
    k = KINDS[kind]
    joins, flags = [], ["t0.archived"]
    for i in range(1, len(chain)):
        prev = KINDS[chain[i - 1]]
        joins.append(f"JOIN {KINDS[chain[i]].table} t{i} ON t{i}.id = t{i - 1}.{prev.parent_col}")
        flags.append(f"t{i}.archived")
    parent = f"t0.{k.parent_col}" if k.parent_col else "NULL"
    return (f"SELECT t0.*, {parent} AS parent_id, MAX({', '.join(flags)}, 0) AS hidden "
            f"FROM {k.table} t0 {' '.join(joins)}")


def _clean_code(kind: str, code: str) -> str:
    k = KINDS[kind]
    code = code.strip()
    if k.upper:
        code = code.upper()
    if not re.fullmatch(k.code_re, code):
        raise ValueError(f"a {k.label} code is {k.code_hint}")
    return code


def get_node(con: sqlite3.Connection, kind: str, node_id: int) -> sqlite3.Row | None:
    return con.execute(_node_sql(kind) + " WHERE t0.id = ?", (node_id,)).fetchone()


def list_nodes(con: sqlite3.Connection, kind: str, parent_id: int | None = None,
               include_archived: bool = False) -> list[sqlite3.Row]:
    sql, args = [_node_sql(kind)], []
    where = []
    if parent_id is not None:
        if not KINDS[kind].parent_col:
            raise ValueError(f"a {KINDS[kind].label} has no parent to filter by")
        where.append(f"t0.{KINDS[kind].parent_col} = ?")
        args.append(parent_id)
    if not include_archived:
        where.append("hidden = 0")
    if where:
        sql.append("WHERE " + " AND ".join(where))
    if kind == "position":     # 0.1, 0.2, ... 1.1 - by level, then left to right
        sql.append("ORDER BY t0.unit_id, CAST(t0.code AS INTEGER), "
                   "CAST(SUBSTR(t0.code, INSTR(t0.code, '.') + 1) AS INTEGER)")
    else:
        sql.append("ORDER BY t0.code")
    return con.execute(" ".join(sql), args).fetchall()


def create_node(con: sqlite3.Connection, kind: str, code: str, name: str = "",
                parent_id: int | None = None, note: str | None = None) -> int:
    k = KINDS[kind]
    code = _clean_code(kind, code)
    name = name.strip()
    if not name and kind != "position":
        raise ValueError(f"a {k.label} needs a name")
    cols = ["code", "name", "note"]
    vals: list[Any] = [code, name, (note or "").strip() or None]
    if k.parent_col:
        if parent_id is None:
            raise ValueError(f"a {k.label} needs a {KINDS[k.parent].label}")  # type: ignore[index]
        cols.append(k.parent_col)
        vals.append(parent_id)
    if kind == "position":
        cols.append("location_code")
        vals.append("")              # filled in just below, from the codes up the path
    cur = con.execute(f"INSERT INTO {k.table} ({', '.join(cols)}) "
                      f"VALUES ({', '.join('?' * len(cols))})", vals)
    node_id = int(cur.lastrowid or 0)
    if kind == "position":
        _refresh_location_codes(con, kind, node_id)
    return node_id


def update_node(con: sqlite3.Connection, kind: str, node_id: int, **kw: Any) -> None:
    """Change code, name, note or archived. Classification codes are locked once
    an item uses them: they are part of its code. A location code can change
    any time; the stored rack codes below it follow (labels need reprinting)."""
    k = KINDS[kind]
    current = get_node(con, kind, node_id)
    if current is None:
        raise ValueError(f"no {k.label} with id {node_id}")
    if "code" in kw:
        kw["code"] = _clean_code(kind, kw["code"])
        if kw["code"] != current["code"] and kind not in LOCATION_KINDS \
                and items_using(con, kind, node_id):
            raise ValueError(f"the code is locked once items use this {k.label}")
    if "name" in kw:
        kw["name"] = (kw["name"] or "").strip()
        if not kw["name"] and kind != "position":
            raise ValueError(f"a {k.label} needs a name")
    if "note" in kw:
        kw["note"] = (kw["note"] or "").strip() or None
    fields = [f for f in ("code", "name", "note", "archived") if f in kw]
    if not fields:
        return
    con.execute(f"UPDATE {k.table} SET {', '.join(f'{f} = ?' for f in fields)} WHERE id = ?",
                [*(kw[f] for f in fields), node_id])
    if "code" in kw and kind in LOCATION_KINDS:
        _refresh_location_codes(con, kind, node_id)


def items_using(con: sqlite3.Connection, kind: str, node_id: int) -> int:
    """Items, archived ones included, classified under this node."""
    sql = {
        "macro": "SELECT COUNT(*) FROM items i JOIN subcategories s ON s.id = i.subcategory_id "
                 "WHERE s.macro_id = ?",
        "subcategory": "SELECT COUNT(*) FROM items WHERE subcategory_id = ?",
        "family": "SELECT COUNT(*) FROM items WHERE family_id = ?",
    }[kind]
    return con.execute(sql, (node_id,)).fetchone()[0]


# The positions under a location node, as a WHERE clause on positions.
_POSITIONS_UNDER = {
    "position": "id = ?",
    "unit": "unit_id = ?",
    "area": "unit_id IN (SELECT id FROM storage_units WHERE area_id = ?)",
    "facility": "unit_id IN (SELECT u.id FROM storage_units u"
                " JOIN storage_areas a ON a.id = u.area_id WHERE a.facility_id = ?)",
}


def _refresh_location_codes(con: sqlite3.Connection, kind: str, node_id: int) -> None:
    """Rebuild the stored rack codes of the positions under one location node."""
    con.execute(
        "UPDATE positions SET location_code = ("
        " SELECT f.code || '-' || a.code || '-' || u.code || '-' || positions.code"
        " FROM storage_units u JOIN storage_areas a ON a.id = u.area_id"
        " JOIN facilities f ON f.id = a.facility_id WHERE u.id = positions.unit_id)"
        f" WHERE {_POSITIONS_UNDER[kind]}", (node_id,))


def find_position(con: sqlite3.Connection, code: str) -> sqlite3.Row | None:
    """The active position a scanned rack label belongs to."""
    code = code.strip()
    if not code:
        return None
    return con.execute(_node_sql("position") + " WHERE t0.location_code = ? AND hidden = 0",
                       (code,)).fetchone()


# ------------------------------------------------------------- item codes
FALLBACK_PREFIX = "ITM"       # items with no classification


def classify(con: sqlite3.Connection, subcategory_id: int | None,
             family_id: int | None) -> tuple[int | None, int | None]:
    """Check that a family belongs to its subcategory. A family alone implies
    its subcategory."""
    if family_id is None:
        return subcategory_id, None
    fam = get_node(con, "family", family_id)
    if fam is None:
        raise ValueError(f"no item family with id {family_id}")
    if subcategory_id is not None and fam["subcategory_id"] != subcategory_id:
        raise ValueError("that item family belongs to a different subcategory")
    return fam["subcategory_id"], family_id


def next_sku(con: sqlite3.Connection, subcategory_id: int | None,
             family_id: int | None = None, reserve: bool = True) -> str:
    """Next free MACRO-SUB-NNNN, or MACRO-SUB-FAM-NNNN with a family; the
    serial counts per full prefix. Numbers are never reused and any code
    already in use (typed by hand) is skipped. reserve=False only previews it,
    leaving the counter alone."""
    subcategory_id, family_id = classify(con, subcategory_id, family_id)
    counter: tuple[str, int] | None = None
    if subcategory_id is None:
        prefix, seq = FALLBACK_PREFIX, None
    else:
        row = con.execute(
            "SELECT m.code AS m, s.code AS s, s.next_seq FROM subcategories s "
            "JOIN macro_categories m ON m.id = s.macro_id WHERE s.id = ?",
            (subcategory_id,)).fetchone()
        if row is None:
            raise ValueError(f"no subcategory with id {subcategory_id}")
        prefix, seq = f"{row['m']}-{row['s']}", row["next_seq"]
        counter = ("subcategories", subcategory_id)
        if family_id is not None:
            fam = con.execute("SELECT code, next_seq FROM item_families WHERE id = ?",
                              (family_id,)).fetchone()
            prefix, seq = f"{prefix}-{fam['code']}", fam["next_seq"]
            counter = ("item_families", family_id)
    if seq is None:
        r = con.execute(
            "SELECT MAX(CAST(SUBSTR(sku, ?) AS INTEGER)) AS m FROM items "
            "WHERE sku LIKE ? COLLATE NOCASE", (len(prefix) + 2, prefix + "-%")).fetchone()
        seq = (r["m"] or 0) + 1
    while con.execute("SELECT 1 FROM items WHERE sku = ? COLLATE NOCASE",
                      (f"{prefix}-{seq:04d}",)).fetchone():
        seq += 1
    if counter is not None and reserve:
        con.execute(f"UPDATE {counter[0]} SET next_seq = ? WHERE id = ?", (seq + 1, counter[1]))
    return f"{prefix}-{seq:04d}"


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
    return int(cur.lastrowid or 0)


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
    return int(cur.lastrowid or 0)


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
    for table in ("items", *(k.table for k in KINDS.values()), "lots",
                  "stock_movements", "print_jobs"):
        try:
            out[table] = con.execute(f"SELECT COUNT(*) c FROM {table}").fetchone()["c"]
        except sqlite3.Error:
            out[table] = -1
    return out
