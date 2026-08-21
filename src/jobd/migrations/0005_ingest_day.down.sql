DROP INDEX IF EXISTS message_external_id_idx;
DROP INDEX IF EXISTS ingest_run_day_idx;
ALTER TABLE ingest_run DROP COLUMN IF EXISTS day;
