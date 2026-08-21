DROP INDEX IF EXISTS message_thread_idx;

ALTER TABLE message DROP COLUMN IF EXISTS thread_id;
