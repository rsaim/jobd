ALTER TABLE sender_rule DROP CONSTRAINT sender_rule_verdict_check;
ALTER TABLE sender_rule ADD CONSTRAINT sender_rule_verdict_check
    CHECK (verdict IN ('positive', 'negative'));

ALTER TABLE sender_category DROP CONSTRAINT sender_category_verdict_check;
ALTER TABLE sender_category ADD CONSTRAINT sender_category_verdict_check
    CHECK (verdict IN ('positive', 'negative'));
