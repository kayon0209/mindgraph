# Retrieval package conventions

- `types.py` defines shared protocols, candidates, traces, and configuration-neutral data contracts.
- `embeddings.py`, `dense.py`, `sparse.py`, `fusion.py`, and `reranker.py` each own one retrieval stage.
- `pipeline.py` composes stages; retrieval strategies must not duplicate the full pipeline.
- `indexing.py` owns versioned full-index rebuilds and corpus snapshots.
- Stable chunk IDs use `<doc_name>::<chunk_index>` and must match the frozen evaluation labels.
- Formal evaluation must never silently use Hash Embedding.
