-- 0011 — local cache of the raw object, to stop paying an S3 round trip for
-- every header re-read (sender/thread/label scans, fanout investigation).
--
-- S3 stays the source of truth (I3) — this is a cache, not a replacement:
-- droppable and re-populatable from the bucket at any time, never the only
-- copy of anything. `raw_payload` mirrors RawMessage.payload exactly;
-- `raw_metadata` mirrors RawMessage.metadata (thread_id, labels,
-- occurred_at) — together they reconstruct the same RawMessage a
-- storage.get() would have returned, with no S3 call.

ALTER TABLE message
    ADD COLUMN raw_payload  bytea,
    ADD COLUMN raw_metadata jsonb;
