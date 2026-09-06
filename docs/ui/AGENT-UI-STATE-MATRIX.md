# Assist UI 状态矩阵（AGENT-UI-STATE-MATRIX）—— UI-G 闸门交付物

> 覆盖 Assist 模式全部 UI 状态与 SSE 事件→状态映射。M2 前端实现与本表一一对应；
> 遗漏状态即闸门未通过。基线（flag off）状态沿用现有五视图，不在本表重复。

## 1. 状态总表

| # | 状态 | 触发（SSE/数据） | 主区表现 | 证据轨表现 | 下一步操作 |
| --- | --- | --- | --- | --- | --- |
| S1 | empty | 初始 | 空态引导（现有） | 占位符（现有） | 提问 |
| S2 | routing/retrieving | `retrieval_routed`/`retrieval_started` | 问题气泡 + 「正在查找相关制度」 | TraceStep 查找步骤=进行中 | 取消（现有断开逻辑） |
| S3 | plan ready | `plan_created` | AgentExecutionSummary「按 N 步查询」 | PlanPanel 展开 N 步（默认折叠） | 等待 |
| S4 | tool running | `tool_call_started` | 摘要行「正在咨询《制度名》」 | 对应步骤=进行中 | 等待 |
| S5 | tool succeeded | `tool_call_finished` status=ok | 摘要更新 | 步骤=完成，记录条目（名/目的/耗时/脱敏摘要） | — |
| S6 | tool failed | `tool_call_finished` status=error | 摘要「其中 1 步未完成」 | 步骤=×，原因（脱敏） | 后续步骤继续或 fail-closed 进入 S9/S11 |
| S7 | tool denied | status=denied（ACL） | 无生成，permission 文案（现有） | 步骤=×，"权限不足" | 申请权限（request_access 指引） |
| S8 | tool timeout | status=timeout | 摘要「其中 1 步超时」 | 步骤=×，"超时" | 重试提问（retry） |
| S9 | clarification required | `clarification_required` | ClarificationCard（≤3 问） | 不动 | 提交补充 → 拼接为独立新问题；不发送恢复字段 |
| S10 | policy conflict | `policy_conflict_detected` | ConflictCard：已停止生成 + 版本族 | 引用列表 + 冲突标记 | 导出证据 / 人工裁决 |
| S11 | insufficient evidence | completed(state=insufficient_evidence) | 现有占位文案 | 现有占位 | 补充信息（走 S9 澄清语义）或重新提问 |
| S12 | permission denied | completed(state=permission_denied) | 现有 ShieldQuestion 占位 | 现有占位 | 联系管理员 |
| S13 | citation integrity failed | `citation_integrity_checked` failed | CitationIntegrityNotice + evidence-only 展示 | fidelity 警告（现有样式升级） | 重新生成（一次）/ 仅看证据 |
| S14 | degraded / loop fallback | `loop_fell_back` 或 `degraded` | LoopFallbackNotice 一句话 | PlanPanel 标注中断点 | 按单次结果继续 |
| S15 | generating | `generation_started` | 流式答案（现有） | 生成步骤=进行中 | 停止 |
| S16 | completed | `completed` | 答案定格 | 引用/校验/用量汇总（现有） | 导出/追问 |
| S17 | aborted/offline/system error | `error` 事件 / 断流 | 现有 error 处理 + 重连提示 | 保留已到达状态 | 重试 |

任务态（queued/running/waiting_approval/completed/failed/cancelled）不属于 M2，预留给 G1 后的 TasksPage 设计评审。

## 2. SSE 事件 → UI 状态映射表（M2 实现对照）

| 事件 | 前端处理（handleEvent 分支） | 目标状态 |
| --- | --- | --- |
| `plan_created` | setTurn({plan: data.steps})；AgentExecutionSummary 挂载 | S3 |
| `tool_call_started` | plan.steps[i].status="running"；ToolActivityPanel append(running) | S4 |
| `tool_call_finished` | steps[i].status=data.status；记录条目补耗时/摘要；S6/S7/S8 按数据分流 | S5–S8 |
| `clarification_required` | setTurn({clarification: data})；**不**走 error 分支 | S9 |
| `loop_fell_back` | setTurn({fallbackReason: data.reason})；挂 LoopFallbackNotice | S14 |
| `citation_integrity_checked` | setTurn({citationIntegrity: data})；failed→S13 | S13 或 S16 |
| 既有 14 事件 | 现有 handleEvent 逻辑不变 | S1/S2/S10–S12/S15–S17 |

**未知事件**：现有 `parseSseFrames` 对事件名开放——`handleEvent` 的 switch 遇未知 case 忽略并保持轮次可继续（M2 契约测试固化）。

## 3. 状态机规则（fail-closed 映射到 UI）

1. S10（冲突）后**永不**进入 S15（生成）——前端在 conflict 事件后忽略后续 answer_delta 的可信渲染（后端同样不生成，双保险）。
2. S13 检验失败后允许一次「重新生成」；按钮文案明确"重新组织引用"，不是"重试"。
3. S6+S7 组合（任一工具 deny）→ 该轮直接进入 S12 终态，无生成。
4. S9 澄清提交是新请求：新 Turn、新 request_id；旧 Turn 的 clarification 卡转为已回答态，不可再编辑。
5. 所有 assist 新状态在 flag off 时不可达（前端按 settings 不渲染 assist 组件）。

## 4. 可访问性检查点

- 每个状态变化通过 `aria-live="polite"` 播报一句话结论（文案见 COPY-DECK）。
- 状态不依赖纯颜色：×/✓/呼吸点必配文字标签。
- ClarificationCard 表单字段全部带 label（不靠 placeholder）。
- 键盘：PlanPanel/ToolActivityPanel 的 details 用原生 Tab 可达；卡片操作按钮进 Tab 顺序，焦点可见（现有 focus-visible token）。

## 5. 桌面与窄屏线框（320–680px）

```
桌面（>1024px）                          窄屏（≤680px）
┌───────────────┬──────────┐            ┌──────────────────┐
│ 对话主区       │ 证据轨    │            │ 对话主区（全宽）   │
│ [问题]        │ 当前进度   │            │ [问题]            │
│ 摘要:按N步查询 │ 执行步骤▸  │            │ 摘要（一行，截断） │
│ [回答/澄清卡]  │ 执行记录▸  │            │ [回答/澄清卡]     │
│ [完整性提示]   │ 检索方式   │            │ [完整性提示]      │
├───────────────┴──────────┤            ├──────────────────┤
│ 输入区 · Assist 开关       │            │ 输入区 · Assist   │
└──────────────────────────┘            └──────────────────┘
                                        底部固定「回答依据」按钮 → 抽屉
                                        （执行步骤/执行记录/引用 同抽屉内折叠段）
```

规则：窄屏下 ClarificationCard 表单占满宽、问题列表堆叠；PlanPanel 在窄屏默认深度折叠（只显示摘要行）。
