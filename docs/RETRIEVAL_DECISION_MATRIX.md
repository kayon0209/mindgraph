# MindGraph Retrieval Decision Matrix

| Query shape | Default route | Rerank | Graph | Safety rule |
|---|---|---:|---:|---|
| Exact fact / simple factual question | Hybrid | No | No | Use active, date-valid evidence only |
| Explicit document title | BM25 | No | No | Preserve title and version terms |
| Versioned policy question | Hybrid | No | No | Filter effective date before answer generation |
| Exception / conflict | Hybrid | Conditional | Conditional | Graph is enabled only by explicit request or a passing gate |
| Cross-policy comparison | Hybrid | Conditional | Conditional | Every expanded edge must be confirmed and evidence-backed |
| Ambiguous / compound question | Hybrid | No | No | Clarify or decompose; never invent missing conditions |
| ACL-restricted question | Hybrid | No | No | Apply ACL during retrieval and graph expansion |

## Current release boundary

Graph retrieval remains an experimental, explicitly controlled path. The current public Golden set is too small to establish production GraphRAG gains. The default request and router therefore keep Graph disabled unless a caller explicitly enables it and the relevant service policy permits it.

All production-facing answers must retain source path, chunk ID, version/lifecycle metadata, and access scope in the trace. Proposed or rejected relations never enter retrieval.
