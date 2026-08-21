-- 0012 — companies vs. agencies.
--
-- An agency is not a separate concept from a company (same canonical_name/
-- domain/first_seen/last_seen semantics) — it is a company that never
-- employs the user, only introduces them to one. `kind` is the discriminator;
-- when a recruiting-agency sender names no specific client, the agency's own
-- company row (kind='agency') becomes the entity a message resolves to,
-- rather than leaving the message unresolved in review_queue forever.

ALTER TABLE company
    ADD COLUMN kind text NOT NULL DEFAULT 'employer'
        CHECK (kind IN ('employer', 'agency'));
