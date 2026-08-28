# ADR-002: Conditional Graph Routing

## Decision

Confirmed, evidence-backed graph edges are available as a bounded retrieval extension, but Graph is not the default path. It may be explicitly enabled for suitable queries and may become a default route only after stratified ablation demonstrates material gain without citation, ACL, or latency regressions.

## Rationale

The current graph is a one/two-hop note relation layer, not a fully typed policy assertion graph. Similarity-generated candidates are useful for review but are not sufficient business facts. The current Golden set is also too small to support a general GraphRAG claim.

## Consequences

Hybrid retrieval remains the safe default. Graph experiments must record dataset version, index, configuration, latency, and failure cases, including no-gain results.

## Gate-to-config flow (Phase 5)

The publish gate is explicit and manual, in three steps:

1. Run the stratified ablation. `evaluation/ablation_runner.py` reports per-category deltas and `evaluate_graph_gate` evaluates the thresholds: Recall@5 gain ≥ +5pp, mean latency ≤ 3× baseline, no ACL leakage.
2. Human decision. The gate produces a recommendation (`conditional_only` / `keep_graph_disabled`); it never flips routing by itself.
3. Write the conclusion back to configuration. On a positive human decision, set `GRAPH_DEFAULT_ENABLED=true` in settings (environment or `.env`). `ChatService` consumes this flag with OR semantics (`request.graph_enabled or flag`): once enabled, graph-eligible routes use graph by default server-wide, and rolling back means setting the flag to `false`. There is deliberately no per-request opt-out — keep the flag `false` if per-request opt-in is required.
