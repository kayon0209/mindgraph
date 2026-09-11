# MindGraph Cursor Execution Pack

生成日期：2026-09-11  
取证基线：`aa3943aad99f377970d16d1f7da9c0f3432fd3c3`（实测 = 远端 `main` 当前 SHA，未过期）  
现场核对：2026-09-11 完成，结论见 `05-FIELD-VERIFICATION.md`

> ⚠️ **交 Cursor 前必读三件事**：
>
> 1. **先读 `05-FIELD-VERIFICATION.md`**（第二轮核对已完成，**15 份任务书全部核过**）。
>    它记录了 3 条会系统性误伤的门禁陷阱、11 处错误或缺失前提、阶段编号不对齐问题。
>    任务书正文与它冲突时，以它为准。
> 2. **基线当前状态：已有 PR 在工作区开工**（PR-02，2026-09-11 21:33 实测）。
>    按 `05` 第 4.1 节的 **时机 B** 处理：工作区混着 A 类（PR-01 收尾加固）、
>    B 类（评测栈数据集分派，**不属于执行包任何 PR**）以及本 PR 自己的改动。
>    **不要提交、不要 stash、不要搬家**；A/B 类作为「既存改动」隔离，不得进入本 PR 的最终提交。
>    B 类归属决策推迟到本 PR 验收之后，**不构成阻塞**。
>    未提交改动已备份到仓库外 `D:\demo\output\_mindgraph_uncommitted_backup_20260911\`。
> 3. **`06-NIGHT-GATE-LOG.md` 是夜间只读门禁日志。** 执行期间由定时巡检追加，
>    含每个时间点的 HEAD / 工作区条目数 / 定向测试结果 / R1–R5 风险命中。
>    开工前扫一眼最近几条，比读任何自述都可靠。

> 🛑 **执行器停手清单（做到这几条必须停下来问人，不得自行决策）**：
>
> | PR | 为什么必须停 |
> |---|---|
> | **PR-12** | 把历史回放进服务端 prompt = 把 MindGraph 从**单轮无状态**改成**有状态多轮**。这是**重新定位**，不是普通 PR，且与已冻结的对外口径直接冲突 |
> | **PR-13** | 「可恢复澄清（interrupt + resume）」按项目边界规则属于 **BrandAgent 的领地**。表已建好（schema v14、当前无消费方），**越容易做越要停** |
> | **PR-04** | 「69 扁平 vs 98 StructuredChunker 哪条口径正确」是产品口径决策 |
> | **PR-09** | Parent–Child 的消费策略未定 |
> | **PR-01** | `/api/v1/evaluations` 默认数据集入口未定 |
>
> 这些项的完整列表见 `manifest.json` → `field_verification.blocking_human_decisions`。


## 用法

1. 将本目录放入 MindGraph 仓库的 `docs/cursor-upgrade/`，或在 Cursor 中同时打开本目录与仓库。
2. 新建分支，不允许直接在 `main` 上开发。
3. 先把 `01-GLOBAL-GUARDRAILS.md`、`05-FIELD-VERIFICATION.md` 和目标 PR 文件交给 Cursor。
4. 一次只执行一个 PR。前一个 PR 未验收，不得开始后一个。
5. Cursor 必须先完成“现场核对”，再提出计划；不得看到路线图后直接改代码。
6. 每个 PR 结束时必须输出：改动文件、测试结果、指标差异、已知风险、回滚方法。

## 推荐执行顺序

- M0：PR-01～PR-05（事实、安全、切分一致性、冲突归因）
- M1：PR-06～PR-09（页级解析、OCR、跨页恢复、Parent–Child）
- M2：PR-10～PR-11（Query Analyzer、条件式 Rerank）
- M3/M4：PR-12～PR-14（多轮、澄清、bad case）
- M6：PR-15（存储接口抽象，仍保持 Local Profile 默认实现）

> **建议首个交付 PR-02**（前提已实测成立、改动面最小、安全收益明确），**前置条件见上方第 2 条**。
> PR-02 有四处需修正：文件清单补 `src/application/agent_service.py`、不改 schema、
> 认证口径必须用 `current_actor`（str）、**测试注入必须 patch `api.routes.feedback.current_actor`
> 而不是 `api.auth.current_actor`**（后者实测无效，且**不得**为了让测试变绿而削弱归属校验）。
> 详见 `05-FIELD-VERIFICATION.md` 第 6 节与其 §6.2、以及 `prs/PR-02-修复反馈归属安全.md` 修正 4。

> 上述阶段号为本执行包自用编号，仓库 `docs/UPGRADE_PLAN.md` 使用 UG-001…UG-008，
> 两者对应关系见 `05-FIELD-VERIFICATION.md` 第 3 节。

## 文件说明

- `01-GLOBAL-GUARDRAILS.md`：所有任务共同遵守的安全、兼容和工程红线。
- `02-QUALITY-GATES.md`：离线、真实模型、安全、性能和发布门禁（命令已按现场核对修正）。
- `03-CURSOR-BOOTSTRAP-PROMPT.md`：每次开启 Cursor 会话时先发送的总提示词。
- `04-PR-TEMPLATE.md`：后续新增任务的标准模板。
- `05-FIELD-VERIFICATION.md`：**现场核对总览**（**优先于其余任务书**）。
  含 §1.1 分支策略、§4.1 基线前置动作、§6 全部 15 个 PR 的前提修正。
- `prs/PR-01...PR-15.md`：可以逐份交给 Cursor 的独立任务书（**15 份均已同步现场核对修正**）。
- `06-NIGHT-GATE-LOG.md`：**夜间只读门禁日志**，记录每个时间点的 HEAD / 工作区条目数 /
  定向测试结果 / R1–R5 风险命中（实现被削弱、注入失效、改动堆积、越界执行、环境变更）。
- `manifest.json`：机器可读的顺序、依赖和阶段信息，含 `field_verification` 摘要与
  `blocking_human_decisions` 清单。

## 总原则

这套材料不是让 Cursor 一次性完成 15 个 PR，而是把 12 周路线图拆成可验证、可回滚的增量任务。任何任务如果发现仓库现状与本文不一致，应先停下并提交差异报告，不得强行套用旧方案。

**依赖是一条 14 层深的串行链，不是可并行的清单**（实测自各任务书 `依赖：` 行）：

```
PR-01 ─ PR-02 ─ PR-03 ─ PR-04 ─ PR-06 ─ PR-07 ─ PR-08 ─ PR-09 ─ PR-11 ─ PR-15
                 └ PR-05 ─ PR-10 ─ PR-12 ─ PR-13 ─ PR-14
```

`manifest.json` 的 `field_verification.pr_corrections` 中 **PR-12 / PR-13 标了
`STOP AND ASK THE HUMAN`**：它们会改变项目的交互模型与边界，不是可以「顺手做完」的增量。

