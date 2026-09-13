# PR-11｜条件式 Cross-Encoder Rerank

阶段：M2  
依赖：PR-09, PR-10

## ⚠️ 现场核对修正（2026-09-11 实测，优先于下文）

**前提成立，无需修正。** 关键引用已实测：

- 「Reranker 已实现但默认关闭」→ 成立：`src/infrastructure/settings.py:113`
  `RERANKER_ENABLED: bool = False`；配套 `RERANKER_MODEL_NAME = "BAAI/bge-reranker-base"`（:114）、
  `RERANKER_LOCAL_FILES_ONLY = True`（:115）、`RERANK_TOP_N = 10`（:116）。
- 「必须先阅读」的 5 个文件全部存在（`src/retrieval/reranker.py`、`src/retrieval/pipeline.py`、
  `src/infrastructure/retrieval_factory.py`、`src/infrastructure/settings.py`、`tests/test_retrieval.py`）。

**补充（供报告参考，不是修正）**：请求走 `hybrid_rerank` 策略而 Reranker 关闭时，管线会降级为
`hybrid`，并在 trace 上留标记（`RetrievalTraceModel.degraded` / `degradation_reason`，
`src/domain/models.py:110-111`）。本 PR 的分层消融要能解释这批降级样本，
否则「Rerank 收益」会被降级样本稀释。

**依赖提醒**：本 PR 依赖 PR-09，而 PR-09 的任务书前提部分不成立，**需先重写该任务书**再依次开工。

## 目标

仅对高收益路由启用 Rerank，并用消融证明质量、延迟和成本取舍。

## 真实业务失败

Reranker 已实现但默认关闭；缺少真实分层数据决定何时值得付出延迟。

## 必须先阅读

- `src/retrieval/reranker.py`
- `src/retrieval/pipeline.py`
- `src/infrastructure/retrieval_factory.py`
- `src/infrastructure/settings.py`
- `tests/test_retrieval.py`
- `tests/test_adaptive_router.py`

## 范围内

- 支持按 route/risk/candidate ambiguity 启用。
- 记录 rerank 前后排名变化。
- 比较 off/all/conditional 三组。
- 模型缺失或错误回退 hybrid。

## 范围外

- 不默认对所有问题启用。
- 不修改 Golden。
- 不隐藏降级。

## 实施顺序

1. 获取最新 main SHA、git status 和现有基线。
2. 复现业务失败或证明当前缺口。
3. 先补失败测试与契约测试。
4. 实现最小兼容改动。
5. 运行目标测试、相关回归与必要评测。
6. 输出新旧差异、已知风险和回滚步骤。

## 测试矩阵

- 条件命中与不命中。
- 模型不可用、超时、空候选。
- ACL 过滤前后不可泄漏。

## 验收标准

- 质量下降≤1pp，P95 或成本相对 all 降低≥20%，否则保持关闭。
- 降级路径完整。

## 回滚

关闭 route-aware rerank flag，恢复现有默认。

## Cursor 可复制指令

```text
你只执行 PR-11「条件式 Cross-Encoder Rerank」，不得顺手做后续任务。

先读取 01-GLOBAL-GUARDRAILS.md、本任务书和“必须先阅读”中的仓库文件。先输出现场核对报告和最小改动计划，不要立即写代码。确认仓库现状与任务书一致后，先补失败测试，再做最小实现。完成后运行目标测试、相关回归和必要评测，报告真实输出、行为/指标差异、风险与回滚步骤。若发现任务前提不成立或需要破坏现有契约，停止实现并提交差异报告。
```
