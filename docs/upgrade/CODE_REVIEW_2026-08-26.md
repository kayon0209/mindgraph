# MindGraph 升级全面代码审查（2026-08-26）

> 审查对象：`D:\demo\mindgraph`（分支 `main`，基线 commit `9180917`）
> 对照文件：`MindGraph_Cursor升级执行计划.md`
> 审查范围：当前工作区未提交改动（40+ 修改文件 + 20+ 新增文件）
> 实证检查：`pytest`（282 passed / 2 skipped）、`ruff` 致命门禁（All checks passed）、`pip check`（No broken requirements）

---

## 0. 总体结论

升级的**工程骨架与核心能力已落地**，代码自洽、测试全绿、无依赖冲突、数据库迁移非破坏性、MCP 鉴权链闭合。但**存在 1 个明确的计划验收缺口（Phase 1 golden 仅 12 条，未达 30 条门槛）** 与 **2 个需修复的技术风险（图扩展无 fail-safe 兜底、关系生命周期未过滤）**，以及若干一致性/诚信偏差。

优先级速览：

| 级别 | 项 | 位置 |
|---|---|---|
| 🔴 Blocker | 图扩展层无错误兜底，图服务异常会炸掉整条检索 | `src/retrieval/mindgraph_pipeline.py:59` |
| ⚠️ 计划缺口 | Phase 1 golden 仅 12 条，未达 30 条进入下一 Phase 门槛 | `evaluation/datasets/mindgraph_golden_v2.jsonl` |
| 🟡 风险 | 关系自身 `effective_from/effective_to`/`version` 未过滤 | `src/application/mindgraph_graph_store.py:64` |
| 🟡 风险 | `structured_clause_store_unavailable` 幽灵原因码（误导） | `src/application/adaptive_retrieval_router.py:125-136` |
| 🟡 配置 | 全局速率限制默认关闭 | `src/infrastructure/settings.py:64` |
| 💭 优化 | 图扩展 O(relations×corpus) 遍历 | `src/retrieval/mindgraph_pipeline.py:132` |

---

## 1. 各 Phase 落实核查

### Phase 0 — 基线审计 ✅ 完成
- `docs/upgrade/MINDGRAPH_ENTERPRISE_BASELINE.md` 已生成：含 commit/环境/入口/调用链/数据集规模/验证结果/与计划差异/Phase 映射。
- 未修改检索行为。符合验收门槛。

### Phase 1 — Golden 数据集治理 ⚠️ 低于验收门槛
**已落地**：`mindgraph_golden_v2.schema.json`、`mindgraph_golden_v2.jsonl`、`mindgraph_candidates_v2.jsonl`（**76 条候选**）、`scripts/generate_candidates.py`、契约测试。
**缺口**：
1. **golden 仅 12 条 approved**（计划要求进入下一 Phase 前至少 30、目标 60–80）。当前 12 条无法支撑 Phase 2/5 的分层消融，且 Route Accuracy≥0.85 等验收目标**无法实证**。
2. **query_type 覆盖单薄**：现有 12 条仅 `multi_condition(5)/versioned_policy(2)/exact_fact(2)/no_answer(2)/exception(1)`；缺 `conflict`(多文档冲突)、`acl_restricted`(权限受限)、同义/缩写、`graph_needed` 对照仅 4 条（计划要 8）。
3. **Schema 与计划 1.1 语义不一致**：实际 schema 字段为 `case_id/question/category/split/expected_behavior/gold_vault_paths/required_facts/forbidden_facts/dataset_version/label_source`；计划建议的 `query_type/answerable/graph_needed/expected_route/acl_context` **未在 schema 中**。全部 12 条数据也**缺失 `answerable` 字段**。需确认契约测试对齐真实 schema（避免"测试通过但校验的不是计划要求的字段"）。
4. candidates 与 golden 物理隔离 ✓；candidate 默认不进评测 Runner ✓。

