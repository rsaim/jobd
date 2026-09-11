DROP INDEX IF EXISTS stage_event_manual_key;
ALTER TABLE stage_event DROP CONSTRAINT IF EXISTS stage_event_evidence_present;
-- Only restorable if no evidence-free rows remain; they are the human's, so
-- they go with the table that explains them.
DELETE FROM stage_event WHERE evidence_message_id IS NULL;
ALTER TABLE stage_event ALTER COLUMN evidence_message_id SET NOT NULL;
DROP INDEX IF EXISTS conversation_application_idx;
DROP INDEX IF EXISTS conversation_company_idx;
DROP TABLE IF EXISTS conversation;
