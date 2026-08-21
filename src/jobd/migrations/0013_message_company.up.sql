-- 0013 — the secondary company/agency link on a message.
--
-- `message.company_id` (0001) stays the primary, timeline-organizing link —
-- unchanged, every existing query still reads it directly. This table is
-- purely additive: it exists for the one case a single FK cannot represent,
-- a message that names both a recruiting agency *and* the specific client
-- company it concerns. Mirrors contact_company's shape exactly (composite
-- PK, CASCADE both ways) — an agency dealing with multiple companies, and a
-- company reached through multiple agencies, are both many-to-many by
-- construction here, the same as a contact moving between companies.

CREATE TABLE message_company (
    message_id  uuid NOT NULL REFERENCES message (id) ON DELETE CASCADE,
    company_id  uuid NOT NULL REFERENCES company (id) ON DELETE CASCADE,
    -- What this company was to this message: the recruiting agency that
    -- sent/sourced it, or (reserved for future use) an employer link
    -- recorded outside the primary column. Only 'agency' is written today.
    role        text NOT NULL CHECK (role IN ('employer', 'agency')),
    created_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (message_id, company_id)
);

CREATE INDEX message_company_company_idx ON message_company (company_id);
