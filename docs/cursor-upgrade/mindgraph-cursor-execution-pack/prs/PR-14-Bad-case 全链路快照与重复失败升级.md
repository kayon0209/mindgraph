# PR-14｜Bad-case 全链路快照与重复失败升级

阶段：M4  
依赖：PR-02, PR-10, PR-12

## ⚠️ 现场核对修正（2026-09-11 第二轮实测，优先于下文）

**修正 1（本 PR 的方向偏差）：你要快照的数据，大部分已经在库里了。**

`query_logs` 表已含 `trace_json / citations_json / usage_json / timing_json / index_version /
prompt_version / actual_provider / category_filter_json`；而 `trace_json` 里的
`RetrievalTraceModel`（`src/domain/models.py:100-121`）**已含**：
`route_decision`、`query_variants`、`original_query`、`dense_results`、`sparse_results`、
`fusion_results`、`reranked_results`、`final_chunks`、`index_version`、`applied_filters`、
`degraded`、`policy_conflicts`、`graph_links`。

现状：`src/application/feedback_service.py:33` 只从中取了 `trace.get("final_chunks")`，
存成 `retrieved_chunks_json` 一列。**所以真实缺口是「没消费」，不是「没采集」。**

→ 改动面应收缩为：**读取面 JOIN `query_logs`**，而不是新建快照列。
   **不要按下文从零扩展 bad-case snapshot 表** —— 那会与 `trace_json` 形成两套事实源。

**修正 2（地雷）：写入端是位置式插入。**
`src/application/feedback_service.py:32` 是
`INSERT OR IGNORE INTO bad_cases VALUES (?,?,?,?,?,?,?,?,?,?,?)` —— 11 个 `?`、**没有列名**。
**任何 `ADD COLUMN` 都会先让这条语句列数不匹配而报错。**
加列之前**必须先把它改成显式列名插入**。

**修正 3：`request_id` 是 UNIQUE，同一 request_id 不可能有第二行。**
表定义 `bad_cases.request_id TEXT NOT NULL UNIQUE`，写入用 `INSERT OR IGNORE` 做幂等。
所以「3 次相近提问」只能实现为**跨 request_id 的近似匹配**（新增 hash 列或新表），
**不能在原表里补行**。

**修正 4：回归导出的骨架已在。** `export_bad_cases()` 已把 `status == "resolved"` 映射为
CSV 的 `regression_candidate` 列 —— 本 PR 补的是「人工审核动作」与升级信号，不是重建导出。

**修正 5：prompt / model / citation 三样的现状。** `prompt_version` / `actual_provider` /
`citations_json` 已在 `query_logs`；**缺的是** citation 正确性判定，以及与 golden 标准答案的关联。

**必须先做的动作**：现场核对报告里**逐项列出「哪些数据已在 `query_logs.trace_json`」**，
据此重写本 PR 的改动清单，然后再动代码。

## 目标

not_helpful 和重复问题形成可归因、可人工处理、可转回归集的质量闭环。

## 真实业务失败

当前 bad_cases 主要保存原问题、回答和最终 chunks，缺少改写 query、各阶段候选、上下文和标准答案；也没有重复失败转人工。

## 必须先阅读

- `src/application/feedback_service.py`
- `src/infrastructure/database.py`
- `src/domain/models.py`
- `src/api/routes/feedback.py`
- `tests/test_answer_evaluation.py`

## 范围内

- additive 扩展 bad-case snapshot。
- 保存 route/entities/variants/stage candidates/index/prompt/model/citation verdict。
- 规范化 hash + 语义相似检测重复。
- 3 次相近提问或 2 次 not_helpful 产生 escalation_suggested。
- resolved 经人工审核导出 regression candidate。

## 范围外

- 不自动把用户原文加入公开数据集。
- 不自动发送给外部工单系统。
- 不保存未经授权的私有候选。

## 实施顺序

1. 获取最新 main SHA、git status 和现有基线。
2. 复现业务失败或证明当前缺口。
3. 先补失败测试与契约测试。
4. 实现最小兼容改动。
5. 运行目标测试、相关回归与必要评测。
6. 输出新旧差异、已知风险和回滚步骤。

## 测试矩阵

- 重复阈值、误聚类、跨会话/跨主体、脱敏、幂等、回归导出。

## 验收标准

- ≥90% bad case 可归因到明确层。
- 人工接管率和解决时间可统计。
- 修复后必须跑单例+全量回归。

## 回滚

关闭 repeat/escalation flag；新增 snapshot 字段继续兼容旧记录。

## Cursor 可复制指令

```text
你只执行 PR-14「Bad-case 全链路快照与重复失败升级」，不得顺手做后续任务。

先读取 01-GLOBAL-GUARDRAILS.md、本任务书和“必须先阅读”中的仓库文件。先输出现场核对报告和最小改动计划，不要立即写代码。确认仓库现状与任务书一致后，先补失败测试，再做最小实现。完成后运行目标测试、相关回归和必要评测，报告真实输出、行为/指标差异、风险与回滚步骤。若发现任务前提不成立或需要破坏现有契约，停止实现并提交差异报告。
```
