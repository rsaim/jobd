-- 'distilled' joins the rule provenances: rules compiled by `jobd distill`
-- from the model's own past judgments (aggregates only, no content re-read).
ALTER TABLE sender_rule DROP CONSTRAINT sender_rule_source_check;
ALTER TABLE sender_rule ADD CONSTRAINT sender_rule_source_check
    CHECK (source = ANY (ARRAY['human'::text, 'auto'::text, 'audit'::text,
                               'distilled'::text]));
