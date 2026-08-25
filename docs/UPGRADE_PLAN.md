# MindGraph 升级计划（Roadmap / Upgrade Plan）

> **状态说明**：`Partial` 表示已有代码入口，但尚未满足端到端验收、迁移或完整 CI 条件；只有全部验收通过后才可改为 `Done`。RAG 质量以“正确、完整、可追溯且不越权的证据”为准，而不是以回答是否流畅为准。

## 优先级与执行顺序

- **P1 — 先保证安全、数据完整性和可测性**：权限必须在召回前生效；连接器必须能安全识别并删除自己的内容；每次改检索都必须有可重复的质量证据。
- **P2 — 再提高资料理解、召回和运行质量**：OCR、语义多查询、知识生命周期、MCP 异步化与 OIDC 实际联调。
- 不引入新的向量数据库作为路线图目标。当前 SQLite + NumPy 基线先把分片、元数据、过滤、混合召回和评测做到可验证；只有指标证明基线成为瓶颈时，才单独评估存储替换。

## 规划条目一览

| ID | 升级项 | 优先级 | 状态 | 核心缺口 / 交付标准 |
|----|--------|:---:|:---:|----|
| UG-003 | 多租户 / 权限前置过滤 | **P1** | **Done** | 可审计、可回滚 ACL 回填；未授权 chunk 不得进入融合、重排或模型上下文 |
| UG-004 | 企业连接器 source ownership | **P1** | **Done** | 正式来源归属、完成迁移和 source-aware prune；外部源仍不可改写 |
| UG-007 | 检索质量评测与证据可观测性 | **P1** | **Partial** | 仍缺 50–200 独立样本、期望证据标注和 CI 回归门禁 |
| UG-001 | 鲁棒文档 ingestion（layout-aware） | P2 | **Partial** | 执行 OCR；按语义结构、表头、单位、页码和来源构建可独立回答的 chunk |
| UG-002 | Query 理解与多查询召回 | P2 | **Partial** | 各 query variant 分别检索，合并去重并以元数据/ACL 预过滤后排序 |
| UG-008 | 知识治理与生命周期过滤 | P2 | **Implemented** | 策略、显式迁移、协调、索引/检索过滤、受控 API 与 Web 人工复核已实现；仍待独立最终审查和生产迁移流程 |
| UG-005 | MCP 只读工具 + 隐私审计 | P2 | **Partial** | async handler 不阻塞事件循环；问题文本审计服从统一隐私策略 |
| UG-006 | SSO / OIDC 企业验收 | P2 | **Partial** | 已完成 discovery/JWK client 缓存；仍需真实非生产 IdP 的端到端验收 |

---

## 跨条目验收原则

1. **分片先于调参**：每个 chunk 必须保留标题/章节路径、适用条件、结论和来源定位；表格必须带表头、行名与单位，不能只索引孤立单元格。
2. **清洗保留检索线索**：删除页眉页脚、导航和重复噪声；保留版本、有效期、来源、页码、权限和业务范围，并把可过滤字段写入 metadata。
3. **过滤前置**：权限、文档状态、有效期、版本和知识分类必须在候选生成阶段收窄范围；后置 Python 裁剪只可作为防御纵深。
4. **上下文最小且可追溯**：只向模型提供经过过滤和排序的必要证据；证据缺失时拒答，证据冲突时说明冲突而非擅自裁决。
5. **每项先测后改**：先加入正常、异常、边界测试，再改生产代码；修改分片、检索、embedding、reranker 或 prompt 时必须运行检索回归集。

### UG-003：多租户 / 权限前置过滤（P1，Done）

**实现**：
- `notes` 表新增 `workspace` / `department` / `acl_json` / `acl_public` 列；
- 检索管线 `_filter_by_access` 在候选融合后、最终返回前按 ACL 裁剪；
- 台账列表、单条详情、关系列表、评测概览均按当前主体 `access_scope` 裁剪；
- `access_audit` 表记录每次访问的 actor / action / resource / decision / reason。
- 企业认证模式下缺失、无效或失败的凭据返回 401，不再降级为匿名；
- `AUTH_MODE=demo` 的匿名主体使用 public-only scope，`AUTH_MODE=off` 是唯一显式 ACL bypass。
- `AclBackfillService` 提供可审计的 plan / apply / rollback；CLI 默认 dry-run，输出仅含聚合计数和 run ID。

**运维约束**：生产历史数据回填由操作人员执行，不自动运行。CLI 使用运行时 `DATABASE_PATH`，要求目标数据库已完成当前 schema 初始化，且自身不执行 schema 迁移；默认 dry-run 以 SQLite 只读模式运行。流程为 dry-run → 审核 unresolved/private 聚合计数 → apply 并保存 run ID → 必要时按精确 run ID rollback。成功输出仅含聚合计数与 run ID，失败仅返回固定脱敏 JSON 和非零退出码。无法可靠判定的记录默认 private；trace 仅记录排除计数与原因，不记录私有正文。

