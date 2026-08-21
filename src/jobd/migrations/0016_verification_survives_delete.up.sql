-- 0016 — company_verification must outlive the company it audited.
--
-- 0015 gave company_id ON DELETE CASCADE, which was wrong for the one case
-- this table exists to record: a company confirmed not job-related gets its
-- messages labelled negative and the row deleted (`jobd verify --apply`,
-- `services/verify.py`) — and CASCADE would delete the very audit entry that
-- explains why, along with it. SET NULL keeps the row (and its self-
-- contained verified_name/verified_domain/reasoning/raw_response columns,
-- which already capture enough to be legible with no company row behind
-- them at all).

ALTER TABLE company_verification
    ALTER COLUMN company_id DROP NOT NULL,
    DROP CONSTRAINT company_verification_company_id_fkey,
    ADD CONSTRAINT company_verification_company_id_fkey
        FOREIGN KEY (company_id) REFERENCES company (id) ON DELETE SET NULL;
