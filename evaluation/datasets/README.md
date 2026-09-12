# 检索评测数据规范

`mindgraph_golden_v2.jsonl` 是当前冻结的 V2 黄金集，共 90 条样本（数据集版本
`2.4.0`，split 为 83 development / 7 regression，其中 12 条标记 `graph_needed`）。
样本独立于运行时 SQLite 数据库、系统回答、候选关系和检索排序。
当前阶段用它验证评测机制、证据路径和拒答边界；样本仍不足以支持统计显著性结论，
也不是生产效果证明。历史快照口径（`2.2.0` 的 12/50/54 条、`2.3.0` 的 54 条）见
`docs/GOLDEN_DATASET_CARD.md` 的版本沿革说明。

> 注意：旧文件 `mindgraph_golden.jsonl`（版本 `2.1.0`）是迁移前的历史快照，
> 只被 `scripts/run_ablation.py` 与 `tests/test_enterprise_golden.py` 等遗留
> 评测入口引用；新增与评审一律以 `mindgraph_golden_v2.jsonl` 为准。

离线结构回归测试会直接加载并复用 `evaluation.mindgraph_retrieval_eval` 的
`load_golden_dataset` 和校验器，不调用模型、API、网络或运行时数据库：

```powershell
python -m pytest tests/test_mindgraph_retrieval_eval.py tests/test_answer_evaluation.py -q --no-cov
```

`.venv` 可用时，将命令中的 `python` 替换为 `.venv\Scripts\python.exe`。上述是
当前可用的无模型、无网络、无运行时数据库测试入口。`scripts/run_ablation.py` 已存在，
但它是依赖 MindGraph 运行时索引、检索管线和本地嵌入模型的遗留消融入口，非 V2 数据
结构回归测试；即使使用 `--dry-run`，也不能替代上述离线结构测试。

单条 V2 记录的结构规范见 `mindgraph_golden_v2.schema.json`。实际校验合同集中在
`evaluation.mindgraph_retrieval_eval.validate_golden_cases`：除逐条字段校验外，它还检查
`case_id` 唯一和整个 JSONL 的 `dataset_version` 一致性。

## 数据划分

- `development`：允许用于错误分析和参数选择。
- `regression`：用于回归验证，不参与日常调参。

当前 V2 合同只接受以上两种划分。未来若由未参与开发的标注者新增 `holdout`，必须先
升级 MindGraph V2 校验合同和专用 schema；不得套用历史 Expense-QA 的
`holdout_schema.json`，也不得将当前 regression 宣称为独立测试集。

划分按题型分层后固定写入文件，不在运行时随机抽样。

## Gold 标注规则

1. 标签必须先依据 `knowledge/`（或冻结的 `demo-vault/`）原文完成，不能从运行
   数据库、系统回答或检索结果反推。
2. `gold_vault_paths` 只放回答结论所必需的证据；多份制度共同支撑时必须全部命中
   才算完整证据。路径使用仓库相对路径。
3. `answer` 表示制度原文足以支持可核验结论，至少有一个 Gold 路径；`abstain` 表示
   无制度依据或问题信息不足，Gold 路径必须为空。歧义拒答不能与知识域外拒答混为
   一类，并由 `category` 记录原因。
4. 新增样本必须在看不到系统回答和检索排序的条件下标注，覆盖正常、边界和异常场景，
   同时记录争议样本、来源、`category`、`split`、`expected_behavior`、必要事实和
   禁止事实。
5. `case_id` 在同一版本内唯一，所有记录的 `dataset_version` 必须一致。制度内容、
   切分配置或标注规则变化时递增数据集版本，不覆盖旧版本而不留记录。

## 扩样与阈值门禁

V2 当前有 90 条，已达到路线图要求的 50--200 条独立样本区间，但统计质量
阈值门禁仍未启用（来源/标注审查尚未完成）。当前 CI 只执行数据契约和确定性机制回归；
完成来源/标注审查后，才应单独评估统计稳定性，再由维护者决定是否把经过验证的质量阈值接入 CI。扩样前
的离线报告只能用于机制验证和错误分析，不应声称实时评测、端到端模型效果或显著性
结论。

旧版数据曾从私人 Vault 和 confirmed 关系自动派生，包含本机路径并造成“用系统输出
证明系统”的数据泄漏；V2 已替换该做法。新增 holdout 不得从旧题改写或运行日志直接
复制，并应在候选版本冻结后独立运行一次，披露样本规模和数据来源。

## 评分口径版本（evaluator_version）

数字必须连着口径一起引用。运行记录把口径写在
`evaluation_runs.configuration_json.evaluator_version`，比对历史结果前先确认这个字段。

### v2/deterministic-answer-v2（2026-09-11）

相对 v1 的三处修复，都是「让指标名与真实含义一致」，不是为了让分数好看：

1. **引用正确性改用「正文实际引用的证据」为基准。** v1 用候选证据集合，而系统固定返回
   检索 top-k（实测均值 5.11 条、中位数 5），Gold 通常只有 1 条，于是 precision 被结构性
   稀释——同一批数据里 Gold 文档命中率其实是 92%（76 条中命中 70 条），v1 的
   `citation_correctness` 却只有 0.351。v2 新增 `citation_precision` / `citation_recall`
   把被丢掉的维度显式化。
2. **事实匹配全链路归一化**（NFKC 统一全半角 + 去 markdown 标记 + 去空白）。v1 用裸子串
   `fact in answer`，模型写 `**800 元**` 就匹配不上 Gold 的 `800元`。
3. **「检索到但未被引用」不再判为引用标注缺陷。** 运行时契约里 `citations` 是提供给模型的
   候选证据；系统提示词只要求「使用 [citation-N] 标注引用来源」，从未要求每条候选都被引用。
   v1 该规则在 90 条里命中 79 条，属正常现象而非模型缺陷。改用 `citation_usage_ratio`
   如实记录「候选证据被用掉的比例」。

**v1 数值的延续性**：v1 的引用 F1 原样保留为 `citation_offered_f1`，实测与 v1 的
`citation_correctness` **逐位一致**（`0.3510442774`），因此历史结果可直接对照，不存在
"改了口径就再也比不了"的问题。

**新增指标**：`citation_precision`、`citation_recall`、`citation_offered_f1`、
`citation_usage_ratio`。引用标注完整性的失败码细化为
`citation_marker_malformed` / `citation_marker_unknown` / `citation_marker_duplicate`
（v1 一律记 `citation_marker_integrity`）。

**读法提醒**：`citation_correctness` 是**逐条 F1 再求均值**，而 `citation_precision` /
`citation_recall` 是各自先求均值，因此三者不满足 `F1 = 2pr/(p+r)` 的恒等关系
（例如 0.7799 vs 由 0.7295/0.8838 反推的 0.7995）。这是均值与比值的次序差异，不是错误。

### v1/deterministic-answer-v1

初版确定性口径。引用正确性按候选证据集合算 F1，事实匹配为裸子串，引用标注完整性包含
「必须用尽候选证据」这一条。
