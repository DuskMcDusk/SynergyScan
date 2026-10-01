-- Area -> Category taxonomy, Lot/Batch tracking, and the item-master fields
-- called for by the pilot proposal (Docs/requirements.md): Category,
-- Specification, Supplier, Lead time, a primary Location, and a soft
-- threshold for a three-tier (Sufficient/Low/Reorder) availability model.
--
-- Additive only: every new column is nullable and every new table stands on
-- its own, so existing items/movements rows and every existing query keep
-- working unchanged.

CREATE TABLE areas (
    id         INTEGER PRIMARY KEY,
    name       TEXT    NOT NULL UNIQUE COLLATE NOCASE,
    archived   INTEGER NOT NULL DEFAULT 0,
    created_at TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- Exactly two fixed levels (Area -> Category), not a generic tree: a
-- category always belongs to one area.
CREATE TABLE categories (
    id         INTEGER PRIMARY KEY,
    area_id    INTEGER NOT NULL REFERENCES areas(id),
    name       TEXT    NOT NULL,
    archived   INTEGER NOT NULL DEFAULT 0,
    created_at TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (area_id, name COLLATE NOCASE)
);
CREATE INDEX idx_categories_area ON categories(area_id);

-- A lot is metadata attached to an item, referenced optionally from
-- stock_movements.lot_id. It holds no quantity of its own - on-hand per lot
-- is derived by summing its movements, exactly like on_hand() already does
-- per item.
CREATE TABLE lots (
    id          INTEGER PRIMARY KEY,
    item_id     INTEGER NOT NULL REFERENCES items(id),
    code        TEXT    NOT NULL,             -- e.g. "Supplier Lot 4582"
    received_at TEXT,
    expires_at  TEXT,
    note        TEXT,
    archived    INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (item_id, code COLLATE NOCASE)
);
CREATE INDEX idx_lots_item ON lots(item_id);

-- Item master fields from the proposal's field table, a primary/default
-- Location, and the soft "low" threshold for the 3-tier availability model:
--   qty <= min_qty           -> "reorder"  (existing hard threshold)
--   min_qty < qty <= low_qty -> "low"      (new soft threshold)
--   otherwise                -> "sufficient"
-- low_qty may be NULL, meaning no soft threshold is configured. SQLite
-- forbids a CHECK on ALTER TABLE ADD COLUMN that references another column,
-- so the low_qty >= min_qty ordering rule is enforced in db.py instead, the
-- same way add_movement() already enforces its invariants in Python.
ALTER TABLE items ADD COLUMN category_id    INTEGER REFERENCES categories(id);
ALTER TABLE items ADD COLUMN location_id    INTEGER REFERENCES locations(id);
ALTER TABLE items ADD COLUMN specification  TEXT;
ALTER TABLE items ADD COLUMN supplier       TEXT;
ALTER TABLE items ADD COLUMN lead_time_days INTEGER;
ALTER TABLE items ADD COLUMN low_qty        REAL;

CREATE INDEX idx_items_category ON items(category_id);
CREATE INDEX idx_items_location ON items(location_id);

-- Optional per movement: which lot this receive/issue/stocktake touched.
ALTER TABLE stock_movements ADD COLUMN lot_id INTEGER REFERENCES lots(id);
CREATE INDEX idx_movements_lot ON stock_movements(lot_id);

-- SQLite cannot ALTER a view, so recreate stock_on_hand to expose the new
-- item columns that list_items() needs directly. Area/category names and lot
-- rollups are joined explicitly by the caller where needed instead of being
-- baked into this view.
DROP VIEW stock_on_hand;
CREATE VIEW stock_on_hand AS
SELECT i.id            AS item_id,
       i.sku           AS sku,
       i.name          AS name,
       i.unit          AS unit,
       i.min_qty       AS min_qty,
       i.low_qty       AS low_qty,
       i.category_id   AS category_id,
       i.location_id   AS location_id,
       COALESCE(SUM(m.delta), 0) AS qty,
       MAX(m.created_at)         AS last_movement_at
FROM items i
LEFT JOIN stock_movements m ON m.item_id = i.id
WHERE i.archived = 0
GROUP BY i.id;

-- stock_by_location is unchanged: it is about movement placement, which is
-- orthogonal to the taxonomy/lot additions here.
