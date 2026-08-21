-- Reverts to CASCADE/NOT NULL. Only succeeds if no row currently has a NULL
-- company_id — the expected state right after 0015, before any company was
-- ever deleted through the SET NULL path this migration adds.

ALTER TABLE company_verification
    DROP CONSTRAINT company_verification_company_id_fkey,
    ADD CONSTRAINT company_verification_company_id_fkey
        FOREIGN KEY (company_id) REFERENCES company (id) ON DELETE CASCADE,
    ALTER COLUMN company_id SET NOT NULL;
