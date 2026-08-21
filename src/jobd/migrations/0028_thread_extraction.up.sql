-- One model reading per thread. The latest email in a thread quotes the
-- whole history, so one call on it classifies every member — this table is
-- both the cache that stops any second call for a thread the model has
-- already read, and the surface the Classifications page renders.
CREATE TABLE thread_extraction (
    id uuid PRIMARY KEY,
    thread_id text NOT NULL UNIQUE,
    latest_message_id uuid REFERENCES message(id) ON DELETE SET NULL,
    -- sent_at of the message actually read; a later arrival is new evidence
    -- and re-extracts (upsert), anything at or before it reuses the row.
    covers_sent_at timestamptz,
    model text NOT NULL,
    label text NOT NULL,
    company_name text,
    company_domain text,
    company_kind text,
    agency_name text,
    agency_domain text,
    role_title text,
    stage text,
    relationship text,
    client_companies jsonb NOT NULL DEFAULT '[]'::jsonb,
    payload jsonb NOT NULL,
    messages_covered int NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX thread_extraction_recent_idx ON thread_extraction (updated_at DESC);
CREATE INDEX thread_extraction_label_idx ON thread_extraction (label);
