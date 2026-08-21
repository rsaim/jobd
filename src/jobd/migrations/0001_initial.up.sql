-- 0001 — the record (PRD P3).
--
-- Company (+aliases) -> Application -> StageEvent -> Message -> Contact.
-- Postgres is the *derived* working store; S3 holds the source of truth, so
-- everything here is reconstructible by `jobd rebuild` (I3). That is why no
-- table carries user-authored data that exists nowhere else.
--
-- Conventions, chosen once and applied throughout:
--   * uuid primary keys from gen_random_uuid() (built in since PG13).
--   * timestamptz everywhere. A job search crosses timezones; naive timestamps
--     would silently reorder a timeline.
--   * TEXT + CHECK instead of native ENUM. Adding a value to a native enum is
--     a migration that cannot be run inside a transaction with other DDL, and
--     removing one is worse. A CHECK is boring and reversible.

CREATE EXTENSION IF NOT EXISTS vector;

-- ---------------------------------------------------------------- companies

CREATE TABLE company (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    canonical_name  text        NOT NULL,
    -- Primary web domain, when known. The strongest entity-resolution signal
    -- available: two "Acme" mails from acme.test are the same Acme.
    domain          text,
    first_seen_at   timestamptz NOT NULL,
    last_seen_at    timestamptz NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT company_name_not_blank CHECK (length(btrim(canonical_name)) > 0),
    CONSTRAINT company_seen_ordered   CHECK (last_seen_at >= first_seen_at)
);

-- Domains are unique when present. Partial index rather than a UNIQUE column,
-- because many companies are discovered from a signature block with no domain
-- at all and NULLs must not collide.
CREATE UNIQUE INDEX company_domain_key ON company (lower(domain))
    WHERE domain IS NOT NULL;

CREATE TABLE company_alias (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id  uuid NOT NULL REFERENCES company (id) ON DELETE CASCADE,
    alias       text NOT NULL,
    -- Where the alias came from. `manual` beats `llm` when they disagree, and
    -- keeping the provenance is what lets a re-derive discard only the
    -- machine-made ones (I3).
    source      text NOT NULL CHECK (source IN ('llm', 'manual', 'domain', 'import')),
    created_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT company_alias_not_blank CHECK (length(btrim(alias)) > 0)
);

CREATE UNIQUE INDEX company_alias_key ON company_alias (lower(alias));
CREATE INDEX company_alias_company_idx ON company_alias (company_id);

-- ----------------------------------------------------------------- contacts

CREATE TABLE contact (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    display_name  text,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now()
);

-- One row per address/handle a person is known by. This table *is* the
-- cross-channel merge (G1): the same recruiter mailing two of the user's
-- addresses, then messaging on LinkedIn, is three identities and one contact.
CREATE TABLE contact_identity (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    contact_id  uuid NOT NULL REFERENCES contact (id) ON DELETE CASCADE,
    channel     text NOT NULL CHECK (channel IN ('email', 'linkedin')),
    identifier  text NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT contact_identity_not_blank CHECK (length(btrim(identifier)) > 0)
);

-- An identifier belongs to exactly one contact. A merge rewrites the pointer;
-- it never leaves the same address on two people.
CREATE UNIQUE INDEX contact_identity_key
    ON contact_identity (channel, lower(identifier));
CREATE INDEX contact_identity_contact_idx ON contact_identity (contact_id);

-- People move. The association is per-company and time-bounded rather than a
-- column on contact, so a recruiter who pinged you from three firms (PRD §1)
-- keeps all three relationships instead of overwriting them.
CREATE TABLE contact_company (
    contact_id  uuid NOT NULL REFERENCES contact (id) ON DELETE CASCADE,
    company_id  uuid NOT NULL REFERENCES company (id) ON DELETE CASCADE,
    role_title  text,
    first_seen_at timestamptz NOT NULL,
    last_seen_at  timestamptz NOT NULL,
    PRIMARY KEY (contact_id, company_id),
    CONSTRAINT contact_company_seen_ordered CHECK (last_seen_at >= first_seen_at)
);

CREATE INDEX contact_company_company_idx ON contact_company (company_id);

-- ------------------------------------------------------------- applications

