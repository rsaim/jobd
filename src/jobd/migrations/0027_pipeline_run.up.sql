-- Live run metrics: one row per pipeline process (classify worker, sweep,
-- audit, backfill). The writing process owns its row exclusively, so updates
-- are absolute-value writes — no counter-merge SQL, no row contention at any
-- mailbox size. Concurrent partition workers of one logical run share a
-- group_id and the reader aggregates.
CREATE TABLE pipeline_run (
    id uuid PRIMARY KEY,
    group_id uuid NOT NULL,
    kind text NOT NULL,
    worker text NOT NULL DEFAULT '',
    args jsonb NOT NULL DEFAULT '{}'::jsonb,
    status text NOT NULL DEFAULT 'running',
    total bigint,
    processed bigint NOT NULL DEFAULT 0,
    counters jsonb NOT NULL DEFAULT '{}'::jsonb,
    llm_calls bigint NOT NULL DEFAULT 0,
    llm_tokens_in bigint NOT NULL DEFAULT 0,
    llm_tokens_out bigint NOT NULL DEFAULT 0,
    llm_cost_usd numeric(12, 6) NOT NULL DEFAULT 0,
    errors bigint NOT NULL DEFAULT 0,
    last_error text,
    started_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz
);

-- The dashboard's poll is "active runs, then recent history" — both served
-- by these two.
CREATE INDEX pipeline_run_active_idx
    ON pipeline_run (started_at DESC) WHERE finished_at IS NULL;
CREATE INDEX pipeline_run_recent_idx ON pipeline_run (started_at DESC);
