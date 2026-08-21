-- Same provenance argument as 0024, for aliases the audit's renames write.
-- The original set is preserved verbatim; 'audit' is strictly additive.
ALTER TABLE company_alias DROP CONSTRAINT company_alias_source_check;
ALTER TABLE company_alias ADD CONSTRAINT company_alias_source_check
    CHECK (source IN ('llm', 'manual', 'domain', 'import', 'audit'));