-- Unbounded per company, across years (P3). Note what is deliberately absent:
-- there is NO unique constraint on (company_id, role_title). Applying to the
-- same role at the same company in 2021 and again in 2024 is two applications,
-- and a uniqueness constraint here would merge them into one corrupted
-- timeline. M3 gate 3 exists to keep this decision from being "tidied up".
CREATE TABLE application (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id   uuid NOT NULL REFERENCES company (id) ON DELETE CASCADE,
    role_title   text,
    started_at   timestamptz NOT NULL,
    ended_at     timestamptz,
    -- Terminal state, or NULL while live. `ghosted` is NOT here: see below.
    outcome      text CHECK (outcome IN ('offer', 'accepted', 'rejected', 'withdrawn')),
    source       text NOT NULL DEFAULT 'inferred'
                 CHECK (source IN ('inferred', 'manual', 'import')),
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT application_ended_after_start CHECK (ended_at IS NULL OR ended_at >= started_at)
);

CREATE INDEX application_company_idx ON application (company_id, started_at DESC);

-- ----------------------------------------------------------------- messages

CREATE TABLE message (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    -- The content-hash key in raw storage. Unique, and the reason ingestion is
    -- idempotent at the record layer too: a second ingest of the same message
    -- collides here instead of inserting a duplicate row (I2).
    storage_key   text NOT NULL,
    channel       text NOT NULL CHECK (channel IN ('email', 'linkedin')),
    account       text NOT NULL,
    external_id   text,
    direction     text NOT NULL CHECK (direction IN ('inbound', 'outbound')),
    sent_at       timestamptz NOT NULL,
    subject       text,
    -- Plaintext extracted for search. The authoritative bytes stay in S3; this
    -- column is derived and may be re-extracted at any time.
    body_text     text NOT NULL DEFAULT '',
    company_id     uuid REFERENCES company (id) ON DELETE SET NULL,
    application_id uuid REFERENCES application (id) ON DELETE SET NULL,
    contact_id     uuid REFERENCES contact (id) ON DELETE SET NULL,
    -- Nullable, and populated in M5. Dimension is fixed at the column, so
    -- switching to an embedder with a different width is a migration — see
    -- docs/build-guide.md M3 and the note in docs/schema.md.
    embedding      vector(1536),
    created_at     timestamptz NOT NULL DEFAULT now(),
    -- Generated, not trigger-maintained: it cannot drift from the text it
    -- indexes, and there is no ordering hazard on bulk insert.
    search_tsv     tsvector GENERATED ALWAYS AS (
                       to_tsvector('english',
                           coalesce(subject, '') || ' ' || coalesce(body_text, ''))
                   ) STORED
);

CREATE UNIQUE INDEX message_storage_key ON message (storage_key);
CREATE INDEX message_search_idx  ON message USING gin (search_tsv);
CREATE INDEX message_company_idx ON message (company_id, sent_at DESC);
CREATE INDEX message_application_idx ON message (application_id, sent_at DESC);
CREATE INDEX message_contact_idx ON message (contact_id, sent_at DESC);
-- Whose-turn-is-it (P4.1) is "latest message per company, and which way it
-- went", so direction rides along in the index that answers it.
CREATE INDEX message_company_turn_idx ON message (company_id, sent_at DESC, direction);

-- ------------------------------------------------------------- stage events

-- Every stage claim points at the message that evidences it. NOT NULL, not by
-- convention: G2 promises "each claim linked to its evidence message", and a
-- nullable column would make that promise optional in practice.
--
-- `ghosted` is absent from this list on purpose, and this is the resolution of
-- the PRD §10 open question. Ghosting is the *absence* of an event — nobody
-- ever sends the message that means "we have stopped replying to you" — and an
-- absence cannot be evidence-linked, so it cannot be a row here. It is derived
-- at query time from last-touch recency plus direction, the same two facts that
-- drive whose-turn-is-it (P4.1).
CREATE TABLE stage_event (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    application_id      uuid NOT NULL REFERENCES application (id) ON DELETE CASCADE,
    stage               text NOT NULL CHECK (stage IN (
                            'applied', 'recruiter_screen', 'phone_screen',
                            'technical', 'onsite', 'offer', 'rejected',
                            'withdrawn', 'accepted')),
    occurred_at         timestamptz NOT NULL,
    evidence_message_id uuid NOT NULL REFERENCES message (id) ON DELETE RESTRICT,
    -- 0..1 from the extractor. Sub-threshold extractions never reach this
    -- table; they go to the M5 review queue instead.
    confidence          numeric(4, 3) CHECK (confidence >= 0 AND confidence <= 1),
    extracted_by        text NOT NULL DEFAULT 'manual',
    created_at          timestamptz NOT NULL DEFAULT now()
);

-- The same evidence must not produce the same stage twice on one application —
-- that is what a re-run of the extractor would otherwise do (I2).
CREATE UNIQUE INDEX stage_event_evidence_key
    ON stage_event (application_id, stage, evidence_message_id);
CREATE INDEX stage_event_application_idx ON stage_event (application_id, occurred_at);
