-- Semantic search over message.embedding (tools/registry.py's
-- search_communications, mode="semantic") — an HNSW index over cosine
-- distance, the operator the `<=>` queries below use. HNSW over IVFFlat
-- because it needs no training/list-count tuning and builds correctly
-- incrementally as rows are embedded after the index already exists
-- (IVFFlat's cluster centers are fixed at CREATE INDEX time, so a mostly-
-- empty table at creation gives it a bad partition for everything embedded
-- afterward — this column starts at zero populated rows). pgvector 0.8.6
-- confirmed installed (migration 0001), which has supported HNSW since 0.5.0.
--
-- NULL embeddings (unembedded messages — most of them; see
-- services/classify.py's backfill_embeddings docstring) are simply absent
-- from the index, same as any other nullable indexed column.
CREATE INDEX message_embedding_hnsw_idx ON message
    USING hnsw (embedding vector_cosine_ops);
