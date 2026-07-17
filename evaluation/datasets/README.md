# 评测数据规范

`expense_qa_v1.jsonl` 是从原 34 题迁移出的首个版本化数据集。它规模较小且源自既有项目，只能用于开发和回归，不能作为生产效果证明。

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
