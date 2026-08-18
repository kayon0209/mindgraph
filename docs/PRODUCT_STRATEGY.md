# MindGraph 产品边界与升级路线

> 状态：Phase 0 已完成；Phase 1 工程基线已落地，clean-clone 人工验收待执行。本文定义方向、阶段和验收，未标注完成的能力不得视为已实现。

## 1. 产品定义

MindGraph 是**本地优先的企业制度与决策依据知识服务**。首个垂直场景聚焦报销、财务与制度合规，随后再验证 HR、采购和法务场景。

核心承诺：

- 回答必须能追溯到原文证据；
- 制度版本、生效期、例外和冲突必须显式处理；
- 无充分证据时拒答或转人工；
- 检索策略按问题类型和成本路由；
- Obsidian、Web 与 MCP 是接入渠道，不是产品本体。

## 2. 当前边界

当前已经具备：

- Dense + Sparse + RRF 混合检索；
- 可选重排、SSE 问答和 citation；
- proposed → confirmed/rejected 的人工确认流程；
- confirmed 笔记关系的一跳补充检索；
- SQLite + FAISS 的本地低运维部署基础。

当前尚未具备：

- 企业连接器、ACL 继承、SSO 和完整审计；
- 受领域 schema 约束的实体/条款关系图；
- 经独立人工 Golden Set 证明的多跳 GraphRAG 增益。

因此，当前准确描述是“带人工确认关系扩展的 Hybrid RAG”，不能宣称已经具备完整知识图谱推理。

## 3. 实施顺序

### Phase 0：产品边界与品牌迁移

- 统一 README、API、包元数据和当前活跃模块的 MindGraph 命名；
- 保留历史报销领域 RAG 项目的演进记录和必要兼容代码；
- 明确当前入口与历史入口；
- 建立后续迁移清单。

验收：新访客能在 30 秒内说明产品对象、核心价值和当前真实能力。

### Phase 1：可复现 Demo 与可信 UI

- 提供公开、脱敏的 `demo-vault/`；
- 将 Web 前端并入 `web/` 或以同一 release 固定版本；
- 提供 demo/production 两套明确启动模式；
- 修复索引统计、评测指标和关系标题展示；
- 用真实 SSE 事件替代假进度；
- 添加 clean-clone smoke test。

验收：干净机器从 clone 到看到带 citation 的答案少于 5 分钟，无需个人 Vault。

当前进度：公开 `demo-vault/`、无密钥离线全链路、同仓 React Web、真实 SSE、索引统计、评测图表和关系标题展示均已实现，并已进入 CI 与 Docker Compose。仍需在全新机器上按 README 完整计时验收；真实模型回答仍需配置 Provider，不能用 Fake 模型冒充生产效果。

### Phase 2：独立评测与证据治理

- 建立人工冻结且独立于运行关系库的 Golden Set；
- 覆盖事实、条件组合、版本、例外、冲突、无答案、多文档和权限场景；
- 为文档增加 policy_key、owner、version、effective_from/to、status；
- 将确定性回归放入 CI，将 LLM judge/RAGAS 放入校准后的周期评测。

验收：能够量化检索质量、引用正确性、拒答正确性、延迟和成本。

当前进度：已用公开 `demo-vault/` 建立 12 条人工冻结 V2.1 回归集，并禁止从运行数据库或 confirmed 关系反向生成 Golden 标签。检索消融与答案评测已拆分：答案层可直接运行当前服务或复用冻结预测，确定性量化 citation F1、拒答正确性、版本有效性、必需事实覆盖与禁用事实规避；P95 总延迟、平均 Token、平均估算成本和数据覆盖率进入同一 `evaluation_runs` 账本，缺失用量不会被当作零成本。样本量、权限场景和人工/LLM judge 校准仍未达到本阶段最终验收。

