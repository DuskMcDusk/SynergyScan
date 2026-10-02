-- A location (shelf) belongs to an area: Area A and Area B can each have a
-- "Shelf 1". So the code is unique within an area, not across the whole
-- warehouse. SQLite cannot drop the old column-level UNIQUE on code, so the
-- table is rebuilt with the same ids; items.location_id and
-- stock_movements.location_id keep pointing at the right rows.
--
-- apply_all switches foreign key enforcement off around each migration (the
-- procedure SQLite documents for rebuilding a table) and runs
-- PRAGMA foreign_key_check before committing, so a bad copy still rolls back.
--
-- area_id is nullable only for locations created before this migration; they
-- keep working and can be assigned an area in Setup. New locations require one.
CREATE TABLE locations_new (
    id         INTEGER PRIMARY KEY,
    area_id    INTEGER REFERENCES areas(id),
    code       TEXT    NOT NULL COLLATE NOCASE,
    name       TEXT    NOT NULL,
    archived   INTEGER NOT NULL DEFAULT 0,
    created_at TEXT    NOT NULL DEFAULT (datetime('now'))
);

INSERT INTO locations_new (id, code, name, archived, created_at)
SELECT id, code, name, archived, created_at FROM locations;

DROP TABLE locations;
ALTER TABLE locations_new RENAME TO locations;

CREATE UNIQUE INDEX idx_locations_area_code ON locations(area_id, code COLLATE NOCASE);
CREATE INDEX idx_locations_area ON locations(area_id);
-- NULLs never collide in the index above, so keep codes of the not-yet-assigned
-- legacy locations unique among themselves, as they were before.
CREATE UNIQUE INDEX idx_locations_unassigned_code ON locations(code COLLATE NOCASE)
    WHERE area_id IS NULL;
