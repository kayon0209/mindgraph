# 检索评测数据规范

`mindgraph_golden.jsonl` 是当前冻结的 V2 黄金集，共 12 条样本（数据集版本
`2.1.0`）。它由 `demo-vault/` 中的公开制度原文人工标注，独立于运行时 SQLite
数据库、系统回答、候选关系和检索排序。当前阶段用它验证评测机制、证据路径和
拒答边界是否有效，不足以支持统计显著性结论，也不是生产效果证明。

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

V2 当前只有 12 条，不能伪设 50 条下限，也不启用检索阈值门禁。只有扩充到路线图
要求的 50--200 条独立样本、完成标注审查并冻结版本后，才应单独评估统计稳定性，
再由维护者决定是否把经过验证的回归阈值接入 CI。扩样前的离线报告只能用于机制验证
和错误分析，不应声称实时评测、端到端模型效果或显著性结论。

旧版数据曾从私人 Vault 和 confirmed 关系自动派生，包含本机路径并造成“用系统输出
证明系统”的数据泄漏；V2 已替换该做法。新增 holdout 不得从旧题改写或运行日志直接
复制，并应在候选版本冻结后独立运行一次，披露样本规模和数据来源。
