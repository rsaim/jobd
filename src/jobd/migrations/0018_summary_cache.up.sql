-- Cache, not a record: droppable and regenerable, same posture migration
-- 0011 took for raw_payload. See docs/chat-and-summaries.md §5.
--
-- `summary` is jsonb, not text (the design doc's original shape) - a
-- per-company summary is asked for as structured sections (headline,
-- timeline bullets, flagged messages, TODOs, a suggested reply), and a
-- renderer picking those apart out of prose would be reparsing what the
-- model already returned as JSON via its own schema.
CREATE TABLE summary_cache (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    scope        text NOT NULL CHECK (scope IN ('briefing', 'company')),
    scope_id     uuid REFERENCES company (id) ON DELETE CASCADE,
    fingerprint  text NOT NULL,
    model        text NOT NULL,
    summary      jsonb NOT NULL,
    -- Message ids the summary cites (e.g. a flagged message) - validated
    -- against the generation input before the row is written, so a
    -- fabricated id never reaches this column. See summaries.py.
    citations    jsonb NOT NULL DEFAULT '[]',
    created_at   timestamptz NOT NULL DEFAULT now()
);

-- Partial uniques rather than an expression index over coalesce(): the
-- pattern company_domain_key and review_queue_open_key already use, and it
-- keeps "one briefing row" and "one row per company" as two legible rules.
CREATE UNIQUE INDEX summary_cache_briefing_key
    ON summary_cache (scope) WHERE scope_id IS NULL;
CREATE UNIQUE INDEX summary_cache_company_key
    ON summary_cache (scope, scope_id) WHERE scope_id IS NOT NULL;
