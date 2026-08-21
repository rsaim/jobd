-- 0008 — learned sender rules (fanout classification).
--
-- The persisted half of "identify attribute as pos/neg -> update the filter
-- -> mark all other emails on this attribute": bulk_resolve/bulk_negative
-- (message repository) sweep the *current* backlog once; this table is what
-- makes the same decision apply to every message ingested *after* it too,
-- via prefilter.py reading it on every future classify run. Two match kinds
-- because a decision can be about a whole company's domain (any sender at
-- acmecorp.com) or one specific address (a recruiter's personal gmail).

CREATE TABLE sender_rule (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    match_type  text NOT NULL CHECK (match_type IN ('domain', 'address')),
    value       text NOT NULL,
    verdict     text NOT NULL CHECK (verdict IN ('positive', 'negative')),
    -- Only meaningful for a positive verdict — which company this attribute
    -- belongs to, so a fanout match can link straight to it with no
    -- extractor call, the same free path thread-carry-forward uses.
    company_id  uuid REFERENCES company (id) ON DELETE SET NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX sender_rule_key ON sender_rule (match_type, lower(value));
