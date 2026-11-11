-- Two independent trees replace Area > Category and the flat shelf list
-- (Docs/Inventory_Proposal_2.pptx, slides 16-27):
--
--   classification  Macro-category (2) > Subcategory (3) > Item family (2, optional)
--                   giving item codes MACRO-SUB-NNNN or MACRO-SUB-FAM-NNNN
--   location        Facility > Area > Storage unit > Position
--                   giving rack codes such as RnD-WH-A-0.1
--
-- An item points at one subcategory (plus an optional family) and one
-- position; moving it changes position_id only, never its code.
--
-- Additive: areas, categories, locations and items.category_id/location_id
-- are left as they were, unused from here on, so a rollback to an older
-- release still finds everything where it expects it (the same choice as 005).
--
-- Codes are unique within their parent, compared without case. A row is
-- hidden when it or any ancestor is archived, so archiving a branch needs no
-- cascade bookkeeping and restoring it brings back exactly what it hid.
-- `note` holds the slides' Examples (classification) or Contents (positions).
-- next_seq is the next serial for codes with that prefix (see db.next_sku).

CREATE TABLE macro_categories (
    id         INTEGER PRIMARY KEY,
    code       TEXT    NOT NULL UNIQUE COLLATE NOCASE,
    name       TEXT    NOT NULL,
    note       TEXT,
    archived   INTEGER NOT NULL DEFAULT 0,
    created_at TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE subcategories (
    id         INTEGER PRIMARY KEY,
    macro_id   INTEGER NOT NULL REFERENCES macro_categories(id),
    code       TEXT    NOT NULL COLLATE NOCASE,
    name       TEXT    NOT NULL,
    note       TEXT,
    next_seq   INTEGER NOT NULL DEFAULT 1,
    archived   INTEGER NOT NULL DEFAULT 0,
    created_at TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (macro_id, code)
);

CREATE TABLE item_families (
    id             INTEGER PRIMARY KEY,
    subcategory_id INTEGER NOT NULL REFERENCES subcategories(id),
    code           TEXT    NOT NULL COLLATE NOCASE,
    name           TEXT    NOT NULL,
    note           TEXT,
    next_seq       INTEGER NOT NULL DEFAULT 1,
    archived       INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (subcategory_id, code)
);

CREATE TABLE facilities (
    id         INTEGER PRIMARY KEY,
    code       TEXT    NOT NULL UNIQUE COLLATE NOCASE,
    name       TEXT    NOT NULL,
    note       TEXT,
    archived   INTEGER NOT NULL DEFAULT 0,
    created_at TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE storage_areas (
    id          INTEGER PRIMARY KEY,
    facility_id INTEGER NOT NULL REFERENCES facilities(id),
    code        TEXT    NOT NULL COLLATE NOCASE,
    name        TEXT    NOT NULL,
    note        TEXT,
    archived    INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (facility_id, code)
);

CREATE TABLE storage_units (
    id         INTEGER PRIMARY KEY,
    area_id    INTEGER NOT NULL REFERENCES storage_areas(id),
    code       TEXT    NOT NULL COLLATE NOCASE,
    name       TEXT    NOT NULL,
    note       TEXT,
    archived   INTEGER NOT NULL DEFAULT 0,
    created_at TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (area_id, code)
);

-- location_code is the whole path, stored so a rack label can be scanned and
-- looked up directly. db.py rewrites it whenever a code along the path changes.
CREATE TABLE positions (
    id            INTEGER PRIMARY KEY,
    unit_id       INTEGER NOT NULL REFERENCES storage_units(id),
    code          TEXT    NOT NULL COLLATE NOCASE,      -- <level>.<position>, level 0 at the bottom
    location_code TEXT    NOT NULL UNIQUE COLLATE NOCASE,
    name          TEXT    NOT NULL DEFAULT '',
    note          TEXT,
    archived      INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (unit_id, code)
);

CREATE INDEX idx_subcategories_macro ON subcategories(macro_id);
CREATE INDEX idx_item_families_subcategory ON item_families(subcategory_id);
CREATE INDEX idx_storage_areas_facility ON storage_areas(facility_id);
CREATE INDEX idx_storage_units_area ON storage_units(area_id);
CREATE INDEX idx_positions_unit ON positions(unit_id);

ALTER TABLE items ADD COLUMN subcategory_id INTEGER REFERENCES subcategories(id);
ALTER TABLE items ADD COLUMN family_id      INTEGER REFERENCES item_families(id);
ALTER TABLE items ADD COLUMN position_id    INTEGER REFERENCES positions(id);
ALTER TABLE items ADD COLUMN tech_ref       TEXT;     -- e.g. the vault code of a mechanical part

CREATE INDEX idx_items_subcategory ON items(subcategory_id);
CREATE INDEX idx_items_family ON items(family_id);
CREATE INDEX idx_items_position ON items(position_id);

-- Recreated to expose the new columns. category_id and location_id stay so
-- an older release still runs against this view after a rollback.
DROP VIEW stock_on_hand;
CREATE VIEW stock_on_hand AS
SELECT i.id             AS item_id,
       i.sku            AS sku,
       i.name           AS name,
       i.unit           AS unit,
       i.min_qty        AS min_qty,
       i.low_qty        AS low_qty,
       i.category_id    AS category_id,
       i.location_id    AS location_id,
       i.subcategory_id AS subcategory_id,
       i.family_id      AS family_id,
       i.position_id    AS position_id,
       COALESCE(SUM(m.delta), 0) AS qty,
       MAX(m.created_at)         AS last_movement_at
FROM items i
LEFT JOIN stock_movements m ON m.item_id = i.id
WHERE i.archived = 0
GROUP BY i.id;

-- ------------------------------------------------------------ seed data
-- The lists agreed in the proposal. They are ordinary rows: Setup edits,
-- extends and archives them like anything added by hand.
INSERT INTO macro_categories (code, name) VALUES
    ('EL', 'Electrical'),
    ('ME', 'Mechanical'),
    ('CH', 'Chemical'),
    ('HY', 'Hydraulics'),
    ('CI', 'Control & Instrumentation');

-- Slides 17, 18, 20, 22 and 23.
INSERT INTO subcategories (macro_id, code, name, note)
SELECT m.id, v.column2, v.column3, v.column4
FROM (VALUES
    ('EL', 'DDC', 'DC-DC Converters', 'DC-DC, AUX DC-DC'),
    ('EL', 'PCS', 'Power Conversion Systems', 'PCS'),
    ('EL', 'PSU', 'Power Supply Units', 'SITOP PSU6200'),
    ('EL', 'CAB', 'Cables & Wires', 'AWG3, AWG15, AWG20'),
    ('EL', 'CON', 'Electrical Connectors', 'Terminals, connectors'),
    ('EL', 'PCP', 'Passive Components', 'Resistors, capacitors'),
    ('EL', 'PRO', 'Electrical Protection & Switching', 'Circuit breakers, fuses, switches, disconnectors'),
    ('EL', 'ENC', 'Electrical Enclosures', 'Cabinets, junction boxes, electrical boxes'),
    ('ME', 'SKD', 'Skids', 'Structural skid'),
    ('ME', 'SUP', 'Supports', 'Pipe supports'),
    ('ME', 'RCK', 'Racks', '19-inch server rack'),
    ('ME', 'TRY', 'Trays & Containment', 'Spill trays'),
    ('ME', 'PLT', 'Plates & Covers', 'Front/back plates, mid plates'),
    ('ME', 'FRM', 'Frames', 'Stack flow frames'),
    ('ME', 'MAN', 'Manifolds', 'Stack manifolds'),
    ('ME', 'GSK', 'Gaskets', 'Flat and chord gaskets'),
    ('ME', 'ORG', 'O-Rings', 'NBR, EPDM O-rings'),
    ('ME', 'FAS', 'Fasteners', 'Screws, nuts, washers, tie-rods'),
    ('ME', 'SPR', 'Springs', 'Springs'),
    ('ME', 'PIN', 'Alignment Pins', 'Stack alignment pins'),
    ('ME', 'ADH', 'Adhesives & Sealants', 'Elastosil E43'),
    ('ME', 'MSH', 'Meshes & Protective Layers', 'Mosquito net, nonwoven protector'),
    ('CH', 'REA', 'Reagents & Chemicals', 'KOH, ZnO, sulfur'),
    ('CH', 'CAL', 'Calibration standards', 'pH 4.01, pH 7.00, KCl 3.0M cond. std.'),
    ('CH', 'MEM', 'Membranes', 'FAAM-15, FAAM-20'),
    ('CH', 'FOM', 'Metal Foams', 'Fe foam, 40/60/110 PPI'),
    ('CH', 'MTS', 'Metal Sheets', 'Zn, Cu, stainless steel, Zn-coated Fe'),
    ('CH', 'BPP', 'Bipolar Plates', 'Stack bipolar plates'),
    ('HY', 'PMP', 'Pumps', 'Electrolyte circulation pumps'),
    ('HY', 'PSV', 'Pressure Relief Valves', 'Pressure relief valves'),
    ('HY', 'BVL', 'Ball Valves', 'Manual ball valves'),
    ('HY', 'BFV', 'Butterfly Valves', 'Manual butterfly valves'),
    ('HY', 'CVL', 'Check Valves', 'Non-return valves'),
    ('HY', 'AVL', 'Actuated Valves', 'Actuated ball/butterfly valves'),
    ('HY', 'PIP', 'Rigid Pipes', 'PVC-U DN25–DN150'),
    ('HY', 'TUB', 'Flexible Tubing', 'Silicone, Viton, PVC'),
    ('HY', 'ELB', 'Elbows', '45° and 90° elbows'),
    ('HY', 'TEE', 'Tee Fittings', 'Equal and reducing tees'),
    ('HY', 'RED', 'Reducers', 'DN150/DN100, DN50/DN25'),
    ('HY', 'FLG', 'Flanges', 'DN100 flanges'),
    ('HY', 'FIT', 'Fittings & Connectors', 'Hose barbs, straight connectors'),
    ('HY', 'TNK', 'Tanks & Vessels', '20 L and 30 L containers'),
    ('CI', 'PLC', 'PLCs & CPUs', 'CPU1510SP F-1 PN'),
    ('CI', 'IOM', 'I/O Modules', 'DI, DQ, AI, AQ, F-DI, F-DQ'),
    ('CI', 'PAC', 'PLC Accessories', 'BaseUnits, BusAdapter, memory cards'),
    ('CI', 'SWL', 'Software & Licenses', 'SIMATIC OPC UA'),
    ('CI', 'HMI', 'Human-Machine Interfaces', 'MTP1200 Unified Comfort'),
    ('CI', 'PTR', 'Pressure Transmitters', '0–4 bar transmitter'),
    ('CI', 'FTR', 'Flow Transmitters / Meters', 'Vortex flow meter'),
    ('CI', 'TTR', 'Temperature Transmitters', 'Temperature transmitter'),
    ('CI', 'LTR', 'Level Transmitters', 'Level transmitter'),
    ('CI', 'VTR', 'Voltage Transducers', 'Voltage sensor'),
    ('CI', 'CTR', 'Current Transducers', 'Current sensor'),
    ('CI', 'FCT', 'Flow controllers', 'Flow controllers, mass flow controllers (MFCs)'),
    ('CI', 'TCT', 'Temperature Controllers', 'PID temperature controllers, digital temperature controllers'),
    ('CI', 'SPR', 'Spare equipment', 'Spare equipment')
) AS v JOIN macro_categories m ON m.code = v.column1;

-- Slide 21. Families for the other subcategories are added in Setup as they are defined.
INSERT INTO item_families (subcategory_id, code, name, note)
SELECT s.id, v.column3, v.column4, v.column5
FROM (VALUES
    ('CH', 'REA', 'MN', 'Main', 'KOH, S, ZnO'),
    ('CH', 'REA', 'IS', 'Inorganic salts', 'KCl, CuSO4'),
    ('CH', 'REA', 'OS', 'Organic salts', 'Sodium citrate, Ferrocene'),
    ('CH', 'REA', 'PA', 'Polymeric additives', 'PEI, PVA'),
    ('CH', 'REA', 'MP', 'Metallic powders', 'Ferrite, Zn powder'),
    ('CH', 'REA', 'SV', 'Solvents', 'H2SO4, H2O, HCl 1.000M STD')
) AS v
JOIN macro_categories m ON m.code = v.column1
JOIN subcategories s ON s.macro_id = m.id AND s.code = v.column2;

INSERT INTO facilities (code, name) VALUES
    ('LsT', 'Large-scale Testing Facility'),
    ('HQ',  'Headquarters'),
    ('RnD', 'Research and Development Facility');

INSERT INTO storage_areas (facility_id, code, name)
SELECT id, 'WH', 'Warehouse' FROM facilities WHERE code = 'RnD';

INSERT INTO storage_units (area_id, code, name)
SELECT a.id, v.column1, 'Pallet shelf ' || v.column1
FROM (VALUES ('A'), ('B'), ('C')) AS v
JOIN storage_areas a ON a.code = 'WH'
JOIN facilities f ON f.id = a.facility_id AND f.code = 'RnD';

-- Slides 25-27. The note is what the pallet-shelf inventory listed there, as
-- a starting point for entering those items.
INSERT INTO positions (unit_id, code, location_code, note)
SELECT u.id, v.column2, 'RnD-WH-' || v.column1 || '-' || v.column2, v.column3
FROM (VALUES
    ('A', '0.1', 'Ground cables cover, EPDM gasket roll'),
    ('A', '0.2', 'Silica'),
    ('A', '0.3', 'Xanthan gum, Sodium Citrate, Potassium Chloride (KCl), Zinc Oxide (ZnO)'),
    ('A', '1.1', 'VoltStorage stack'),
    ('A', '1.2', 'VoltStorage stack'),
    ('A', '1.3', 'Shear cutter'),
    ('A', '2.1', 'Cables 5 poles 25 mm2, Rectifiers (5V, 12V, 48V, 60V), Victron Inverters and MPPT'),
    ('A', '2.2', 'Lead-acid batteries, Empty boxes (power tools)'),
    ('A', '2.3', 'Portable pressure washer, Inox containers, Argon cylinders + tubes + valves, Chain, Peristaltic pumps, Mixer blades, Hydrothermal reactor, Oil + funnels'),
    ('A', '3.1', 'Bolts for Schmaltz stack, Schmaltz stack components'),
    ('A', '3.2', 'Schmaltz stack components'),
    ('A', '3.3', 'Centrifugal pumps'),
    ('A', '4.1', 'Empty IBC'),
    ('A', '4.2', 'Empty IBC'),
    ('B', '0.1', 'Sulfur sacks, Na2S, Na2SO3, NaCl'),
    ('B', '0.2', 'Sodium Hydroxide sack, Potassium Hydroxide sack'),
    ('B', '0.3', 'Potassium Hydroxide bucket, Sodium Hydroxide bucket, PPG, Potassium chloride bucket'),
    ('B', '1.1', 'Low power electric cables 1.5+'),
    ('B', '1.2', 'PLC terminal blocks, Cooling fans, Thermomagnetic switches, Derivation boxes, Ethernet cables, Heat controllers + probes, Pump inverters, Bus bars'),
    ('B', '1.3', 'Empty'),
    ('B', '2.1', 'High power electric cables 25 +'),
    ('B', '2.2', 'Aspirators + flexible ducts, Heating elements, Flow, P, T, level sensors'),
    ('B', '2.3', 'Empty'),
    ('B', '3.1', 'Empty'),
    ('B', '3.2', 'Empty'),
    ('B', '3.3', 'Empty'),
    ('B', '4.1', 'Disassembled shelves'),
    ('B', '4.2', 'Disassembled shelves'),
    ('B', '4.3', 'Water hose (Pozzi), Disassembled shelves, Water boiler (lab)'),
    ('C', '0.1', 'Sulfur sacks'),
    ('C', '0.2', 'Potassium hydroxide sacks'),
    ('C', '0.3', 'ZH energy stack components'),
    ('C', '1.1', 'Flex PVC tube 32, Compressor tubes + pistol, Hose tube, Corrugated tube (electrical), Flex PVC tube 40, Peristaltic tube (Fedrotek)'),
    ('C', '1.2', 'Mech components (Al profile), Quick-step connectors (for tanks), PVC adapter, Tube connectors 20, 25'),
    ('C', '1.3', 'Filters, Non return & spring valves 32, Tube connectors 32, 40'),
    ('C', '2.1', 'Empty'),
    ('C', '2.2', 'Metallic plates (Galvanized steel)'),
    ('C', '2.3', 'PP sheets (0.8 mm)'),
    ('C', '3.1', 'Empty'),
    ('C', '3.2', 'Tubes 40 L (PP), Filtration flasks (glass), Jugs (PP), Buckets (PP), Trays (PP)'),
    ('C', '3.3', 'Cans 10-20 L, Tubes 40 L (PP)'),
    ('C', '4.1', 'Absorbent paper (lab use)'),
    ('C', '4.2', 'Absorbent paper (lab use)'),
    ('C', '4.3', '2x Electrolyte tanks (200L)')
) AS v
JOIN facilities f ON f.code = 'RnD'
JOIN storage_areas a ON a.facility_id = f.id AND a.code = 'WH'
JOIN storage_units u ON u.area_id = a.id AND u.code = v.column1;