元数据治理第二批已落地：Vault 中的 policy_key、owner、version、effective_from/to、status 会规范化进入 schema v5，缺失或非法值形成显式质量问题；API、Web 制度台账、检索 chunk 和 citation 均保留制度族、版本、稳定 Vault 路径与有效期语境。同一 policy_key 在查询日期存在多个有效版本时，问答服务会在调用模型前返回 `conflicting_evidence`，SSE 与 Web 完整展示待人工裁决版本。该机制解决“检测并安全拒答”，不自动决定哪个冲突版本有效；权限场景评测、人工复核校准，以及回答质量与 token/P95/单问成本的正式发布门槛仍未完成。

### Phase 3：自适应检索路由

- 事实型问题走 Hybrid；
- 版本、金额和统计走结构化查询；
- 例外与冲突走 Hybrid + 受控关系；
- 长问先澄清、改写或分解；
- rerank 只在高精度小候选场景启用；
- 输出 route_decision、延迟、候选数和降级原因。

验收：相对全量重管道，关键质量不下降，P95 延迟与单问成本下降。

### Phase 4：企业制度断言图

- 建模 DocumentVersion、PolicyClause、Role、Department、ExpenseType、Region、ApprovalRole、Amount/Condition；
- 支持 APPLIES_TO、REQUIRES_APPROVAL、HAS_LIMIT、EXCEPTION_TO、SUPERSEDES、CONTRADICTS；
- 每条边保存原文证据、版本、生效时间、抽取来源、置信度和审核记录；
- embedding 只负责候选召回，confirmed typed edge 才参与遍历。

验收：在版本覆盖、例外和冲突测试集上，相对 Hybrid baseline 有可重复的增量。

### Phase 5：企业接入与受控 MCP

- [x] 本地 Markdown 目录连接器的增量同步、删除同步与 ACL 继承（`DirectoryConnectorService`）
- [x] workspace/部门/文档级 ACL 过滤（检索前裁剪，非生成后过滤）
- [x] SSO/OIDC 最小可行接入（Bearer JWT 校验 + claims → principal 映射）
- [x] 审计日志（`access_audit` 表：谁问了什么、引用了什么、依据什么版本回答）
- [x] 本地 stdio MCP（开发者预览，默认只读）
- [x] 企业 HTTP MCP（`/api/v1/mcp`，走认证 + ACL + 审计）
- [x] 部署指南（`docs/DEPLOYMENT.md`）

验收：越权检索为零（`tests/test_access_control.py` 全绿），回答可按用户、来源版本和证据完整回放（`access_audit` 可追溯）。

## 4. 品牌迁移清单

### Phase 0 处理

- [x] FastAPI 标题、根端点和当前日志命名切换为 MindGraph
- [x] `pyproject.toml` 项目名与描述切换为 MindGraph
- [x] 项目规范更新为当前架构和产品边界
- [x] README 快速开始和定位更新
- [x] 当前实现架构文档更新

### 后续兼容迁移

- [x] 已将 `src/app.py` 移入归档；`rag_engine.py`、`vector_store.py`、`special_cases.py` 仅被 `evaluation/baseline.py` 引用，待评测基线迁移后隔离
- [x] 新备份切换为 `mindgraph-backup-*`，同时兼容读取和清理旧命名
- [x] 更新 `.env.example` 标题与兼容配置说明
- [x] 更新 Nginx、Docker 镜像名和 CI/CD 标签
- [x] 提供公开合成 `demo-vault/` 与无需外部模型的全链路验证
- [x] 将无密钥离线全链路演示加入 Python 3.12 CI smoke test
- [x] React Web 前端并入 `web/`，与 API 同仓构建和发布
- [ ] 更新 GitHub 仓库简介、Topics、Release 和演示素材

当前 CI 采用 55% 覆盖率基线和仅阻断运行时致命错误的 Ruff gate。原因不是标准足够，而是旧代码尚未完全隔离、全量 Ruff 存在大量既有债务；Phase 1 必须把活跃核心覆盖率提升到至少 60%，并逐批恢复完整 lint/format gate。

## 5. 暂不优先

- 不为展示效果迁移 Postgres；
- 不默认启用 Cross-Encoder；
- 不先做泛化社区摘要或个人年度主题；
- 不同时开发多个企业连接器；
- 不把 MCP 安装量当作企业产品的北极星指标。
