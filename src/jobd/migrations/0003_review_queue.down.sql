DROP TABLE IF EXISTS review_queue;

ALTER TABLE message
    DROP COLUMN IF EXISTS classified_at,
    DROP COLUMN IF EXISTS classified_by;