### Phase 2 — 统一离线评测 ✅ 基本完成
- `evaluation/runner.py`（统一入口 + `--suite`）、`evaluation/manifest.py`（SHA256/commit/参数/分层）已落地，整合 retrieval/routing/answer/ablation 既有评测器，**未另造评分逻辑**（符合计划 2.1 "不得再添加第六套 Runner"）。
- 注意：`DEFAULT_ABLATION_SOURCE` 指向固定历史文件 `comparison_20260713T153349Z.json`，需确认该文件存在且代表当前基线；retrieval suite 是否纳入 golden_v2 需确认。分层指标受 12 条小数据集限制，统计意义有限。

### Phase 3 — 扩展 Adaptive Router ✅ 主体完成 / 有偏差
- typed `RetrievalRouteDecision`（mode/route/strategy/graph_enabled/cost_tier/latency_tier/degraded）、保守确定性路由、cost/latency 分级、rerank **非默认**（仅 `exception_or_conflict`/`cross_policy` 高风险路由启用 `hybrid_rerank`）、graph 受 `graph_allowed` 门控。
- **偏差**：
  - `reasons` 是**自由字符串，非计划 3.2 要求的枚举**（`reason_codes` enum）。当前为受控 snake_case 常量，但无枚举约束，新增原因码无编译期保护。
  - **缺失计划 3.2 字段**：`confidence`、`fallback`、`top_k`、`filters(effective_at)`、`query_type` 未在决策对象中表达 → "Router 低置信度回退 Hybrid"（3.3）无置信度驱动；版本问题"先版本过滤"（3.3）在路由器层未注入 effective_at 过滤（仅靠下游管线）。
  - 🟡 **误导性"幽灵"原因码**：`adaptive_retrieval_router.py:125-136` 的 `structured_fallback` 分支将 `reason` 写为 `"structured_clause_store_unavailable"` 且 `degraded=True`，但**代码从未检查任何 store 是否存在**——原因码与事实不符，违反项目诚信约束（易被面试/审计质疑）。
  - 权限不足→Clarify/No-answer（3.3）仅在复合问题（≥2 问号）时路由 clarification，单条不可答问题无专门路由。
  - 验收目标（Route Accuracy≥0.85 等）无 30+ 标注集无法实证。

