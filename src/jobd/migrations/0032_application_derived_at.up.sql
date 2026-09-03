-- An application records when a derivation last reached it, not just what one wrote.
--
-- Migration 0031 stamps each `stage_event` with the batch that produced it and
-- has readers keep the newest batch per application. Its closing promise was
-- that "an application no derivation has reached keeps showing what it always
-- showed" -- the legacy per-message rows survive until something better
-- replaces them.
--
-- That promise reads the wrong signal. A batch stamp only exists where a batch
-- wrote a row, so "no derivation has reached this" and "a derivation read the
-- whole chain and correctly concluded nothing" are the same absence. The
-- second case is not rare: of 461 applications, 116 have no stage rows at all
-- and 36 more are still legacy-only. Where a derivation deliberately withdrew
-- a stage, its silence was read as having nothing to say, and the superseded
-- `llm` rows stayed on the page -- an ex-employer's exit paperwork kept
-- showing as 17 `accepted` events that the current pipeline does not believe.
--
-- Withdrawing a claim is a conclusion. Recording it on the application makes
-- the two absences different facts: `derived_at` here means a derivation ran
-- against this application and its output -- however empty -- is the current
-- answer. Readers can then suppress legacy rows for an application that has
-- been derived while leaving untouched applications exactly as they were,
-- which is what 0031 meant to say.
--
-- NULL means no derivation has reached this application yet. That is the state
-- every row starts in, so the backfill below is what changes behaviour, not
-- the column.

ALTER TABLE application ADD COLUMN IF NOT EXISTS derived_at timestamptz;

-- Readers ask "has this been derived?" alongside the stage join on every
-- company render.
CREATE INDEX IF NOT EXISTS application_derived_at_idx
    ON application (derived_at)
    WHERE derived_at IS NOT NULL;

-- Backfill the applications a derivation demonstrably reached: any that
-- carries a batch-stamped row. This cannot recover the ones a derivation
-- reached and wrote nothing for -- that is the fact the column exists to
-- record and it was never written down. Those keep showing their legacy rows
-- until the next derivation stamps them, which is the same behaviour they have
-- today, so the backfill only ever removes phantoms and never invents them.
UPDATE application a
   SET derived_at = (
       SELECT max(s.derived_at) FROM stage_event s
        WHERE s.application_id = a.id AND s.derived_at IS NOT NULL)
 WHERE EXISTS (
       SELECT 1 FROM stage_event s
        WHERE s.application_id = a.id AND s.derived_at IS NOT NULL);
