-- Category prefixes for generated SKUs (PREFIX-0001). prefix is nullable here
-- because the suggestion rule lives in Python (db.suggest_prefix); existing
-- categories get theirs assigned by db.backfill_prefixes() on first access.
-- next_seq is the next number to hand out; generation also skips any SKU
-- already in use, so it needs no backfill.
ALTER TABLE categories ADD COLUMN prefix   TEXT;
ALTER TABLE categories ADD COLUMN next_seq INTEGER NOT NULL DEFAULT 1;
CREATE UNIQUE INDEX idx_categories_prefix ON categories(prefix COLLATE NOCASE)
    WHERE prefix IS NOT NULL;
