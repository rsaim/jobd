-- One row per scrape run: what ran, how long, and the funnel counters the
-- live dashboard showed — kept so "what did last night's cron do" is a
-- query, not a log hunt.
CREATE TABLE scrape_run (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    started_at  timestamptz NOT NULL,
    finished_at timestamptz,
    accounts    text[] NOT NULL,
    window_days integer,
    counters    jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX scrape_run_started_idx ON scrape_run (started_at DESC);
