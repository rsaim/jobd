-- Offline conversations: the calls that leave no mail.
--
-- A phone screen, a coffee chat, a verbal offer over the phone -- these move
-- a search forward and leave nothing in the mailbox to derive from. Without
-- somewhere to put them the record silently under-counts: a company whose
-- whole process ran over the phone looks like it never happened.
--
-- This is NOT a message. `message` is write-once storage for what a mail
-- provider actually handed us (hence storage_key, external_id, raw_payload
-- NOT NULL, and a channel CHECK of email/linkedin). A human's recollection of
-- a call has no envelope and no provider, so forcing it in there would mean
-- fabricating those fields and corrupting the "derived view of raw mail"
-- invariant. It gets its own table, with the human as the stated source.
CREATE TABLE IF NOT EXISTS conversation (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id uuid NOT NULL REFERENCES company (id) ON DELETE CASCADE,
    application_id uuid REFERENCES application (id) ON DELETE SET NULL,
    occurred_at timestamptz NOT NULL,
    kind text NOT NULL CHECK (
        kind = ANY (ARRAY['phone', 'video', 'in_person', 'other'])
    ),
    counterpart text,
    notes text NOT NULL DEFAULT '',
    -- The stage this conversation establishes, if any. Nullable on purpose:
    -- most chats are just context, and only some carry a verdict worth
    -- putting in the funnel.
    stage text CHECK (
        stage IS NULL OR stage = ANY (ARRAY[
            'applied', 'recruiter_screen', 'phone_screen', 'technical',
            'onsite', 'offer', 'rejected', 'accepted', 'declined', 'withdrawn'
        ])
    ),
    -- The stage_event this conversation produced, so editing or deleting the
    -- conversation can carry its stage with it instead of orphaning a row
    -- nothing points at.
    stage_event_id uuid REFERENCES stage_event (id) ON DELETE SET NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS conversation_company_idx
    ON conversation (company_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS conversation_application_idx
    ON conversation (application_id) WHERE application_id IS NOT NULL;

-- Evidence that is not a message.
--
-- `stage_event.evidence_message_id` was NOT NULL to enforce a real invariant:
-- every stage must point at the thing that proves it, because a stage nobody
-- can trace is indistinguishable from a guess. That invariant is right, and
-- this keeps it -- it only stops assuming the proof is always an *email*.
--
-- A verbal offer has a witness rather than a message. So the column becomes
-- nullable and a CHECK takes over the guarantee: a row may omit the message
-- only when it is a human's own entry (`extracted_by='manual'`), which is
-- traceable to the conversation row that carries the who, the when and the
-- notes. Machine-extracted rows are unaffected -- `llm` and `timeline` still
-- cannot exist without a message to point at.
ALTER TABLE stage_event ALTER COLUMN evidence_message_id DROP NOT NULL;

ALTER TABLE stage_event DROP CONSTRAINT IF EXISTS stage_event_evidence_present;
ALTER TABLE stage_event ADD CONSTRAINT stage_event_evidence_present CHECK (
    evidence_message_id IS NOT NULL OR extracted_by = 'manual'
);

-- The uniqueness rule that stops a re-run duplicating a stage (I2) is indexed
-- on evidence_message_id, and NULLs do not collide in a unique index -- so
-- manual rows need their own guard or the same call could be recorded twice.
CREATE UNIQUE INDEX IF NOT EXISTS stage_event_manual_key
    ON stage_event (application_id, stage, occurred_at)
    WHERE evidence_message_id IS NULL;
