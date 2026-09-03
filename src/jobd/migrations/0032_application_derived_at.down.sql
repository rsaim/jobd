DROP INDEX IF EXISTS application_derived_at_idx;
ALTER TABLE application DROP COLUMN IF EXISTS derived_at;