### Phase 4 — Typed Policy Graph 最小实现 ✅ 完成 / 有风险点
- `mindgraph_graph_store.py`：typed 关系类型、status 枚举、evidence 字段、`related_note_ids` 仅读 confirmed、参数化查询、跳数限制（1–2）、`seen` 防环、ACL 双向校验、governance 关系强制 chunk 证据。
- `mindgraph_pipeline.py`：仅 hybrid/hybrid_rerank 扩展、补充证据 `original_score=0`（不抢排名）、evidence 可溯源校验、`max_graph_chunks=4`、按 target 聚合取最高 confidence、`_visible_for_trace` 对扩展 chunk 做状态/版本/ACL 过滤、graph_links 记录可追溯。
- **风险点**：
  - 🔴 **图服务无错误兜底**：`MindGraphRetrievalPipeline.retrieve()` → `_expand_graph()` **无 try/except**。若 `graph_store. 相关查询抛错（DB 连接/查询异常），整条检索失败，违反计划 4.5「图服务失败时安全回退 Hybrid」**。应在 `_expand_graph` 外包 try/except 记录 warning 并跳过图扩展。
  - 🟡 **关系自身生命周期未过滤**：`related_note_ids` 只按 `status` 过滤，**未校验 relation 的 `effective_from/effective_to` 与 `source_document_version` 有效性**（计划 4.2/4.4 要求"仅当前版本有效且调用者有权限的边可进入检索"）。管线只校验了目标 chunk 元数据日期，关系自身的版本/有效期未被强制 → 旧版本关系可能进入检索。
  - 💭 `_visible_for_trace` 直接 `filters.get(...)`；若 `trace.applied_filters` 为 None 会 `AttributeError`（脆弱性，正常路径由 base.retrieve 保证非 None，但图扩展分支无守卫）。
  - 🟡 **性能**：`_expand_graph` 对每个候选关系遍历 `self.dense.chunks`（全语料）匹配 mindgraph_id → O(relations×corpus)。小语料可接受，大语料下有性能隐患（应预先建 mindgraph_id→chunks 索引）。

### Phase 5 — Graph 增益消融与发布闸门 ⚠️ 默认态满足 / 实证不足
- ✅ **Graph 默认关闭、客户端 opt-in**：`ChatRequest.graph_enabled` 默认 False，`chat_service` 用 `graph_allowed=request.graph_enabled`；MCP 硬编码 `graph_enabled=False`。满足"未过消融前 Graph 仍实验态、Router 默认关闭 Graph"。
- ✅ `evaluation/ablation_runner.py`、`scripts/run_ablation.py`、`tests/test_phase5_ablation_gate.py` 已落地。
- ⚠️ **消融"门槛"未被代码强制**：计划 5 要求"Graph 仅在满足 Recall@5 提升≥5pp 等条件才进入默认策略"。当前 graph 完全由请求标志 opt-in，没有"基于消融结果自动切换默认策略"的开关，也没有把消融结论写回配置（如 `GRAPH_DEFAULT_ENABLED`）。属"实验态"但**未建立从消融到发布的自动闸门**（gate 是人工/配置决策，非代码强制）——可接受但需明确记录。
- ⚠️ 消融结论依赖 12 条 golden，统计意义不足；`test_phase5_ablation_gate.py` 通过不代表真实增益达标。

### Phase 6 — 可信证据工作台 ✅ 代码改动落地 / 本次未重验构建
- `web/src/pages/ChatPage.tsx`、`EvaluationPage.tsx`、`RelationsPage.tsx`、`Primitives.tsx`、`metrics.ts`、`types.ts` 均修改。
- 基线文档记录 web typecheck/test/build 均 PASS（12 tests）。**本次会话未重新跑 web 验证**（需 `pnpm install` + 网络），建议合入前重跑 `pnpm typecheck && pnpm test && pnpm build`。
- 答案→原文证据链、Graph 启用/未启用对照、权限受限用户看不到隐藏来源元数据等 UX 约束，本次静态核查未逐行验证组件实现。

### Phase 7 — MCP 受控证据接口 ✅ 安全闭合（经核实）
- 五工具均**只读**（list_notes/get_note/search/evaluation_overview/list_relations），有限额（top_k 1–20、limit 1–200、batch 20）、超时（15s `asyncio.wait_for`）、审计（`record_access_audit`）、ACL 裁剪。
- ✅ **鉴权闭合**：`src/api/main.py:132` 挂载 MCP 路由时 `dependencies=[Depends(require_authenticated)]`；非 off 模式下未认证请求在依赖阶段即 401，**不会**到达工具层 `note_acl_matches(None)=True` 的 allow-all 分支（该分支仅在 `AUTH_MODE=off` 可达，而 off 模式本就按设计全量可见）。**早期假设的"未认证泄露所有租户"不成立。** ✅ proposed 关系不暴露（`status='confirmed'`）。
- 💭 `scope` 解析不一致：路由用 `auth.get_optional_principal`（off 模式返回匿名 `authenticated=False`）注入工具，而依赖用 `require_authenticated`（off 模式返回 admin `allow=*`）。off 模式下两者结果都是"全可见"无真实泄露；非 off 模式两者一致。属实现脆弱性，建议统一从 `require_authenticated` 主体派生 scope。
- 💭 `mindgraph_get_note` 工具描述称"含 confirmed 关系"，实际返回体**不含 relations**（仅 note 字段）——描述与实现不符（轻微）。
- 🟡 **全局速率限制关闭**：`RATE_LIMIT_ENABLED=False` 默认值；MCP 有超时+批量上限但无每客户端速率限制。计划 7 要求"限额和超时生效"——超时满足，速率限制缺省关闭，建议生产开启。
- 💭 `PRIVACY_LOG_QUESTIONS=True` 默认记录问题到 query_logs（含潜在 PII）；企业合规场景需明确保留/脱敏策略。

### Phase 8 — 文档与对外口径 ✅ 完成
- 新增 `ADR-001-agent-ready-evidence-layer.md`、`ADR-002-conditional-graph-routing.md`、`DEMO_SCRIPT.md`、`GOLDEN_DATASET_CARD.md`、`GRAPH_SCHEMA_GOVERNANCE.md`、`MCP_PERMISSION_MATRIX.md`、`RETRIEVAL_DECISION_MATRIX.md`、`THREAT_MODEL.md`，README/README.zh-CN 更新。
- ✅ **命名门槛合规**：代码/文档未自称 "Adaptive GraphRAG"（ADR-002 为 "conditional graph routing"），对外口径正确。建议核对 README 是否仍声称 "100+ Golden"（计划 1 验收"README 不提前声称 100+ Golden"）。

---

## 2. 全局技术风险矩阵

| 维度 | 结论 | 证据 |
|---|---|---|
| 依赖冲突 | **无** | `pip check` → No broken requirements；核心包导入正常 |
| 接口不兼容 | 低 | 聊天请求 `graph_enabled` 为可选 opt-in，向后兼容；旧 `mindgraph_golden.jsonl`(2.1.0) 仍被 legacy 入口引用（计划允许保留） |
| 数据迁移 | **安全（非破坏性）** | `CREATE TABLE IF NOT EXISTS` + `schema_meta(version)` + `_ensure_columns`（PRAGMA 查列后 `ADD COLUMN`），幂等前向迁移；`note_relations` Phase 4 证据字段已补齐 |
| 错误处理缺失 | 🔴 图扩展无兜底 | `mindgraph_pipeline.py:59` 无 try/except |
| 性能隐患 | 🟡 图扩展 O(relations×corpus) | `mindgraph_pipeline.py:132` |
| 安全漏洞 | 无真实泄露 | MCP 鉴权闭合、ACL 双向校验、参数化查询、表名 `isalnum` 校验；off 模式全可见为设计内 |
| 诚信偏差 | 🟡 幽灵原因码 | `adaptive_retrieval_router.py:125-136` |

---

## 3. 必须处理 / 建议清单

**🔴 修复（建议合入前）**
1. `mindgraph_pipeline.py`：`_expand_graph` 外包 `try/except`，图服务异常时记录 warning 并跳过图扩展（安全回退 Hybrid），符合计划 4.5。
2. `mindgraph_graph_store.py`：`related_note_ids` 增加 `effective_from/effective_to` 与 `source_document_version` 有效性过滤（调用方可传当前查询日期/版本），落实计划 4.2/4.4。

**⚠️ 计划验收缺口（需决策）**
3. Phase 1 golden 从 12 → 至少 30（目标 60–80），补齐 conflict/acl_restricted/同义/graph_needed 对照等覆盖；否则 Phase 2/5 分层度量与 Route Accuracy≥0.85 无法实证。
4. 统一 golden schema 字段语义（计划 1.1 的 `query_type/answerable/graph_needed/expected_route/acl_context` vs 实际 `category/split/...`），并确保契约测试对齐真实 schema。

**🟡 建议优化**
5. `adaptive_retrieval_router.py`：移除 `structured_clause_store_unavailable` 幽灵原因码（或真正实现对应检查）；将 `reasons` 升级为枚举。
6. `settings.py`：生产环境开启 `RATE_LIMIT_ENABLED`；明确 `PRIVACY_LOG_QUESTIONS` 的保留/脱敏策略。
7. `mindgraph_pipeline.py`：预建 `mindgraph_id→chunks` 索引替代逐关系遍历全语料。
8. `mcp.py`/`auth.py`：统一 scope 从 `require_authenticated` 主体派生，消除 off 模式 anonymous/admin 不一致；修正 `mindgraph_get_note` 工具描述。

**未决（记录即可）**
9. Phase 5 消融闸门为人工/配置决策，未代码强制——属"实验态"，符合计划精神，但建议补充 `GRAPH_DEFAULT_ENABLED` 之类的显式开关与"基于消融结论切换"的说明。
10. 合入前重跑 web `pnpm typecheck && pnpm test && pnpm build`（本次未验）。
