# MindGraph Threat Model

## Protected assets

- Policy text and source paths
- Version/lifecycle metadata
- Workspace, department, and document ACLs
- Retrieval traces and audit records
- Provider credentials and generated indexes

## Main threats and controls

| Threat | Control |
|---|---|
| Unauthorized retrieval | ACL filtering in dense/sparse recall, fusion, rerank, final context, and graph expansion |
| Metadata leakage | Out-of-scope note requests return not-found semantics; relation endpoints filter both endpoints |
| Prompt injection in documents | Documents are evidence only; tools do not grant document or permission mutation capabilities |
| Proposed relation treated as truth | Only confirmed, evidence-backed, valid edges are traversed |
| Version confusion | Effective-date and lifecycle filters plus pre-generation conflict detection |
| MCP denial of service | Request size, rate limits, batch limits, bounded top-k, and execution timeout |
| Audit privacy leakage | MCP search audit records metadata and counts, not plaintext query content by default |
| Cross-source deletion | Connector notes carry source ownership; pruning is scoped to the connector source |

## Operational rules

Never commit real Vault content, API keys, generated indexes, tokens, or personal data. Security tests must run with synthetic fixtures and must verify both allow and deny paths.
