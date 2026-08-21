-- The recorded-approval trail I1 requires ("every outbound action needs
-- recorded human approval — no configuration flag relaxes this",
-- SECURITY.md §4). One row per draft, whether or not it is ever sent: a
-- draft nobody approved is still worth being able to see, same reasoning
-- review_queue keeps a resolved-elsewhere row visible rather than deleting
-- it.
--
-- Not a cache (unlike summary_cache, migration 0018) - this is the audit
-- itself, the thing I1 requires exist, so it must never be truncatable by
-- `jobd rebuild`. That is exactly why `in_reply_to_message_id` below is a
-- plain uuid with NO foreign key to `message`: `message` is one of
-- rebuild.DERIVED_TABLES, and `TRUNCATE message CASCADE` truncates every
-- table with a foreign key to it regardless of the FK's ON DELETE action -
-- Postgres ignores ON DELETE entirely under TRUNCATE CASCADE (the exact bug
-- docs/chat-and-summaries.md §0 documents for sender_rule/
-- company_verification/sender_category). A hard FK here would let a rebuild
-- silently erase the record of every email actually sent - the one thing
-- this table exists to make undeletable. A soft reference, nulled at read
-- time when it no longer resolves, is the correct trade: the sent record
-- survives; the link to which inbound message prompted it may not.
CREATE TABLE outbound_message (
    id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    channel                text NOT NULL,
    account                text NOT NULL,
    in_reply_to_message_id uuid,
    recipients             text[] NOT NULL,
    subject                text NOT NULL,
    body                   text NOT NULL,
    -- The channel's own draft id (e.g. a Gmail draft id) - what `send()`
    -- is called with.
    draft_id               text NOT NULL,
    status                 text NOT NULL DEFAULT 'drafted'
                           CHECK (status IN ('drafted', 'sent')),
    -- Both null until a human's click on the Send endpoint fills them in -
    -- that fill is the recorded approval itself, not a flag saying one
    -- happened. Single-operator app, so `approved_by` is the mailbox's own
    -- address (whoever is running jobd), not a multi-user identity.
    approved_by            text,
    approved_at            timestamptz,
    sent_message_id        text,
    created_at             timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX outbound_message_reply_idx
    ON outbound_message (in_reply_to_message_id);