**验收**：无权限 chunk 不出现在 dense/sparse/fusion/rerank/LLM context 任一阶段；回填可重复、可审计、可回滚；最终 `_filter_by_access` 仍作为防御纵深。

### UG-004：企业连接器 source ownership（P1，Done）

**实现**：
- `DirectoryConnectorService` 支持本地 Markdown 目录增量同步；
- 新增 / 修改检测基于 `content_hash`；删除同步暂时关闭，避免无 source ownership 时跨源误删；
- workspace / department 推断优先级：frontmatter > 请求参数 > 目录结构 > 根 `acl.json`；
- 同步结果写入 `connector_syncs` 表（含 file_count / added / updated / pruned / error）；
- API：`POST /api/v1/connectors/directories`。
- 同步只允许 `CONNECTOR_ALLOWED_ROOTS` 或项目 `knowledge/` 下的 canonical path；
- 连接器不再向源 Markdown 注入 ID，不同 source 通过稳定 connector 前缀临时隔离同名相对路径。
- 外部 source 文件已通过 `connector_syncs` 的受控路径解析进入索引；该项不再是路线图缺口；
- `notes.source_id` 已持久化并迁移历史记录；只有上次快照和本次扫描完整成功时才会执行 source-aware prune。扫描失败、部分读取失败或 source 不可达时绝不删除，外部内容也绝不写回源文件。

**验收**：connector A 的删除永不影响 connector B 或内置 Vault；失败扫描零删除；删除后索引原子切换且缓存失效；迁移前后 note ID、ACL 与检索结果一致。

### UG-005：MCP 只读工具 + 隐私审计（P2，Partial）

**实现**：
- `src/mcp_server.py`：自包含 JSON-RPC 2.0，无外部 SDK 依赖；
- 工具：`mindgraph_list_notes` / `mindgraph_get_note` / `mindgraph_search` / `mindgraph_evaluation_overview` / `mindgraph_list_relations`；
- stdio 传输（开发者本地）+ HTTP 传输（`/api/v1/mcp`）；
- 所有工具按当前主体 ACL 裁剪，调用写入 `access_audit`。

**剩余工作**：将 CPU/IO 型同步检索移至线程池或显式同步边界，保证并发请求不阻塞事件循环；批量请求维持单项错误隔离；审计 metadata 不保存问题原文，除非 `PRIVACY_LOG_QUESTIONS=true` 且调用方有相应权限。

**验收**：并发、取消、隐私开关与 ACL 回归测试通过；审计可关联调用但不默认泄露问题文本。

### UG-006：SSO / OIDC 企业验收（P2，Partial）

**实现**：
- `src/api/oidc.py`：Bearer JWT 校验（RS256/HS256），JWKS 自动发现与缓存；
- claims → principal 映射：`workspaces` / `departments` / `roles` 自动转成 ACL scope；
- 与 API Key 并存：OIDC 使用 Bearer；API Key fallback 必须显式使用 `X-API-Key`；
- 未配置 `OIDC_ENABLED=true` 时完全不影响现有认证流程。
- issuer discovery、动态 `jwks_uri` 和跨请求 JWK client 缓存已完成；该项不再是路线图缺口。

**剩余工作**：使用真实的非生产企业 IdP 验证轮换 JWK、过期 token、错误 issuer/audience、角色与 workspace/department claims；验证 discovery 不可用时仅在缓存仍有效且 issuer 一致时降级。

**验收**：不提交、不记录测试 token、client secret 或租户信息；真实 IdP 用例和离线契约用例均通过。

---

## UG-007：检索质量评测与证据可观测性（P1，Partial）

**实施范围**：建立 50–200 条脱敏的真实高频问题集；每题记录期望文档、章节/chunk、版本/有效期、权限主体，以及应拒答或应标记冲突的情形。报告 Recall@k、MRR、引用正确率、版本正确率、拒答正确率、ACL 泄露数、重复候选率、延迟与 token 使用量；`RetrievalTrace` 输出 variants、候选数、预过滤原因、融合/重排变化和最终引用 ID。

**剩余工作与验收**：仍需建立 50–200 条独立脱敏样本并完成期望证据标注；正确证据未进 Top-k、进入但排序靠后、证据第一但生成错误三类问题可被区分；任何 ACL 泄露为零容忍失败；确定性离线回归作为 CI 门禁，外部模型评测单独标记为非阻塞。

## UG-001：鲁棒文档 ingestion（layout-aware）（P2，Partial）

**已实现**：PDF layout/table 解析入口与 `ocr_required_pages` 检测；Markdown 主路径的标题分段与结构化 chunk 基线。

