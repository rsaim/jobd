-- The LLM audit teaches corrective rules; its provenance is neither a human
-- decision nor classify's auto-teach, and pretending it is either would lie
-- to anyone later asking "who decided this sender is negative?".
ALTER TABLE sender_rule DROP CONSTRAINT sender_rule_source_check;
ALTER TABLE sender_rule ADD CONSTRAINT sender_rule_source_check
    CHECK (source IN ('human', 'auto', 'audit'));
