-- 0015 — company_verification: an independent LLM audit pass over one
-- company/agency's whole recorded message chain (services/verify.py), not
-- the per-message classify path. A finding, never a mutation: nothing here
-- changes `company`, `sender_rule`, or any message — a human (or
-- `jobd verify --apply`, which only ever writes non-negative suggested
-- rules, see that command's help) acts on a row afterward.
--
-- Insert-only, one row per pass, so a re-verify's history stays visible
-- instead of overwriting whatever the last pass concluded.

CREATE TABLE company_verification (
    id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id              uuid NOT NULL REFERENCES company (id) ON DELETE CASCADE,
    -- The model id used for this pass (e.g. "openrouter/anthropic/claude-3.5-
    -- haiku") — what makes a later re-verify with a stronger model legible as
    -- a distinct, comparable row rather than an unexplained changed answer.
    model                   text NOT NULL,
    message_count           int NOT NULL,
    classification_correct  boolean,
    verified_name           text,
    verified_kind           text CHECK (verified_kind IN ('employer', 'agency')),
    verified_domain         text,
    reasoning               text,
    -- Candidate sender_rule rows the model proposed from reading the whole
    -- chain — [{match_type, value, verdict, reason}, ...]. Kept whole, same
    -- reasoning as review_queue.extraction: normalising into columns would
    -- lose exactly the fields that explain why a rule was proposed.
    suggested_rules         jsonb NOT NULL DEFAULT '[]',
    -- The full structured response, verbatim — the "other details of the
    -- call" the verified_* columns above are extracted from, kept whole so
    -- nothing is lost to a parsing decision made today.
    raw_response             jsonb NOT NULL,
    created_at               timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX company_verification_company_idx
    ON company_verification (company_id, created_at DESC);
