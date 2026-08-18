# MindGraph 升级计划（Roadmap / Upgrade Plan）

> **状态说明**：Phase 5 企业接入能力已落地。下表标注的 `Done` 条目已实现并通过回归测试，
> `Planned` / `Candidate` 条目尚未实施。对外声称能力时以本表状态为准。

## 规划条目一览

| ID | 升级项 | 优先级 | 预计版本 | 状态 | 说明 |
|----|--------|--------|----------|------|------|
| UG-001 | 鲁棒文档 ingestion（layout-aware） | **P2** | **v2.2** | **Done** | PDF / 扫描件 layout-aware 解析，OCR fallback 与表格感知输出，已回归验证 |
| UG-002 | Query 理解层 | P2 | v2.2（候选） | **Done** | 轻量确定性查询改写 / 简单分解，提升长问与复合问检索命中，已回归验证 |
| UG-003 | 多租户 / 权限过滤 | P3 | v3.0 | **Done** | workspace / department / ACL 行级隔离，检索前裁剪（Phase 5 已实现） |
| UG-004 | 企业连接器（本地目录） | P3 | v3.1 | **Done** | Markdown 目录增量同步 + ACL 继承 + connector_syncs 审计 |
| UG-005 | MCP 只读工具 + 审计 | P3 | v3.1 | **Done** | stdio + HTTP 双传输，ACL 校验，access_audit 审计 |
| UG-006 | SSO / OIDC | P3 | v3.1 | **Done** | Bearer JWT 校验 + claims → principal 映射，与 API Key 并存 |

---

## 已完成条目说明

### UG-003：多租户 / 权限过滤（Done）

**实现**：
- `notes` 表新增 `workspace` / `department` / `acl_json` / `acl_public` 列；
- 检索管线 `_filter_by_access` 在候选融合后、最终返回前按 ACL 裁剪；
- 台账列表、单条详情、关系列表、评测概览均按当前主体 `access_scope` 裁剪；
- `access_audit` 表记录每次访问的 actor / action / resource / decision / reason。

**验证**：`tests/test_access_control.py`（11 项全绿），覆盖 workspace/department 命中、public 覆盖、deny 拦截、越权 404 + 审计写入。

### UG-004：企业连接器（Done）

**实现**：
- `DirectoryConnectorService` 支持本地 Markdown 目录增量同步；
- 新增 / 修改 / 删除检测基于 `content_hash`；
- workspace / department 推断优先级：frontmatter > 请求参数 > 目录结构 > 根 `acl.json`；
- 同步结果写入 `connector_syncs` 表（含 file_count / added / updated / pruned / error）；
- API：`POST /api/v1/connectors/directories`。

**验证**：`tests/test_directory_connector.py`（4 项全绿）。

### UG-005：MCP 只读工具 + 审计（Done）

**实现**：
- `src/mcp_server.py`：自包含 JSON-RPC 2.0，无外部 SDK 依赖；
- 工具：`mindgraph_list_notes` / `mindgraph_get_note` / `mindgraph_search` / `mindgraph_evaluation_overview` / `mindgraph_list_relations`；
- stdio 传输（开发者本地）+ HTTP 传输（`/api/v1/mcp`）；
- 所有工具按当前主体 ACL 裁剪，调用写入 `access_audit`。

**验证**：`tests/test_mcp.py`（4 项全绿）。

### UG-006：SSO / OIDC（Done）

**实现**：
- `src/api/oidc.py`：Bearer JWT 校验（RS256/HS256），JWKS 自动发现与缓存；
- claims → principal 映射：`workspaces` / `departments` / `roles` 自动转成 ACL scope；
- 与 API Key 并存：Bearer 优先 OIDC，未命中回退 API Key；
- 未配置 `OIDC_ENABLED=true` 时完全不影响现有认证流程。

**验证**：`tests/test_oidc.py`（5 项全绿）。

---

## 变更说明

UG-001 和 UG-002 已完成，并通过 `tests/test_document_intelligence.py` 与 `tests/test_adaptive_router.py` 以及全量回归验证。当前文档仅保留升级计划的历史记录，不再把这两个条目标记为待办。

后续如果要继续扩展，可在此补充新的升级候选项。
