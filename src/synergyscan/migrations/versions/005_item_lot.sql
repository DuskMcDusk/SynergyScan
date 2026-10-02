-- The lot is a plain property of the item (a free-text lot/batch code), not a
-- separate record. Items that already had lots keep their first active one;
-- the lots table and stock_movements.lot_id stay untouched so a rollback to an
-- older release still finds everything where it expects it.
ALTER TABLE items ADD COLUMN lot TEXT;

UPDATE items SET lot = (
    SELECT l.code FROM lots l
    WHERE l.item_id = items.id AND l.archived = 0
    ORDER BY l.id LIMIT 1
);
