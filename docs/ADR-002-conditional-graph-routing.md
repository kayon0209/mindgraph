# ADR-002: Conditional Graph Routing

## Decision

Confirmed, evidence-backed graph edges are available as a bounded retrieval extension, but Graph is not the default path. It may be explicitly enabled for suitable queries and may become a default route only after stratified ablation demonstrates material gain without citation, ACL, or latency regressions.

## Rationale

The current graph is a one/two-hop note relation layer, not a fully typed policy assertion graph. Similarity-generated candidates are useful for review but are not sufficient business facts. The current Golden set is also too small to support a general GraphRAG claim.

## Consequences

Hybrid retrieval remains the safe default. Graph experiments must record dataset version, index, configuration, latency, and failure cases, including no-gain results.
