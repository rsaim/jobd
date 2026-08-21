-- 0002 — where each source resumes from.
--
-- Operational state, not a derived artifact: `jobd rebuild` reads raw storage
-- and never touches this table. Losing it costs one full re-sync, which is
-- slow and completely safe, because ingestion is idempotent (I2). That is the
-- reason it can live in the rebuildable store at all.
--
-- Not in the secret store, either: a Gmail history id is not a secret, and
-- mixing operational state into a keychain makes both harder to reason about.

CREATE TABLE ingest_cursor (
    source     text NOT NULL,
    account    text NOT NULL,
    -- Opaque to jobd. Gmail puts a history id here; another source may not.
    cursor     text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (source, account)
);
