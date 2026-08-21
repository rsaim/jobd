-- 0005 — day-scoped ingest runs, and a way to resume one without re-downloading.
--
-- Backfilling years of mail as one process is fragile: a single stall kills
-- everything fetched after it, nothing is visibly "in progress" until the
-- whole thing finishes, and there is no unit of work smaller than "the entire
-- range" to retry. `day` turns each calendar day into that unit — its own
-- process, its own run row, its own pass/fail.

ALTER TABLE ingest_run ADD COLUMN day date;

-- A worker's first move is "has this day already succeeded?" — this index is
-- that query. Partial or not, it stays small: one row lookup per day per
-- backfill, not per message.
CREATE INDEX ingest_run_day_idx ON ingest_run (source, account, day)
    WHERE day IS NOT NULL;

-- Resuming a killed day-worker means "which of today's message ids do I
-- already have", asked *before* the expensive per-message fetch — a list
-- call is cheap, a get call is not. That question is keyed on the source's own
-- id, not the content hash (the hash needs the payload, which is the thing
-- resuming is trying to avoid re-fetching).
CREATE UNIQUE INDEX message_external_id_idx ON message (channel, account, external_id)
    WHERE external_id IS NOT NULL;
