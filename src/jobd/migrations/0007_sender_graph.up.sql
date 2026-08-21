-- 0007 — sender/recipient columns on message (M5 graph-propagation learning).
--
-- thread_id (0006) only answers "same conversation". The correspondent graph
-- the user actually wants — "this recruiter's address, anywhere in the
-- mailbox, in or out of this thread" — needs the address to be a queryable
-- column, not something re-derived by fetching every raw object from S3 on
-- every lookup. This is the SQL-side-filtering rule (P4.1) applied one
-- milestone early, because bulk rule propagation is exactly the same shape of
-- problem the dashboard's filters are.
--
-- Three columns rather than one because "from" and "to/cc" answer different
-- questions: sender_domain/sender_address is who *sent* this message (the
-- recruiter's own outbound mail), recipient_addresses is who the user's own
-- outbound replies were *sent to* (the other half of "involving the same
-- people" — a learned rule must catch both directions of the conversation).

ALTER TABLE message
    ADD COLUMN sender_address      text,
    ADD COLUMN sender_domain       text,
    ADD COLUMN recipient_addresses text[] NOT NULL DEFAULT '{}';

CREATE INDEX message_sender_address_idx
    ON message (lower(sender_address)) WHERE sender_address IS NOT NULL;
CREATE INDEX message_sender_domain_idx
    ON message (sender_domain) WHERE sender_domain IS NOT NULL;
CREATE INDEX message_recipient_addresses_idx
    ON message USING gin (recipient_addresses);
