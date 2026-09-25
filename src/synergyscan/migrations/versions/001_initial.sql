-- Initial schema.
--
-- Stock is an append-only ledger, not a mutable quantity column. Every change
-- is a row in stock_movements and the on-hand figure is derived. That buys an
-- audit trail for free, makes stocktake reconciliation a query rather than a
-- correction, and removes the lost-update race you get when two people scan
-- the same item at once.

CREATE TABLE items (
    id          INTEGER PRIMARY KEY,
    sku         TEXT    NOT NULL UNIQUE COLLATE NOCASE,
    name        TEXT    NOT NULL,
    description TEXT,
    unit        TEXT    NOT NULL DEFAULT 'pcs',
    min_qty     REAL    NOT NULL DEFAULT 0,
    barcode     TEXT             UNIQUE COLLATE NOCASE,  -- scanned code, if it differs from the SKU
    archived    INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE locations (
    id         INTEGER PRIMARY KEY,
    code       TEXT    NOT NULL UNIQUE COLLATE NOCASE,
    name       TEXT    NOT NULL,
    archived   INTEGER NOT NULL DEFAULT 0,
    created_at TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- Never UPDATE or DELETE a row in this table. A mistake is corrected by
-- appending an opposing movement, which is what an auditor expects to see.
CREATE TABLE stock_movements (
    id          INTEGER PRIMARY KEY,
    item_id     INTEGER NOT NULL REFERENCES items(id),
    location_id INTEGER          REFERENCES locations(id),
    delta       REAL    NOT NULL CHECK (delta <> 0),
    reason      TEXT    NOT NULL CHECK (reason IN
                    ('receive', 'issue', 'adjust', 'stocktake', 'move_in', 'move_out')),
    note        TEXT,
    actor       TEXT,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_movements_item ON stock_movements(item_id);
CREATE INDEX idx_movements_created ON stock_movements(created_at);
CREATE INDEX idx_items_barcode ON items(barcode);

-- Audit trail for labels, so "which labels did we print for that batch?" is
-- answerable after the fact.
CREATE TABLE print_jobs (
    id          TEXT PRIMARY KEY,
    item_id     INTEGER REFERENCES items(id),
    spec_json   TEXT NOT NULL,
    state       TEXT NOT NULL,
    error       TEXT,
    copies      INTEGER NOT NULL DEFAULT 1,
    queued_at   TEXT NOT NULL,
    finished_at TEXT
);

CREATE VIEW stock_on_hand AS
SELECT i.id            AS item_id,
       i.sku           AS sku,
       i.name          AS name,
       i.unit          AS unit,
       i.min_qty       AS min_qty,
       COALESCE(SUM(m.delta), 0) AS qty,
       MAX(m.created_at)         AS last_movement_at
FROM items i
LEFT JOIN stock_movements m ON m.item_id = i.id
WHERE i.archived = 0
GROUP BY i.id;

CREATE VIEW stock_by_location AS
SELECT m.item_id, m.location_id, SUM(m.delta) AS qty
FROM stock_movements m
GROUP BY m.item_id, m.location_id;
