-- 0004 — a history of ingest runs, not just the latest cursor.
--
-- `ingest_cursor` overwrites in place — it can tell you where a source will
-- resume from, never what happened last time. Once ingestion runs unattended
-- (cron, not a human watching stdout), a failure with nobody watching is a
-- failure nobody finds. This is the log that makes it findable.
--
-- Operational state, like `ingest_cursor`: `jobd rebuild` never touches it,
-- and losing it costs nothing but the log itself (I3 is unaffected).

CREATE TABLE ingest_run (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source         text NOT NULL,
    account        text NOT NULL,
    started_at     timestamptz NOT NULL DEFAULT now(),
    finished_at    timestamptz,
    fetched        integer NOT NULL DEFAULT 0,
    stored         integer NOT NULL DEFAULT 0,
    already_stored integer NOT NULL DEFAULT 0,
    rows_inserted  integer NOT NULL DEFAULT 0,
    rows_existing  integer NOT NULL DEFAULT 0,
    -- Per-message errors from a partially-successful run (ingest_account skips
    -- and continues rather than aborting). NULL, not '', when there were none.
    row_errors     text,
    -- Set only when the whole run raised — e.g. HistoryTooOld. A run with rows
    -- above and this NULL succeeded; both NULL and finished_at NULL means it
    -- is still running or the process died mid-run.
    failure        text
);

CREATE INDEX ingest_run_account_started_idx
    ON ingest_run (source, account, started_at DESC);
