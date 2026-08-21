-- 0010 — a category can be pinned UNDECIDED, not just positive/negative.
--
-- Real need: LinkedIn's reply/InMail addresses (hit-reply@, inmail-hit-
-- reply@...) often carry the same bulk-mail headers / Gmail social label as
-- LinkedIn's own noise — which would otherwise make prefilter.score silently
-- resolve them NEGATIVE via the bank/bulk-header tier, exactly the wrong
-- answer for a real recruiter's reply. Tagging the group UNDECIDED is an
-- explicit override: "we looked, this group needs the LLM to read the
-- content" — protected from the generic bulk-mail heuristics below it, but
-- still yielding to a stronger *proven* per-message signal above it
-- (known_contact, thread_positive, an ATS-domain hit) — see prefilter.score's
-- rewritten cascade.

ALTER TABLE sender_category DROP CONSTRAINT sender_category_verdict_check;
ALTER TABLE sender_category ADD CONSTRAINT sender_category_verdict_check
    CHECK (verdict IN ('positive', 'negative', 'undecided'));

ALTER TABLE sender_rule DROP CONSTRAINT sender_rule_verdict_check;
ALTER TABLE sender_rule ADD CONSTRAINT sender_rule_verdict_check
    CHECK (verdict IN ('positive', 'negative', 'undecided'));
