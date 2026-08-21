-- Reverting requires no 'declined' rows exist, same as any CHECK narrowing.
-- If this fails, the caller has 'declined' data to resolve first — that is
-- the correct failure, not something to paper over here.

ALTER TABLE stage_event DROP CONSTRAINT stage_event_stage_check;
ALTER TABLE stage_event ADD CONSTRAINT stage_event_stage_check
    CHECK (stage IN (
        'applied', 'recruiter_screen', 'phone_screen',
        'technical', 'onsite', 'offer', 'rejected',
        'withdrawn', 'accepted'));

ALTER TABLE application DROP CONSTRAINT application_outcome_check;
ALTER TABLE application ADD CONSTRAINT application_outcome_check
    CHECK (outcome IN ('offer', 'accepted', 'rejected', 'withdrawn'));
