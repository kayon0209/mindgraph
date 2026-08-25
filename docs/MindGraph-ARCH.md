# MindGraph · 架构与落地计划

> 当前实现架构。产品边界与后续阶段以 `docs/PRODUCT_STRATEGY.md` 为准。

## 1. 产品定位

MindGraph 是**本地优先的企业制度与决策依据知识服务**。首个垂直场景聚焦报销、财务与制度合规，目标是提供带来源、版本和证据链的可审计回答。

Obsidian Vault 是当前已实现的数据源和客户端之一，不是产品边界。后续企业接入将以连接器、权限继承和审计为前提。

### 版本叙事

| 版本 | 能力 | 状态 |
|------|------|------|
| V1 | 报销制度单库问答 RAG（历史项目，已归档至 `archive/legacy-rag/`） | 已演进 |
| V2 | 多来源个人知识检索：混合检索 + 版本化索引 | 已演进 |
| **V3** | **Hybrid RAG + 人工确认关系扩展（MindGraph 当前基线）** | **当前** |
| V4 | 企业制度断言图 + 版本/权限/审计 | 规划 |

## 2. 方案选型（方案 A）

**Markdown/Vault + 本地 FastAPI 服务 + 可替换客户端**

选型理由：

- 不重造编辑器，Obsidian 继续作为当前本地工作流客户端
- 本地优先降低企业敏感制度资料的外发风险
- FastAPI 保持客户端无关，后续可接 Web、MCP 与企业协作工具

## 3. 系统架构

```
离线：Markdown / Obsidian Vault
   → VaultSyncService 递归扫描 .md + 注入 mindgraph_id（Frontmatter 稳定 ID）
   → content_hash 去重 → notes 表（index_status 状态机）
   → MindGraphIndexService：BGE 嵌入 + FAISS(Dense) + BM25(Sparse) + RRF 融合
   → 版本化索引 mg-<ts>-<uuid>，CURRENT 指针原子切换（失败不影响线上）

在线：用户问题
   → 范围检查 → 向量化 → 融合检索(RRF) → [重排]
   → MindGraphRetrievalPipeline：命中笔记沿 confirmed note_relations 一跳扩展补充证据
   → PolicyConflictService：按 policy_key + 查询日期检查有效版本唯一性；冲突则拒绝生成并转人工
   → 拼装 Prompt（系统指令 + 引用证据 + graph_links）
   → LLM 流式生成（SSE）→ 结论 + [citation-N] + 关系面板
```

## 4. 数据模型

- **notes**(`note_id` PK, `vault_path` UNIQUE, `title`, `content_hash`, `frontmatter_json`,
  `ai_access_level`, `policy_key`, `owner`, `document_version`, `effective_from/to`, `policy_status`,
  `metadata_issues_json`, `chunk_count`, `index_status`, `index_version`, 时间戳)
  - 稳定 ID：Frontmatter 注入 `mindgraph_id`；`vault_path` 作跨重命名 / 移动溯源键
  - 治理字段由 Vault Frontmatter 规范化；缺失/非法字段记录为 issue，不静默伪造默认业务含义
  - `policy_key` 是跨版本稳定的制度族标识；schema v5 使用兼容 `ALTER TABLE ADD COLUMN` 原位升级，并建立生命周期联合索引，保留既有笔记数据
- **note_relations**(`relation_id` PK, `source_note_id` / `target_note_id` FK, `relation_type`,
  `direction`, `status`∈{proposed,confirmed}, `evidence_chunk_id`, `confidence`,
  `model_version`, `prompt_version`, `proposed_at`, `resolved_at`, `resolved_by`)
  - 关系候选冲突：若某 proposed 关系反向已存在 confirmed 对，标记为 `conflict=true`；这与制度有效版本冲突是两个不同概念
- 真实元数据库：`data/product/product.sqlite3`（非 `mindgraph.db`，后者为无用残留已归档）

## 5. 关键技术决策

| 决策 | 选择 | 理由 |
|------|------|------|
| 存储 | MVP 复用 SQLite + FAISS + BM25 + 新增 `note_relations` | 已是可用 hybrid retriever，避免 pgvector 的 Docker 运维负担 |
| 嵌入 | BAAI/bge-small-zh-v1.5，本地缓存离线推理 | 大陆 Windows 用 `snapshot_download(local_dir, local_dir_use_symlinks=False)` 落本地目录最稳 |
| 关系 | 相似度/规则召回候选（proposed）→ 可选 LLM 精炼 → 用户确认 → confirmed | 当前属于受控相关笔记扩展，不等同于完整知识图谱 |
| 前端 | React + TypeScript + Vite + 纯 CSS 变量，与后端同仓 | 同仓构建、测试和 Compose 交付；避免为 MVP 引入额外样式工具链 |
| 插件 | Obsidian 插件用纯 JS（无需构建即可加载） | 降低发布与演示门槛 |

