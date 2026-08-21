-- 0014 — provenance on a learned rule.
--
-- Every sender_rule row has looked the same regardless of how it got here:
-- a human's `jobd learn`, or _record's online-learning auto-teach (M5,
-- 2026-08-13). Without a marker, "rules learned" on the home dashboard
-- would be an undifferentiated count, and nobody could later find and
-- review/promote the auto-taught ones. Existing rows default 'human' —
-- an honest approximation, not a reconstruction: this table records
-- decisions, not derived mail content (I3 does not promise it back), so
-- rules auto-taught before this column existed cannot be told apart from
-- ones a human typed.

ALTER TABLE sender_rule
    ADD COLUMN source text NOT NULL DEFAULT 'human'
        CHECK (source IN ('human', 'auto'));
