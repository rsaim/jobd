-- 0017 — an offer the candidate turned down is not the same event as one the
-- company withdrew.
--
-- `outcome`/`stage_event.stage` conflated two very different endings under
-- 'withdrawn': the candidate pulling out of a process before any decision,
-- and the candidate declining an offer already in hand. The second is a
-- signal worth telling apart on the dashboard (it means the process
-- succeeded — an offer was extended) and is definitely not the same as
-- 'rejected' (the company said no). 'declined' names it.

ALTER TABLE application DROP CONSTRAINT application_outcome_check;
ALTER TABLE application ADD CONSTRAINT application_outcome_check
    CHECK (outcome IN ('offer', 'accepted', 'rejected', 'withdrawn', 'declined'));

ALTER TABLE stage_event DROP CONSTRAINT stage_event_stage_check;
ALTER TABLE stage_event ADD CONSTRAINT stage_event_stage_check
    CHECK (stage IN (
        'applied', 'recruiter_screen', 'phone_screen',
        'technical', 'onsite', 'offer', 'rejected',
        'withdrawn', 'accepted', 'declined'));
