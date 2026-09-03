DROP INDEX IF EXISTS stage_event_latest_idx;
DROP INDEX IF EXISTS stage_event_evidence_legacy_key;
DROP INDEX IF EXISTS stage_event_evidence_batch_key;
ALTER TABLE stage_event DROP COLUMN IF EXISTS derived_at;
CREATE UNIQUE INDEX IF NOT EXISTS stage_event_evidence_key
    ON stage_event (application_id, stage, evidence_message_id);
