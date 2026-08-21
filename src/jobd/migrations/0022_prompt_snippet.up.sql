-- A small, human-curated library of reusable instruction text — tones and
-- anything else worth saving once and reusing everywhere a free-text
-- "extra instructions" field feeds a model call (today: the reply box's
-- generate/regenerate; the shape is generic on purpose so a later surface
-- can reuse it without a new table).
--
-- `label` is unique so seeding here (and any later re-seed) can be
-- idempotent via ON CONFLICT rather than needing a separate "did we already
-- insert the defaults" check.
CREATE TABLE prompt_snippet (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    label      text NOT NULL UNIQUE,
    text       text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

INSERT INTO prompt_snippet (label, text) VALUES
    ('Warm & friendly', 'Write in a warm, friendly, approachable tone.'),
    ('Formal & professional', 'Write in a formal, professional, polished tone.'),
    ('Direct & concise', 'Be direct and concise — get to the point in as few words as possible.'),
    ('Enthusiastic', 'Write with genuine enthusiasm and energy about the opportunity.'),
    ('Firm but polite', 'Be firm and clear about what you need, while staying polite and respectful.')
ON CONFLICT (label) DO NOTHING;
