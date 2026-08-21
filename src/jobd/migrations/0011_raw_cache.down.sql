ALTER TABLE message
    DROP COLUMN IF EXISTS raw_payload,
    DROP COLUMN IF EXISTS raw_metadata;
