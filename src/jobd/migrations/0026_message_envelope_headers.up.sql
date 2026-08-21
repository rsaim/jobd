-- The envelope facts the raw store held that Postgres never did. Everything
-- here is re-derived from raw mail (the backfill and the ingest writer both
-- fill it), so dropping and re-adding is always safe — I3's "re-derive the
-- world" applies to these columns exactly as it does to classification.
--
-- First-class columns for what code queries; two jsonb catch-alls for the
-- rest: `raw_metadata` (the source sidecar: Gmail labels, occurred_at,
-- thread_id) already existed and was never filled, `raw_headers` (selected
-- RFC 5322 headers) is new. The queryable columns:
--   reply_to          the real correspondent behind a platform sender
--   cc_addresses      same shape as recipient_addresses
--   message_id_header RFC Message-ID — cross-source threading identity
--   in_reply_to       RFC threading, the fallback when a source has no
--                     native thread id
--   references_ids    the full References chain, same purpose
--   is_bulk           List-Unsubscribe / Precedence bulk|list, the marker
--                     prefilter's _is_bulk read from raw on every classify —
--                     stored, DB-only passes (the review sweep) can see it
ALTER TABLE message
    ADD COLUMN reply_to text,
    ADD COLUMN cc_addresses text[],
    ADD COLUMN message_id_header text,
    ADD COLUMN in_reply_to text,
    ADD COLUMN references_ids text[],
    ADD COLUMN is_bulk boolean,
    ADD COLUMN raw_headers jsonb;

-- RFC-threading lookups: "which message did this reply to" and "who replied
-- to this message" both key on Message-ID values.
CREATE INDEX message_message_id_header_idx
    ON message (message_id_header) WHERE message_id_header IS NOT NULL;
CREATE INDEX message_in_reply_to_idx
    ON message (in_reply_to) WHERE in_reply_to IS NOT NULL;
