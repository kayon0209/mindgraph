<div align="center">

# MindGraph

### Local-first evidence intelligence for Markdown and AI agents

**Find the right source. Respect versions and permissions. Stop when evidence conflicts. Answer with citations.**

<p>
  <a href="./README.md">English</a> · <a href="./README.zh-CN.md">简体中文</a>
</p>

<p>
  <a href="https://github.com/kayon0209/mindgraph/actions/workflows/ci-cd.yml"><img src="https://img.shields.io/github/actions/workflow/status/kayon0209/mindgraph/ci-cd.yml?branch=main&style=flat-square&label=CI" alt="CI"></a>
  <a href="https://github.com/kayon0209/mindgraph/stargazers"><img src="https://img.shields.io/github/stars/kayon0209/mindgraph?style=flat-square&logo=github" alt="GitHub stars"></a>
  <img src="https://img.shields.io/badge/Python-3.11+-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/MCP-ready-7C3AED?style=flat-square" alt="MCP ready">
  <img src="https://img.shields.io/badge/local--first-yes-0F766E?style=flat-square" alt="Local first">
  <a href="./LICENSE"><img src="https://img.shields.io/badge/license-MIT-22C55E?style=flat-square" alt="MIT License"></a>
</p>

<p>
  <a href="#quickstart">Quickstart</a> ·
  <a href="#why-mindgraph">Why MindGraph</a> ·
  <a href="#mcp-for-ai-agents">MCP</a> ·
  <a href="#how-it-works">Architecture</a> ·
  <a href="#evaluation-and-boundaries">Evaluation</a> ·
  <a href="./docs/PRODUCT_STRATEGY.md">Roadmap</a>
</p>

<img src="assets/hero-banner.jpg" alt="MindGraph" width="100%">

</div>

MindGraph turns a Markdown or Obsidian vault into a local evidence layer for people and AI agents. It combines hybrid retrieval with version, lifecycle, access-control, conflict and citation checks—so an answer is generated only when the evidence is fit to support it.

The first vertical is policy-heavy knowledge such as expense, finance and compliance. The same evidence pipeline can support any Markdown knowledge base where freshness, permissions and traceability matter.

## The failure MindGraph is built for

**Question:** Which expense deadline applies on 2026-08-18: 30 days or 60 days?

| Generic RAG | MindGraph |
|---|---|
| May retrieve the semantically similar but archived 60-day policy | Filters evidence by status and effective date |
| May silently mix two active versions | Returns `conflicting_evidence` before calling the LLM |
| Produces a fluent answer with unclear provenance | Returns the source, version, date and retrieval trace |

> **MindGraph's product principle: govern the evidence before generating the answer.**

## Why MindGraph

<table>
<tr>
<td width="25%" align="center"><b>Local-first</b><br><sub>SQLite and local indexes<br>Keep knowledge under your control</sub></td>
<td width="25%" align="center"><b>Evidence-first</b><br><sub>Citations and retrieval traces<br>Return to the original source</sub></td>
<td width="25%" align="center"><b>Version-aware</b><br><sub>Lifecycle and effective dates<br>Stop on conflicting evidence</sub></td>
<td width="25%" align="center"><b>Agent-ready</b><br><sub>REST, SSE and MCP<br>Use from agent workflows</sub></td>
</tr>
</table>

- **Hybrid retrieval:** BGE / FAISS dense search + BM25 sparse search + RRF fusion
- **Adaptive routing:** selects an appropriate retrieval strategy from query intent
- **Controlled graph expansion:** only human-confirmed relations can add evidence
- **Grounded answers:** streaming responses with citations and retrieval traces
- **Policy lifecycle:** stable `policy_key`, version, status and effective-date filtering
- **Conflict-before-generation:** conflicting active versions stop the LLM call
- **Governed access:** API key / OIDC, workspace / department ACLs and audit logs
- **Evaluation ledger:** retrieval, answer trust, latency and cost in one history
- **Web and Obsidian clients:** ask, inspect evidence, review relations and compare runs

## Quickstart

### Option A: verify the pipeline without keys

The public `demo-vault/` contains synthetic policies, workflows and cases. This command verifies sync, indexing, hybrid retrieval, confirmed-relation expansion and ablation in a temporary directory.

```bash
git clone https://github.com/kayon0209/mindgraph.git
cd mindgraph

python -m venv .venv
source .venv/bin/activate              # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python scripts/validate_mindgraph_offline.py
```

The offline check uses deterministic fake embeddings and a fake LLM. It proves that the engineering path is reproducible; it does not claim real-model quality.

### Option B: start the Web workspace

```bash
cp .env.example .env                   # Windows: Copy-Item .env.example .env
# Configure a model provider in .env when you want real answers.
docker compose up --build
```

| Service | URL |
|---|---|
| Web workspace | <http://127.0.0.1:3000> |
| API | <http://127.0.0.1:8000> |
| OpenAPI docs | <http://127.0.0.1:8000/api/docs> |

## How it works

