# 评测数据规范

`expense_qa_v1.jsonl` 是从原 34 题迁移出的首个版本化数据集。它规模较小且源自既有项目，只能用于开发和回归，不能作为生产效果证明。

`mindgraph_golden.jsonl` V2 是基于仓库内公开 `demo-vault/` 人工冻结的企业制度回归集。标签不读取运行数据库、系统回答、候选关系或检索排序，当前包含版本、替代、阈值、例外、跨制度、案例推理、无答案和歧义类型。`run_ablation.py` 只把 `expected_behavior=answer` 的样本用于检索指标；abstain 样本保留给后续回答与拒答评测。

旧版 `mindgraph_golden.jsonl` 从私人 Vault 和 confirmed 关系自动派生，既包含本机路径，也会造成“用系统输出证明系统”的数据泄漏；V2 已完全替换该做法，脚本禁止从运行数据库覆盖独立 Golden Set。

## 数据划分

- `development`：允许用于错误分析和 Milestone 2 参数选择。
- `regression`：只用于回归验证，不参与日常调参。
- `holdout`：未来由未参与开发的标注者新增，格式见 `holdout_schema.json`；当前没有 holdout 数据，不得将 regression 宣称为独立测试集。

划分是按题型分层后固定写入文件，不在运行时随机抽样。

## Gold 标注规则

1. `gold_chunk_ids` 只放回答结论所必需的证据；多块共同支撑时必须全部命中才算完整 Chunk hit。
2. `acceptable_chunk_ids` 放相关但非必需或可替代的证据，只参与引用准确性判断，不替代必需证据。
3. Chunk ID 使用 `<doc_name>::<chunk_index>`，对应当前标题感知切分配置（500 字，overlap 50）。
4. 拒答题没有 Gold 文档和 Chunk；歧义问题使用 `abstain`，不能与知识域外拒答混为一类。
5. Gold 只根据 `knowledge/` 原文标注。算法表现不能反向改变 Gold；制度或切分变化必须提升数据集版本。

## Future holdout 准入

- 问题不得从现有 34 题改写或从运行日志直接复制。
- 标注者在看不到系统回答和检索排序的条件下先标 Gold。
- 至少覆盖正常、边界、异常三类场景，并独立记录争议样本。
- holdout 只在候选版本冻结后运行一次；结果必须披露样本规模和数据来源。
