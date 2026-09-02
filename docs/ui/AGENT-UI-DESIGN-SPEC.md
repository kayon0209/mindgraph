# Assist UI 设计规格（AGENT-UI-DESIGN-SPEC）—— UI-G 闸门交付物

> M2 编码前置设计闸门。信息架构与组件树基于当前 ChatPage 真实结构（`web/src/pages/ChatPage.tsx:1018-1120`）。
> 配套文档：`AGENT-UI-STATE-MATRIX.md`（全状态矩阵）、`AGENT-UI-COPY-DECK.md`（文案表）。
> TasksPage 不属于本闸门（推迟到 Gate G1 后）。

## 1. 设计原则（Editorial Evidence Workspace）

延续现有工作台语言：**小圆角、1px 边框、实色背景、sans 字体、lucide 图标**。明确不做：紫蓝渐变、发光、玻璃拟态等 AI 模板味。Assist 模式不是新皮肤，而是同一证据轨的增量状态。

五条总目标（继承自《Codex/Cursor 实施指令·UI 硬约束》）：

1. **Evidence first**：先理解结论/证据/版本/风险，不先看到内部技术名词。
2. **Progressive disclosure**：默认只显示当前步骤、结论与关键风险；计划、工具轨迹、耗时、token、原始状态码放折叠区。
3. **Governed actions**：冲突、无权限、证据不足、引用失败、待审批各用不同且稳定的视觉状态，不能都长成普通 error。
4. **Recoverability**：每个失败/等待状态都有明确下一步。
5. **Backward compatibility**：`AGENT_ASSIST_ENABLED=false` 时五个视图与 Chat 体验零变化（UI 不出现 assist 元素）。

## 2. 信息架构（Assist 开启后的 ChatWorkspace）

```
ChatWorkspace
├── 对话主区（chat-main）
│   ├── Turn（每轮）
│   │   ├── 用户问题
│   │   ├── AgentExecutionSummary（计划摘要一行，默认收起）→ 详见 §4.1
│   │   ├── 回答 / 澄清卡 ClarificationCard / 冲突卡 ConflictCard
│   │   ├── CitationIntegrityNotice（失败时的结论级提示）
│   │   └── 下一步操作（导出证据 / 重新生成 / 申请权限）
│   └── 输入区（含 Assist 开关，flag 同步给后端）
└── 证据与执行轨（evidence-rail，现结构保持）
    ├── 当前进度 TraceStep 组（3 步 → assist 下扩展为可数步骤）
    ├── PlanPanel（「执行步骤」，details 默认折叠）
    ├── ToolActivityPanel（「执行记录」，details 默认折叠）
    ├── 检索方式（现有 route-section 不变）
    ├── 引用来源（现有 citation-list + validity pill 不变）
    └── 引用校验与降级（fidelity/integrity 状态）
```

窄屏（<680px）：证据轨保持现有抽屉化方案（collapsed 窄边 + 展开覆盖），Plan/Tool 面板收纳进同一抽屉，不新增第二抽屉。

## 3. 组件树（新增组件，落点与命名）

| 组件 | 位置 | 职责 | 视觉基线 |
| --- | --- | --- | --- |
| `AgentExecutionSummary` | Turn 头部 | 一行摘要：「已完成 N 步 · 咨询了 M 份制度」，点击展开 PlanPanel | `text-xs muted`，与现有 rail-pinned 同级 |
| `PlanPanel` | evidence-rail 顶部 section | steps[] 渲染为带序号列表：名称（用户语言）+ 状态点 | 复用 `trace-steps` 样式 token，`details/summary` 折叠 |
| `ToolActivityPanel` | PlanPanel 下 | 每工具一条：显示名、目的、状态、耗时、脱敏摘要 | 复用 `route-decision-fold` 的 details 模式 |
| `ClarificationCard` | chat-main Turn 内 | 结构化问题卡（≤3 问）+「补充信息」提交 | 卡片语言复用 `citation-policy-meta` 色带；按钮复用 `.button.secondary` |
| `ConflictCard` | chat-main Turn 内（升级现有占位） | 「已停止生成」+ 可见冲突版本族 + 「转人工裁决」 | 红色系边框 + `AlertTriangle`，复用 `rail-degraded` 结构 |
| `CitationIntegrityNotice` | Turn 答案下方 | 「回答未通过引用校验」+ 查看证据 / 重新生成 | 复用 `rail-fidelity-warning` 样式升级为卡 |
| `LoopFallbackNotice` | Turn 答案上方 | 「已回到单次检索模式」一句话 + 原因 | 复用 `rail-degraded` 横幅样式 |

**不新增任何 UI 框架依赖**；全部组件基于现有 `Primitives.tsx` + `styles.css` token。

## 4. 关键交互流

### 4.1 计划与轨迹的渐进披露

`plan_created` 到达 → AgentExecutionSummary 显示「正在按 N 个步骤查询」；每个 `tool_call_started/finished` 更新 PlanPanel 对应步骤状态点；全部完成后摘要定格为「已完成 N 步」。PlanPanel 与 ToolActivityPanel 均 `details` 折叠，**不打断主区阅读**。

### 4.2 澄清恢复流（关闭流 → 新请求）

```
clarification_required 事件到达
  → chat-main 渲染 ClarificationCard（含 clarification_id 上下文提示「以上对话上下文已保留」）
  → completed(result_state=waiting_for_input) 到达，流正常关闭
  → 用户填写并提交
  → 前端发起新请求 POST /api/v1/assist/stream，body 带 resume_from=clarification_id + answers
  → 新 Turn 渲染，旧澄清卡保持已回答态（不复用 error retry 语义，无「重试」字样）
```

超时（expires_at 已过）：提交时后端返回 invalid_clarification → 卡片就地转为「补充信息已过期，请重新提问」，按钮回到普通提问。

### 4.3 冲突卡与降级

冲突：现有 `policy_conflict_detected` 事件已有展示；assist 下升级为 ConflictCard（列出可见版本族，数据来自 `mindgraph_get_policy_history` 同源查询），操作只有「导出证据包 / 联系制度责任人」，**没有**「忽略冲突继续生成」。

降级（loop_fell_back）：LoopFallbackNotice 显示一句原因 + 「本次按单次检索回答」，链接展开查看完整 PlanPanel。

## 5. 视觉原则摘要

- 步骤状态只用三种：进行中（空心点+呼吸）、完成（实心）、跳过/失败（×）；**不用颜色单独表义**，附文字。
- 所有 assist 新元素进入前须过 `prefers-reduced-motion` 检查（呼吸动画禁用）。
- aria：PlanPanel/ToolActivityPanel 的 `<details>` 天然可聚焦；ClarificationCard 提交按钮 `aria-live="polite"` 反馈提交结果。
- 工具摘要文案来自 COPY-DECK 的「执行记录」表，**禁止**出现 Agent Plan / Tool Calls / token / 内部类名。

## 6. 与 M2 验收标准的对应

见 `AGENT-UI-COPY-DECK.md` 末尾的「设计条目 → M2 验收标准」覆盖表（闸门通过判据：每条 M2 前端验收都能索引到本 spec 的具体条目）。
