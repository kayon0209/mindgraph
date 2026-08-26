# MCP Permission Matrix

| Tool | Read | ACL required | Writes | Audit |
|---|---:|---:|---:|---:|
| `mindgraph_search` | Yes | Yes | No | Yes, query content omitted by default |
| `mindgraph_get_note` | Yes | Yes | No | Yes |
| `mindgraph_list_relations` | Yes | Both endpoints | No | Yes |
| `mindgraph_evaluation_overview` | Yes | Visible aggregate only | No | Yes |

MCP uses the same application services and access scope as HTTP API routes. It does not expose relation confirmation, document publishing, ACL mutation, or other write operations. Limits apply to `top_k`, list sizes, batch size, request body size, rate, and execution time.
