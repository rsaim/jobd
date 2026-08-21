ALTER TABLE company_alias DROP CONSTRAINT company_alias_source_check;
ALTER TABLE company_alias ADD CONSTRAINT company_alias_source_check
    CHECK (source IN ('llm', 'manual', 'domain', 'import'));