## 6. 落地计划与验收清单

| 模块 | 状态 | 说明 |
|------|------|------|
| Vault 扫描 + 稳定 ID 注入 | ✅ 完成 | `VaultSyncService.scan_vault` |
| 增量索引 + CURRENT 原子切换 | ✅ 完成 | `MindGraphIndexService` + `MindGraphSyncWatcher` |
| Graph RAG 可信问答（SSE） | ✅ 完成 | `mindgraph_pipeline` + `/api/v1/mindgraph/chat` |
| 只读 API（notes / 评测 / 关系） | ✅ 完成 | `mindgraph_readonly.py` |
| 公开样例闭环 | ✅ 完成 | `demo-vault/` 提供 12 篇企业制度样例，无需自备 Vault 即可复现 |
| 可复现评测（三组消融） | ✅ 完成 | `run_ablation.py` → `evaluation_runs` |
| 独立 Golden Set | ✅ 完成 | 12 个手工标注问题，不从数据库 confirmed 关系反向派生 |
| 关系 seed（图谱 / 链接建议有内容） | ✅ 完成 | `seed_relations.py` 为演示数据生成 proposed 关系 |
| Web 控制台（4 页、无 mock） | ✅ 完成 | `web/`；问答 SSE、知识库、评测指标和关系审核均连接真实 API |
| 制度元数据治理 | ✅ 第一批完成 | owner/version/effective dates/status 入库、质量标记、API 过滤、Web 台账与 citation 透传 |
| 制度版本冲突保护 | ✅ 第一批完成 | policy_key 入库；查询日期存在多个有效版本时在模型前安全拒答，SSE/Web 展示全部冲突依据 |
| 生命周期检索约束 | ✅ 第一批完成 | policy 字段映射到检索过滤元数据；关系扩展复用状态、生效期与分类过滤，不能重新引入历史制度 |
| **Obsidian 插件（双前端①）** | ✅ 完成 | `obsidian-plugin/`（纯 JS 可加载，调 `/chat/stream`） |
| **关系抽取自动化（闭环最后一环）** | ✅ 完成 | `relation_extraction_service.py` + `POST /api/v1/mindgraph/relations/extract` + `scripts/extract_relations.py` |

## 7. 已知限制

- SQLite 并发写有上限，高并发需迁 Postgres
- 关系抽取默认走「离线 BGE 语义相似度」候选（规则 + 语义，无需 LLM）；`use_llm=True` 可选由已配置 Chat Provider 精炼关系类型与依据（需可用 Provider）
- 自动抽取写入 `proposed` 仅供用户确认；确认后（confirmed）才进入 Graph RAG 一跳扩展检索路径（Human-in-the-loop）
- 抽取为 append-only 逐步扩充：候选池（约 1500 对 @0.6 阈值）耗尽后自然不再新增，非严格单次幂等
- 问答语义检索依赖本地 BGE；生成依赖已配置的 LLM Provider
- 评测为小样本 Golden Set，代表工程可复现，非生产效果结论
- 当前独立 Golden Set 只有 12 个问题，可验证回归与三组消融，但不足以证明生产场景的普遍效果
- `demo-vault/` 是合成制度样例，用于零配置体验和回归；企业落地仍需接入自身文档与权限体系
- 企业 ACL 继承（目录 `acl.json`）、SSO/OIDC（Bearer JWT + JWKS）与审计日志（`access_audit`）已实现；剩余边界是 SSO/OIDC 企业验收（UG-006 Partial）与审计完整覆盖，不应宣称已覆盖全部企业审计场景
- 根目录 `.streamlit-run*.log` 为旧报销 RAG 残留日志，被历史进程占用无法删除，不影响 MindGraph 运行

## 8. 复现命令

```bash
# 零配置离线演示（同步公开样例、构建索引并验证关系扩展开关）
python scripts/validate_mindgraph_offline.py

# 或索引自有 Vault
python scripts/sync_vault.py --vault "D:\path\to\your-vault"

# 关系 seed（让图谱 / 链接建议页有内容）
python scripts/seed_relations.py

# 关系自动抽取（离线 BGE 语义相似度 → 写入 proposed 候选）
python scripts/extract_relations.py --threshold 0.62 --top-k 4 --max 200
#   可选：用 LLM 精炼候选（需配置 Chat Provider）
python scripts/extract_relations.py --use-llm
#   或经 API 触发：POST /api/v1/mindgraph/relations/extract {"threshold":0.62,"top_k":4,"max_candidates":200}

# 三组消融评测
python scripts/run_ablation.py

# 启动后端
uvicorn api.main:app --app-dir src --host 127.0.0.1 --port 8000

# 启动 Web 开发服务器（另一个终端）
cd web
corepack enable
pnpm install --frozen-lockfile
pnpm dev

# 或一条命令启动 Web + API
docker compose up --build
```
