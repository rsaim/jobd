-- 0006 — thread_id on message (M5 deterministic filter: conversation chains).
--
-- Gmail's threadId was already flowing through as raw metadata (see
-- adapters/gmail/source.py) but never landed on the row, so nothing could ask
-- "has any other message in this thread already been recorded". That question
-- is the deterministic-filter equivalent of "I already know this thread is a
-- hiring conversation" — once one message in a thread resolves to a company,
-- every reply in it is job-related by construction, the same way an ATS
-- domain hit is (see prefilter.py's status cascade).
--
-- Nullable and unindexed-when-null: LinkedIn messages and anything imported
-- without a native thread concept simply carry no value here, same pattern as
-- `company.domain`.

ALTER TABLE message ADD COLUMN thread_id text;

CREATE INDEX message_thread_idx ON message (thread_id) WHERE thread_id IS NOT NULL;
