-- Archiving an area also archives its categories and locations; restoring the
-- area brings back exactly those, and not the ones that had been archived on
-- their own before. archived_by_area remembers which is which.
ALTER TABLE categories ADD COLUMN archived_by_area INTEGER NOT NULL DEFAULT 0;
ALTER TABLE locations  ADD COLUMN archived_by_area INTEGER NOT NULL DEFAULT 0;