```mermaid
flowchart LR
    A[Markdown / Obsidian] --> B[Parse, clean and govern]
    B --> C[(SQLite WAL)]
    B --> D[Versioned FAISS index]
    Q[Person / AI agent] --> R[Adaptive retrieval router]
    C --> R
    D --> R
    R --> F[Dense + Sparse + RRF]
    F --> G{Evidence conflict?}
    G -- Yes --> H[Stop and surface versions]
    G -- No --> I[Confirmed relation expansion]
    I --> J[LLM generation]
    J --> K[Answer + Citation + Trace]
```

Status, effective-date, category and permission filters are shared by base retrieval and relation expansion. This prevents an archived document from re-entering a current answer through a graph edge.

<div align="center">
  <img src="assets/architecture.svg" alt="MindGraph architecture" width="92%">
</div>

## ACL backfill operations

Historical notes can be given repeatable ACL metadata through a deliberately operator-run workflow. It never runs automatically against production data.

The command uses the configured `DATABASE_PATH`. The target database must already be initialized at the current application schema; this CLI never performs schema migration. Its default dry-run opens SQLite read-only and does not write audit rows or note metadata.

```bash
# 1. Preview aggregate counts only. Review unresolved/private records before proceeding.
python scripts/backfill_note_acl.py --dry-run

# 2. After an operator review, write the backfill and retain the returned run ID.
python scripts/backfill_note_acl.py --apply

# 3. Roll back only that completed run ID when needed.
python scripts/backfill_note_acl.py --rollback RUN_ID
```

The CLI prints aggregate counts and a run ID only; it does not print note bodies, paths or ACL contents. Operation failures return a non-zero exit code with a fixed, redacted JSON error on stderr; argument errors use standard `argparse` usage output. Unresolved source ownership is intentionally backfilled as private and must be reviewed by an operator before any live production change.

## MCP for AI agents

MindGraph exposes a read-only MCP server with five tools:

| Tool | Purpose |
|---|---|
| `mindgraph_list_notes` | List notes visible to the current principal |
| `mindgraph_get_note` | Read one visible note and its governance metadata |
| `mindgraph_search` | Search knowledge and return evidence |
| `mindgraph_list_relations` | List confirmed relations whose endpoints are both visible |
| `mindgraph_evaluation_overview` | Inspect evaluation-run summaries |

Start the stdio server:

```bash
PYTHONPATH=src MCP_PRINCIPAL=local-user python -m mcp_server
```

Claude Desktop–style configuration:

```json
{
  "mcpServers": {
    "mindgraph": {
      "command": "python",
      "args": ["-m", "mcp_server"],
      "env": {
        "PYTHONPATH": "/absolute/path/to/mindgraph/src",
        "MCP_PRINCIPAL": "local-user"
      }
    }
  }
}
```

MCP is intentionally read-only today. Reviewed relation proposals, evidence feedback and evaluation-case write-back are on the roadmap.

## Evaluation and boundaries

```bash
python scripts/run_ablation.py
python scripts/run_routing_evaluation.py
python scripts/run_answer_evaluation.py --live --strategy hybrid
```

The current frozen set contains 12 hand-written policy cases covering replacement, thresholds, exceptions, cross-policy questions, no-answer cases and ambiguity. It scores citation F1, refusal correctness, version validity, required facts, forbidden facts, latency, tokens and estimated cost.

### What MindGraph is today

- A local-first Hybrid RAG system with controlled, human-confirmed relation expansion
- Version-aware, citation-first and MCP-ready
- Reproducible with a public synthetic vault and deterministic offline checks

### What it is not yet

- A complete entity-disambiguation and multi-hop knowledge-graph engine
- Proven for production by a large benchmark—the current 12 cases are regression tests
- A hosted enterprise SaaS

## Project status

| Now | Next | Later |
|---|---|---|
| Hybrid retrieval and adaptive routing | Structure-aware chunk inspection | Typed policy edges |
| Version-conflict interception | 100+ layered evaluation cases | Entity-event dual graph |
| ACL, OIDC and audit | Evidence feedback and writable MCP proposals | Community discovery |
| Web, Obsidian and read-only MCP | Stronger Ruff, mypy and coverage gates | Multi-hop reasoning |

See [`docs/PRODUCT_STRATEGY.md`](docs/PRODUCT_STRATEGY.md) for the product boundary and roadmap.

## Documentation

- [Product strategy and roadmap](docs/PRODUCT_STRATEGY.md)
- [Architecture](docs/MindGraph-ARCH.md)
- [Deployment](docs/DEPLOYMENT.md)
- [Retrieval cost and efficiency](docs/MindGraph-cost-efficiency.md)
- [Obsidian plugin](obsidian-plugin/README.md)

## Contributing

Issues, reproducible evaluation cases, documentation improvements and small focused pull requests are welcome. Especially useful contributions include real-but-shareable policy cases, MCP client examples, Obsidian workflows and retrieval or access-control boundary tests.

<div align="center">

If evidence-first local knowledge systems are useful to you, consider giving MindGraph a star.

[Report an issue](https://github.com/kayon0209/mindgraph/issues) · [Read the roadmap](docs/PRODUCT_STRATEGY.md) · [MIT License](LICENSE)

</div>
