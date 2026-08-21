DELETE FROM sender_rule WHERE source = 'distilled';
ALTER TABLE sender_rule DROP CONSTRAINT sender_rule_source_check;
ALTER TABLE sender_rule ADD CONSTRAINT sender_rule_source_check
    CHECK (source = ANY (ARRAY['human'::text, 'auto'::text, 'audit'::text]));
