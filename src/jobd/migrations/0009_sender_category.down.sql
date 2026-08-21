ALTER TABLE sender_rule
    DROP CONSTRAINT IF EXISTS sender_rule_verdict_or_category,
    ALTER COLUMN verdict SET NOT NULL,
    DROP COLUMN IF EXISTS category;

DROP TABLE IF EXISTS sender_category;
