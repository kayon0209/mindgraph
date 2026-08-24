# MindGraph 升级计划（Roadmap / Upgrade Plan）

> **状态说明**：2026-08-24 安全审查发现 Phase 5 仍有未闭环项。`Partial` 表示代码入口已存在，
> 但尚未满足完整验收条件；只有端到端行为、迁移和 CI 均通过后才能改为 `Done`。

## 规划条目一览

| ID | 升级项 | 优先级 | 预计版本 | 状态 | 说明 |
|----|--------|--------|----------|------|------|
| UG-001 | 鲁棒文档 ingestion（layout-aware） | **P2** | **v2.2** | **Partial** | layout/table 提取存在；扫描页仅检测 `ocr_required`，尚无 OCR 执行与可检索验收 |
| UG-002 | Query 理解层 | P2 | v2.2（候选） | **Partial** | 可生成 query variants；当前只检索第一个 variant，尚无多查询召回与合并 |
| UG-003 | 多租户 / 权限过滤 | P3 | v3.0 | **Partial** | 认证已 fail-closed、匿名仅 public；ACL 历史数据回填和检索层 prefilter 尚未完成 |
| UG-004 | 企业连接器（本地目录） | P3 | v3.1 | **Partial** | 已封堵跨源 prune/源文件改写/越界路径；source ownership 与外部内容索引需迁移后完成 |
| UG-005 | MCP 只读工具 + 审计 | P3 | v3.1 | **Partial** | HTTP 已统一强制认证与 ACL；async 阻塞和隐私审计策略仍待修复 |
| UG-006 | SSO / OIDC | P3 | v3.1 | **Partial** | OIDC 与 API Key 已统一进入 mandatory principal；discovery/JWKS 缓存仍待真实 IdP 验收 |

---

## 已完成条目说明

### UG-003：多租户 / 权限过滤（Partial）

**实现**：
- `notes` 表新增 `workspace` / `department` / `acl_json` / `acl_public` 列；
- 检索管线 `_filter_by_access` 在候选融合后、最终返回前按 ACL 裁剪；
- 台账列表、单条详情、关系列表、评测概览均按当前主体 `access_scope` 裁剪；
- `access_audit` 表记录每次访问的 actor / action / resource / decision / reason。
- 企业认证模式下缺失、无效或失败的凭据返回 401，不再降级为匿名；
- `AUTH_MODE=demo` 的匿名主体使用 public-only scope，`AUTH_MODE=off` 是唯一显式 ACL bypass。

**剩余工作**：历史 notes ACL backfill；将 ACL 从融合后的 Python 过滤下推到检索候选生成阶段。

### UG-004：企业连接器（Partial）

**实现**：
- `DirectoryConnectorService` 支持本地 Markdown 目录增量同步；
- 新增 / 修改检测基于 `content_hash`；删除同步暂时关闭，避免无 source ownership 时跨源误删；
- workspace / department 推断优先级：frontmatter > 请求参数 > 目录结构 > 根 `acl.json`；
- 同步结果写入 `connector_syncs` 表（含 file_count / added / updated / pruned / error）；
- API：`POST /api/v1/connectors/directories`。
- 同步只允许 `CONNECTOR_ALLOWED_ROOTS` 或项目 `knowledge/` 下的 canonical path；
- 连接器不再向源 Markdown 注入 ID，不同 source 通过稳定 connector 前缀临时隔离同名相对路径。

**剩余工作**：为 notes 增加正式 source ownership，并实现 source-aware prune 与外部内容受控快照/索引。

### UG-005：MCP 只读工具 + 审计（Partial）

**实现**：
- `src/mcp_server.py`：自包含 JSON-RPC 2.0，无外部 SDK 依赖；
- 工具：`mindgraph_list_notes` / `mindgraph_get_note` / `mindgraph_search` / `mindgraph_evaluation_overview` / `mindgraph_list_relations`；
- stdio 传输（开发者本地）+ HTTP 传输（`/api/v1/mcp`）；
- 所有工具按当前主体 ACL 裁剪，调用写入 `access_audit`。

**剩余工作**：移除 async handler 内的同步检索阻塞，并让问题文本审计服从统一隐私配置。

### UG-006：SSO / OIDC（Partial）

**实现**：
- `src/api/oidc.py`：Bearer JWT 校验（RS256/HS256），JWKS 自动发现与缓存；
- claims → principal 映射：`workspaces` / `departments` / `roles` 自动转成 ACL scope；
- 与 API Key 并存：OIDC 使用 Bearer；API Key fallback 必须显式使用 `X-API-Key`；
- 未配置 `OIDC_ENABLED=true` 时完全不影响现有认证流程。

**剩余工作**：使用 discovery 返回的 `jwks_uri`、跨请求缓存 JWK client，并对真实企业 IdP 做端到端验收。

---

## 变更说明

2026-08-24 第一批 P0 修复已完成认证 fail-closed、API 全局认证边界、连接器 allowed roots、
非破坏性扫描和跨 connector 临时路径隔离。OCR、复合查询、多租户数据迁移、source-aware 删除、
OIDC discovery、MCP 异步化等仍是明确的验收缺口，修复前不得对外标记为 Done。
