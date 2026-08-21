-- Cache, not a record: droppable and regenerable, the same posture
-- summary_cache (0018) and raw_payload (0011) take. Nothing in the record
-- depends on a logo existing, and dropping this table costs one CLI run.
--
-- The bytes live here rather than in the bucket or on disk because they are
-- small (a favicon is 1-15 KB), because they are per-company and the record
-- is already per-company, and because a browser must never fetch them from
-- the third party directly: doing so would hand every domain in the user's
-- job search to a logo CDN on every page view, which is exactly the promise
-- the footer makes ("nothing leaves this machine"). One CLI run reaches the
-- network; the dashboard then serves what that run captured.
CREATE TABLE company_logo (
    company_id    uuid PRIMARY KEY REFERENCES company (id) ON DELETE CASCADE,
    -- NULL is a real answer, not a missing one: "we looked and there is
    -- nothing", which is what keeps a re-run from asking the network about
    -- the same 300 domains that have no logo. `source` says which it is.
    image         bytea,
    content_type  text,
    -- Where it came from, so a bad provider's results can be deleted by
    -- source alone, and `none` can be told apart from a real hit.
    source        text NOT NULL CHECK (source IN ('duckduckgo', 'iconhorse', 'manual', 'none')),
    -- The domain the fetch actually used. A company's domain can change
    -- under a rename, and this is what says the cached bytes are stale.
    domain        text,
    fetched_at    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX company_logo_found_idx ON company_logo (company_id)
    WHERE image IS NOT NULL;
