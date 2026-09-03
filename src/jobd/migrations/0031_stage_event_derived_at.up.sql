-- Stage events become append-only, with the newest derivation winning on read.
--
-- Deriving a timeline used to have to destroy the previous one: the unique key
-- (application_id, stage, evidence_message_id) meant a re-derivation either
-- collided with its own earlier rows or, worse, silently rewrote whichever
-- pass had claimed that evidence first. Deleting to make room is the opposite
-- of how the rest of this system stores things -- raw mail is write-once and
-- Postgres is a derived view of it (I3) -- and it throws away the record of
-- what an earlier pass concluded, which is exactly what you want when a
-- derivation turns out to be wrong.
--
-- `derived_at` stamps the batch a row came from. Rows accumulate; readers take
-- the newest batch per application and ignore the rest. Re-deriving is then a
-- pure append, correcting a bad pass costs nothing but another append, and the
-- history of what was believed when stays intact.
--
-- NULL means "before this was tracked" -- the per-message rows already on the
-- table. They sort oldest, so any real derivation supersedes them, and an
-- application no derivation has reached keeps showing what it always showed.

ALTER TABLE stage_event ADD COLUMN IF NOT EXISTS derived_at timestamptz;

-- The old key forced one row per (application, stage, evidence) for all time.
-- Including the batch lets the same conclusion be recorded again by a later
-- pass, which is the point of appending; within one batch the old guarantee
-- still holds, so a single pass cannot double-record its own evidence.
ALTER TABLE stage_event DROP CONSTRAINT IF EXISTS stage_event_evidence_key;
DROP INDEX IF EXISTS stage_event_evidence_key;
CREATE UNIQUE INDEX IF NOT EXISTS stage_event_evidence_batch_key
    ON stage_event (application_id, stage, evidence_message_id, derived_at)
    WHERE derived_at IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS stage_event_evidence_legacy_key
    ON stage_event (application_id, stage, evidence_message_id)
    WHERE derived_at IS NULL;

-- Readers ask "the newest batch for this application" on every timeline render.
CREATE INDEX IF NOT EXISTS stage_event_latest_idx
    ON stage_event (application_id, derived_at DESC NULLS LAST);
