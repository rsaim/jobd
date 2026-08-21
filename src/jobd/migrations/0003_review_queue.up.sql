-- 0003 — the human review queue (PRD P2), and the classification watermark.
--
-- P2: "Extractions below a confidence threshold route to a human review queue
-- rather than into state." The queue is therefore *outside* the record. A
-- pending item has changed nothing about any company, application, or stage —
-- that is the whole point, and it is why the extraction sits here as JSON
-- rather than as half-written rows somebody has to unpick later.

CREATE TABLE review_queue (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    message_id   uuid NOT NULL REFERENCES message (id) ON DELETE CASCADE,
    -- The model's output, verbatim. Kept whole rather than split into columns:
    -- a rejected extraction is evidence about the *extractor*, and normalising
    -- it would lose exactly the fields that explain why it was wrong.
    extraction   jsonb NOT NULL,
    confidence   numeric(4, 3) CHECK (confidence >= 0 AND confidence <= 1),
    reason       text NOT NULL,
    status       text NOT NULL DEFAULT 'pending'
                 CHECK (status IN ('pending', 'approved', 'rejected')),
    extracted_by text NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    resolved_at  timestamptz,
    CONSTRAINT review_resolved_iff_decided CHECK (
        (status = 'pending' AND resolved_at IS NULL)
        OR (status <> 'pending' AND resolved_at IS NOT NULL)
    )
);

-- One open item per message. A re-run of the classifier must update the
-- pending item rather than stack a second copy of the same doubt (I2).
CREATE UNIQUE INDEX review_queue_open_key ON review_queue (message_id)
    WHERE status = 'pending';
CREATE INDEX review_queue_pending_idx ON review_queue (created_at)
    WHERE status = 'pending';

-- When this message was last classified, and by which model. NULL means never.
--
-- This is the watermark that makes re-classification incremental and makes
-- `jobd rebuild --reclassify` meaningful: bump the model, clear the column,
-- re-derive. Storing the model name alongside is what lets you tell which rows
-- came from which extractor when the numbers change (I3).
ALTER TABLE message
    ADD COLUMN classified_at   timestamptz,
    ADD COLUMN classified_by   text;

CREATE INDEX message_unclassified_idx ON message (sent_at)
    WHERE classified_at IS NULL;
