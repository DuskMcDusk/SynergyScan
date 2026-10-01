-- The free-text Specification field on items is no longer needed; the item
-- name/description carries that detail. No view or index references it, so a
-- plain DROP COLUMN (SQLite 3.35+) is enough.
ALTER TABLE items DROP COLUMN specification;
