# MindGraph AI · 架构与落地计划

> 当前权威文档。版本：MindGraph v1.0（由 Expense RAG QA 重构演进）

## 1. 产品定位

MindGraph AI 是**本地优先的 AI 个人知识 OS**：把你的 Obsidian Vault 变成一个可对话、可关联、可溯源的知识网络。对标 Obsidian 的 AI 层（不重造编辑器），而非又一个笔记应用。

### 版本叙事

| 版本 | 能力 | 状态 |
|------|------|------|
| V1 | 报销制度单库问答 RAG（Expense RAG QA） | 已演进 |
| V2 | 多来源个人知识检索：混合检索 + 版本化索引 | 已演进 |
| **V3** | **自动关联 + Graph RAG + 知识激活（MindGraph）** | **当前** |

## 2. 方案选型（方案 A）

**Obsidian Vault + 本地 FastAPI 服务 + 双前端（Obsidian 插件 / Web Demo）**

选型理由：

- 不重造编辑器，复用 Obsidian 成熟的笔记体验与本地优先心智
- 本地优先 = 隐私可控，适合个人知识这种敏感数据
- 双前端兼顾「真实日常使用」（Obsidian 插件）与「面试 / 演示」（Web Demo），避免纯插件难演示的问题

## 3. 系统架构

```
离线：Obsidian Vault
   → VaultSyncService 递归扫描 .md + 注入 mindgraph_id（Frontmatter 稳定 ID）
   → content_hash 去重 → notes 表（index_status 状态机）
   → MindGraphIndexService：BGE 嵌入 + FAISS(Dense) + BM25(Sparse) + RRF 融合
   → 版本化索引 mg-<ts>-<uuid>，CURRENT 指针原子切换（失败不影响线上）

在线：用户问题
   → 范围检查 → 向量化 → 融合检索(RRF) → [重排]
   → MindGraphRetrievalPipeline：命中笔记沿 note_relations 一跳图谱扩展补充证据
   → 拼装 Prompt（系统指令 + 引用证据 + graph_links）
   → LLM 流式生成（SSE）→ 结论 + [citation-N] + 关系面板
```

## 4. 数据模型

- **notes**(`note_id` PK, `vault_path` UNIQUE, `title`, `content_hash`, `frontmatter_json`,
  `ai_access_level`, `chunk_count`, `index_status`, `index_version`, 时间戳)
  - 稳定 ID：Frontmatter 注入 `mindgraph_id`；`vault_path` 作跨重命名 / 移动溯源键
- **note_relations**(`relation_id` PK, `source_note_id` / `target_note_id` FK, `relation_type`,
  `direction`, `status`∈{proposed,confirmed}, `evidence_chunk_id`, `confidence`,
  `model_version`, `prompt_version`, `proposed_at`, `resolved_at`, `resolved_by`)
  - 冲突检测：若某 proposed 关系反向已存在 confirmed 对，标记为 `conflict=true`
- 真实元数据库：`data/product/product.sqlite3`（非 `mindgraph.db`，后者为无用残留已归档）

## 5. 关键技术决策

| 决策 | 选择 | 理由 |
|------|------|------|
| 存储 | MVP 复用 SQLite + FAISS + BM25 + 新增 `note_relations` | 已是可用 hybrid retriever，避免 pgvector 的 Docker 运维负担 |
| 嵌入 | BAAI/bge-small-zh-v1.5，本地缓存离线推理 | 大陆 Windows 用 `snapshot_download(local_dir, local_dir_use_symlinks=False)` 落本地目录最稳 |
| 关系 | 规则 + 轻量 LLM 抽候选（proposed）→ 用户确认 → confirmed | P0 不建独立实体表，先跑通闭环 |
| 前端 | Web Demo 用 React+Vite+纯 CSS 变量 | 弃 Tailwind v4/postcss 冲突，100% 离线可靠 |
| 插件 | Obsidian 插件用纯 JS（无需构建即可加载） | 降低发布与演示门槛 |

## 6. 落地计划与验收清单

| 模块 | 状态 | 说明 |
|------|------|------|
| Vault 扫描 + 稳定 ID 注入 | ✅ 完成 | `VaultSyncService.scan_vault` |
| 增量索引 + CURRENT 原子切换 | ✅ 完成 | `MindGraphIndexService` + `MindGraphSyncWatcher` |
| Graph RAG 可信问答（SSE） | ✅ 完成 | `mindgraph_pipeline` + `/api/v1/mindgraph/chat` |
| 只读 API（notes / 评测 / 关系） | ✅ 完成 | `mindgraph_readonly.py` |
| 真实数据闭环（378 笔记 / 3019 chunk） | ✅ 完成 | 真实 Vault 跑通，BGE 本地化 |
| 可复现评测（三组消融） | ✅ 完成 | `run_ablation.py` → `evaluation_runs` |
| 关系 seed（图谱 / 链接建议有内容） | ✅ 完成 | `seed_relations.py` → 23 条 |
| Web Demo（4 页去 mock） | ✅ 完成 | `expense-rag-frontend/`（独立前端仓库） |
| **Obsidian 插件（双前端①）** | ✅ 完成 | `obsidian-plugin/`（纯 JS 可加载，调 `/chat/stream`） |
| **关系抽取自动化（闭环最后一环）** | ✅ 完成 | `relation_extraction_service.py` + `POST /api/v1/mindgraph/relations/extract` + `scripts/extract_relations.py` |

## 7. 已知限制

- SQLite 并发写有上限，高并发需迁 Postgres
- 关系抽取默认走「离线 BGE 语义相似度」候选（规则 + 语义，无需 LLM）；`use_llm=True` 可选由已配置 Chat Provider 精炼关系类型与依据（需可用 Provider）
- 自动抽取写入 `proposed` 仅供用户确认；确认后（confirmed）才进入 Graph RAG 一跳扩展检索路径（Human-in-the-loop）
- 抽取为 append-only 逐步扩充：候选池（约 1500 对 @0.6 阈值）耗尽后自然不再新增，非严格单次幂等
- 问答语义检索依赖本地 BGE；生成依赖已配置的 LLM Provider
- 评测为小样本 Golden Set，代表工程可复现，非生产效果结论
- 根目录 `.streamlit-run*.log` 为旧报销 RAG 残留日志，被历史进程占用无法删除，不影响 MindGraph 运行

## 8. 复现命令

```bash
# 索引构建（真实 Vault）
python scripts/sync_vault.py --vault "D:\ObsidianVault"

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
```
