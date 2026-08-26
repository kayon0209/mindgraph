# MindGraph Typed Graph Schema and Governance

## Scope

The current graph is a bounded evidence-extension graph, not a general-purpose knowledge graph. Only `confirmed` edges may participate in retrieval, and the default route keeps graph expansion disabled until a stratified ablation passes.

## Allowed edge types

- `APPLIES_TO`
- `REQUIRES_APPROVAL`
- `HAS_LIMIT`
- `EXCEPTION_TO`
- `SUPERSEDES`
- `CONTRADICTS`
- `related_to`, `references`, `elaborates` for legacy/demo compatibility

Each edge carries source and target note IDs, relation type, status, evidence chunk/span, document version and effective interval, extraction method, confidence, proposal time, and confirmation audit fields. An edge without evidence is not eligible for retrieval.

## Lifecycle

1. Rules or an LLM may create `proposed` candidates only.
2. A reviewer must inspect the evidence and confirm or reject the candidate.
3. Confirmed edges are filtered by lifecycle and both endpoint ACLs during traversal.
4. When a document version changes, affected edges must be revalidated or expired.
5. Rejected candidates remain auditable and must not be silently re-proposed as confirmed facts.

## Limits

Traversal is limited to two hops maximum and a bounded number of expanded chunks. Graph failures, missing evidence, ACL denial, and stale versions must fall back to the non-graph Hybrid path.
