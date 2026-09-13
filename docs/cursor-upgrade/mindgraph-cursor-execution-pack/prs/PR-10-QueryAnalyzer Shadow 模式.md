# PR-10｜QueryAnalyzer Shadow 模式

阶段：M2  
依赖：PR-01, PR-05

## ⚠️ 现场核对修正（2026-09-11 第二轮实测，优先于下文）

**修正 1：前提成立，可直接做。** 两处原文前提均实测为真：
- 「固定 confidence 不代表真实置信度」→ `src/application/adaptive_retrieval_router.py:287`
  是**两档硬编码**（`0.85` / `0.95`），不是计算出来的置信度；
- 「关键词/正则无法稳定识别指代」→ `src/application/query_understanding.py`（102 行）
  全部是正则与关键词表，**无指代解析、无跨轮、无条件槽**。

**修正 2（重要，任务书未提）：`QueryUnderstandingService` 已经在生产路径上。**
`src/application/chat_service.py:91` 已在真实问答链路中调用
`.plan(request.question, decision)`，并把 mode 与 reasons 写进 trace（:197、:205）。
它是**规则版查询改写/拆解**，不是 shadow。

→ 因此：本 PR 新增的 QueryAnalysis 契约与 analyzer 必须是**独立的观测层**，
**不得改造 `QueryUnderstandingService` 去「实现 analyzer」** —— 那会直接改变生产路由，
违反本 PR 自己的「第一阶段不改变生产路由」。

**修正 3：测试文件。** `tests/test_adaptive_router.py`（283 行）已存在，
但**没有** `tests/test_query_understanding.py`。新增分析器的契约测试请**新建文件**，
不要塞进 router 测试里。

## 目标

结构化输出意图、实体、缺槽、复杂度、风险和置信度，但第一阶段不改变生产路由。

## 真实业务失败

现有关键词/正则无法稳定识别指代、缺少条件、多目标和跨轮依赖，且固定 confidence 不代表真实置信度。

## 必须先阅读

- `src/application/adaptive_retrieval_router.py`
- `src/application/query_understanding.py`
- `src/domain/models.py`
- `tests/test_adaptive_router.py`

## 范围内

- 新增 QueryAnalysis 契约与 deterministic analyzer。
- 复杂度包含目标数、缺槽、指代、跨制度、日期版本、计算和工具预算。
- shadow 写入 trace，不进入路由决策。
- 与旧 route 对比并生成 disagreement report。

## 范围外

- 不调用 LLM 分类。
- 不改变现有 route。
- 不把敏感原文写入默认日志。

## 实施顺序

1. 获取最新 main SHA、git status 和现有基线。
2. 复现业务失败或证明当前缺口。
3. 先补失败测试与契约测试。
4. 实现最小兼容改动。
5. 运行目标测试、相关回归与必要评测。
6. 输出新旧差异、已知风险和回滚步骤。

## 测试矩阵

- 单目标、多目标、缺槽、指代、版本、多个问号但语义单一、越界问题。

## 验收标准

- 90 条现有集 + 新增 ≥30 条 query-understanding 集有完整输出。
- 分歧可解释。
- 生产答案字节级不变。

## 回滚

停止 shadow 调用；契约字段可忽略。

## Cursor 可复制指令

```text
你只执行 PR-10「QueryAnalyzer Shadow 模式」，不得顺手做后续任务。

先读取 01-GLOBAL-GUARDRAILS.md、本任务书和“必须先阅读”中的仓库文件。先输出现场核对报告和最小改动计划，不要立即写代码。确认仓库现状与任务书一致后，先补失败测试，再做最小实现。完成后运行目标测试、相关回归和必要评测，报告真实输出、行为/指标差异、风险与回滚步骤。若发现任务前提不成立或需要破坏现有契约，停止实现并提交差异报告。
```
