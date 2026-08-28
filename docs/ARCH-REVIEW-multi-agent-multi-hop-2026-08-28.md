# 架构审查报告：多智能体编排 与 多跳图谱检索（2026-08-28）

> 审查者：多智能体系统架构师（The Agency / engineering-multi-agent-systems-architect）
> 审查方式：逐文件代码取证 + 评测结果复核 + SQLite 运行数据核查。分析脚本存于 `_audit_outputs/arch_review_stats.py`、`db_probe.py`、`fail_probe.py`（本地，已 gitignore）。

## 核心结论

- **多智能体编排：不需要。** planner/retriever/critic 的职能已被确定性组件覆盖（规则路由、混合检索、生成前冲突中止 + 离线 answer_eval）；真实日志 0 条复杂查询失败；引入只会翻倍 token、拉长已可见的 SSE 延迟（30-40s/答）。
- **多跳图谱：关系数据资产继续积累（是）；检索时开启现在不做（否）。** 90 例消融 R@5 增益 +0.000pp（闸门要求 +5pp）；54 例开/关逐案对比零差异（含全部 12 例 graph_needed）。
- **真正瓶颈在检索召回与评测盲区**：V1 历史版本 not_retrieved、外部手册 retrieved_not_ranked、融合截断——全是检索/排序问题，编排治不了。

## 最重要的机制发现（评测盲区）

图扩展 chunk 以 `original_score=0` **追加在 top_k 之后**（`src/retrieval/mindgraph_pipeline.py` L237-254），而检索评测只计 `final_paths[:top_k]`（`evaluation/mindgraph_retrieval_eval.py` L264-266）。**当前图扩展机制在结构上不可能改变 Recall@5——闸门指标对机制天然失明**，`keep_graph_disabled` 是双重注定的。不能把它当"图谱被证伪"的证据。

反面证据：图追加 chunk 会进入生成上下文与引用列表（不限 top5），可能改善答案证据完整性——但现有评测（只计 top5、retrieval-only）对此失明。补上答案级图消融之前，任何开启/关闭决定都缺一半证据。

## 关键数字

| 策略 | R@1 | R@5 | MRR | 延迟 ms |
|---|---|---|---|---|
| bm25 | 0.544 | 0.838 | 0.743 | 2.6 |
| bm25_vector | 0.550 | 0.886 | 0.785 | 20.2 |
| bm25_vector_graph | 0.550 | 0.886（+0.000） | 0.787 | 18.2 |

- 语料：25 篇笔记 / 581 chunks；图谱：confirmed 关系仅 6 条，proposed=0。
- Golden Set v2：90 例；graph_needed 12 例均为单关系（真多跳案例为零）；无多问号复合句；split 83 dev / 7 regression（回归门禁形同虚设）。
- query_logs 24 条：15 model_unavailable、7 answered、bad_cases=0、feedback 全 helpful。

## 唯一值得做的"类 critic"增强（非智能体）

生成后**确定性引用一致性校验**：正则提取答案中 `[citation-N]` ⊆ 实际 citation_id 集合，不通过打 warning 并随完成帧暴露。坏标记目前会原样进入证据导出包，直接伤害"留证据"主线。零模型成本、零延迟、可写进 answer_eval 回归。

## 多智能体重评信号（出现任一再重启讨论）

1. Golden Set 新增 ≥10 例真多步案例且单遍 required_fact_coverage <0.7，同时子问题单独检索 recall >0.9（证明瓶颈在综合而非检索）。
2. 多轮上下文（P7）落地后出现跨轮规划追问。
3. not_helpful 占比 ≥5% 且聚类为"漏子问题/张冠李戴"。

届时最小形态：ChatService 本身即编排者，不新增框架；planner 仅对 clarification_required 路由启用（先规则后 LLM 兜底）；critic 用确定性校验；失败即落回单遍（现有 5 种降级状态即回退契约）。

## 图谱若未来开启的条件

- **路由触发**：仅 exception_or_conflict / cross_policy / case_reasoning 且 graph_allowed=true（接线点现成：路由器已标记 GRAPH_EXPANSION_DISABLED_BY_REQUEST）。factual/exact_title 永不扩图。
- **跳数**：默认 1 跳；升 2 跳需 ≥5 例双关系链案例证明 1 跳不足。
- **融合降级**：图扩展失败落回纯 hybrid（已有 try/except）；图 chunk 必须带来源标记（graph_evidence/via_relation 元数据已存在）且在引用 UI/导出包可见"经关系扩展引入"；关系证据不可回原文即剔除（已实现）。
- **准出五条证据**：① graph_needed 分层含图追加口径 recall ≥+5pp 且 graph_control 零回归；② answer_eval 双跑 citation F1/required_fact_coverage 不劣；③ ACL 泄漏 0；④ 延迟 ≤3× baseline；⑤ 人工写 GRAPH_DEFAULT_ENABLED=true（evaluate_graph_gate 只出建议）。

## 分阶段建议

| 阶段 | 内容 | 准出 |
|---|---|---|
| 0 修评测盲区（先于一切图谱讨论，0.5-1 天） | 检索评测双口径（base / +graph）；答案级图消融（answer_eval 双跑分层）；引用一致性校验落地为指标 | 三条新指标进质量账本，90 例基线归档 |
| 1 关系资产积累（持续，无运行时改动） | 定期跑关系抽取；补齐 12 例 graph_needed 的 confirmed 边；regression split 7→≥20 例 | 12/12 边存在；regression ≥20 例 |
| 2 检索真缺口修复（比编排和图谱都优先） | V1 历史版本召回；外部手册 reranker/chunk 粒度排查 | 8 个失败案例 ≥6 个转 final；R@5 不回归 |
| 3 图谱条件开启（仅当阶段 0 证据达标） | 按上述触发/跳数/降级/准出执行 | 五条证据全达标 |

## 反对过度设计清单（红线）

1. 不引入外部图库（Neo4j 等）——25 笔记 6 边，SQLite note_relations + 现有 BFS 余量巨大。
2. 不做 GraphRAG 社区摘要/实体消歧——frontmatter 元数据比 LLM 抽取更可靠。
3. 不做查询时 LLM 关系抽取——所有边走 proposed→confirmed HITL。
4. 不把 keep_graph_disabled 当"图谱被证伪"——机制与指标互盲，结论不能外推。
5. 不用多智能体解决检索问题。
6. 不在 regression split 修复前扩大能力面。
7. 不为假想查询形态预建能力——先补案例，让评测说话。

## 证据索引

| 结论 | 证据文件 |
|---|---|
| 单遍流水线与降级链 | `src/application/chat_service.py` |
| 确定性路由/查询理解 | `src/application/adaptive_retrieval_router.py`、`src/application/query_understanding.py` |
| 图扩展机制（追加式、score=0） | `src/retrieval/mindgraph_pipeline.py` |
| 图治理 | `src/application/mindgraph_graph_store.py`、`src/application/relation_extraction_service.py` |
| 闸门定义 | `docs/ADR-002-conditional-graph-routing.md`、`evaluation/ablation_runner.py` |
| 开/关逐案零差异 | `evaluation/results/retrieval_external_graph_on.json` / `_graph_off.json` |
| 答案级确定性评测 | `evaluation/answer_eval.py` |