**剩余工作与验收**：对无文本层或低文本密度 PDF 条件执行本地 OCR；OCR 失败保留诊断并禁止将空/乱码内容标为可检索成功。按标题、段落、条款、列表、问答和表格边界分片；制度文档保留条款编号和上级标题，表格序列化携带表头、行名、单位、页码与来源。扫描 PDF、混合 PDF 与含表 PDF 必须有离线 fixture；`.md` 主路径的性能与回归召回不得退化。

## UG-002：Query 理解与多查询召回（P2，Partial）

**已实现**：确定性 query rewrite/decompose 规则和 variants 追踪。

**剩余工作与验收**：对每个有效 variant 分别执行 dense + BM25 检索；在 metadata/ACL 预过滤后按 canonical chunk ID 去重、保留最佳分数和命中 variant，再融合与可选 rerank。信息不足或高风险问题先澄清；不得用改写虚构用户未提供的版本、权限或事实条件。用 UG-007 问题集校准候选预算和上下文长度，确保复合问题的期望证据覆盖率提升且重复候选受控。

## UG-008：知识治理与生命周期过滤（P2，Implemented / 待独立最终审查）

**实施范围与验收**：入库阶段识别草稿、过期、重复、冲突和权限不明资料；默认不把不可判定资料作为可回答的正式知识。索引和检索阶段一致执行 `policy_status`、有效期、版本和来源权威级别规则；同一逻辑文档多版本并存时默认优先当前有效版本，并显式暴露冲突。过期/草稿资料不得成为默认证据，治理动作必须有审计记录。

**当前实现**：统一治理策略已接入 reconciliation、同步后索引构建、ACL 后且检索前的治理过滤、冲突/无证据拒答、case/event API、隐私安全 health 和 Web 人工复核队列。索引只接收当前有效且 canonical 的 eligible note；alias、冲突、过期、草稿和 unresolved note 不得进入。检索仍以数据库权威决策为准，索引 metadata 只作防御性校验；ACL 始终先于治理、dense、BM25、融合、rerank、关系扩展和模型上下文。

全新数据库直接初始化为 schema 9；既有 schema-8 数据库在正常服务启动时必须继续保持 schema 8，不允许隐式升级。此时 health 可启动并报告治理 unavailable，治理 API 与 governed MindGraph 路径返回受控 503，不得回退到未治理检索，直到操作员显式执行 `scripts/migrate_governance_schema.py --apply`。

迁移 CLI 使用运行时 `DATABASE_PATH`，默认（或 `--dry-run`）通过 SQLite 只读连接验证 `8 -> 9` 计划；`--apply` 在一个 `BEGIN IMMEDIATE` 事务中创建四张治理业务表、append-only 事件触发器和保留的 `schema_migration_runs` 审计表，并返回 run ID。`--rollback RUN_ID` 只接受该精确 completed run；四张治理业务表中任一存在记录即拒绝回滚。成功回滚删除治理业务对象、把逻辑版本恢复到 8，但保留并更新迁移账本。

CLI 的 stdout 始终只有一个聚合 JSON，错误返回固定脱敏 code 和非零退出码，不输出数据库路径、note ID、正文、标题、ACL、凭据或 traceback。`governance_events` 的 schema 及写入契约不得承载正文、标题、路径、ACL 或任何 token/secret。

**开发与验收边界**：开发与自动化测试只在 pytest 创建的临时 SQLite 数据库中执行 apply/rollback；未对 `data/product/product.sqlite3`、真实企业数据库或用户数据库执行 schema 9 apply/rollback。生产迁移仍需独立备份、审批与操作员变更流程。本分支实现仍需 Task 7 独立审查和 branch-wide 最终审查，审查完成前不宣称最终验收 Done。

Graph 能力的准确边界是 confirmed 关系的一跳受控扩展，不是完整知识图谱引擎，也未证明多跳 GraphRAG 增益。UG-007 仍只有 12 条人工冻结样本，扩样与 CI 阈值未完成；OIDC 也仍缺少真实非生产 IdP 的外部验收。

---

## 本次补充的参考与取舍

- 《RAG 知识库打不准的三步排查：分片、清洗、召回》提供了证据链诊断顺序，已吸收为分片、metadata 保真和召回定位标准；其不足是没有覆盖权限前置、版本治理、迁移安全和持续评测。
- 《AI 知识库别乱搭：RAG 与向量库实战讲透》提供了数据—索引—推理—反馈的全链路框架，已吸收为混合检索、上下文最小化、来源引用、生命周期治理和评测门禁；其不足是偏通用架构讨论，未给出本项目 SQLite 迁移、离线基线和 ACL 实现路径。
- 因此本路线图不把“更换向量库”当成优化捷径：先用评测证明问题发生在分片、清洗、过滤、召回还是生成层，再针对性改动。
