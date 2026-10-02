-- A location is identified by its name alone ("Shelf 1"); the separate short
-- code is dropped. The name is unique within an area, so any names that would
-- now collide inside one area (they only differed by code before) get their
-- code appended, e.g. "Shelf (D1)", instead of failing the migration.
--
-- The table is rebuilt with the same ids, so items.location_id and
-- stock_movements.location_id keep pointing at the right rows (see 006 for
-- how apply_all makes that safe).
UPDATE locations SET name = name || ' (' || code || ')'
WHERE EXISTS (
    SELECT 1 FROM locations o
    WHERE o.id < locations.id
      AND o.area_id IS locations.area_id
      AND o.name = locations.name COLLATE NOCASE
);

CREATE TABLE locations_new (
    id         INTEGER PRIMARY KEY,
    area_id    INTEGER REFERENCES areas(id),
    name       TEXT    NOT NULL COLLATE NOCASE,
    archived   INTEGER NOT NULL DEFAULT 0,
    created_at TEXT    NOT NULL DEFAULT (datetime('now'))
);

INSERT INTO locations_new (id, area_id, name, archived, created_at)
SELECT id, area_id, name, archived, created_at FROM locations;

DROP TABLE locations;
ALTER TABLE locations_new RENAME TO locations;

CREATE UNIQUE INDEX idx_locations_area_name ON locations(area_id, name COLLATE NOCASE);
CREATE UNIQUE INDEX idx_locations_unassigned_name ON locations(name COLLATE NOCASE)
    WHERE area_id IS NULL;
CREATE INDEX idx_locations_area ON locations(area_id);
