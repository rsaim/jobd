-- 0009 — named sender categories.
--
-- sender_rule (0008) ties one verdict to one address/domain. Real mail has
-- groups: LinkedIn alone sends job-match digests, InMail replies, social
-- notifications, and security alerts from different addresses that should
-- not all carry the same verdict, but *within* one group they always should.
-- sender_category is the group's verdict, set once; sender_rule.category
-- tags which senders belong to it, so re-deciding a whole group later is one
-- UPDATE instead of re-teaching every address in it.

CREATE TABLE sender_category (
    category    text PRIMARY KEY,
    verdict     text NOT NULL CHECK (verdict IN ('positive', 'negative')),
    company_id  uuid REFERENCES company (id) ON DELETE SET NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE sender_rule
    ADD COLUMN category text REFERENCES sender_category (category) ON DELETE SET NULL,
    ALTER COLUMN verdict DROP NOT NULL,
    ADD CONSTRAINT sender_rule_verdict_or_category
        CHECK (verdict IS NOT NULL OR category IS NOT NULL);
