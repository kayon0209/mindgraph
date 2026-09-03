# Tasks UI-G2：任务页设计规格（M4-A 编码前置，独立于 Assist UI-G）

> 依据实施方案：G1-E 通过后先执行独立 Tasks UI-G2，再实施 M4-A。feature flag 下提供，
> 不默认进入主导航（默认入口由 Product Signal 决定，当前 UNVALIDATED）。
> 视觉语言沿用 docs/ui/AGENT-UI-DESIGN-SPEC.md（Editorial Evidence Workspace），不重复。

## 1. 信息架构（TasksPage，flag 开启时经设置区/侧栏入口进入，非第六主导航）

```
TasksPage
├─ TasksHeader（标题 + 说明一句："后台核对任务：提交后在后台运行，完成后可导出证据包"）
├─ TaskComposer（提交新任务：document_query 输入、as_of 日期、约束折叠区）
├─ TaskList（我的任务，轮询刷新，倒序）
│  └─ TaskCard
│     ├─ 状态徽标 + 任务摘要（查询词 · 提交时间）
│     ├─ 进度行（queued=排队中 / running=步骤 n / 终态=结果一句话）
│     ├─ 折叠区：执行记录（步骤名/状态/耗时，脱敏）
│     ├─ 操作：running→取消；completed→查看 artifact；failed→重试（新任务）；终态→导出
│     └─ ArtifactPreview（completed 时）：命中数、冲突数、checksum、逐文档证据摘要
└─ TaskEmpty（无任务时的引导文案 + 一次示例任务建议）
```

窄屏（≤680px）：TaskList 全宽堆叠；折叠区默认更深折叠；操作按钮换行。

## 2. 状态矩阵（覆盖方案 UI 硬约束要求的任务态）

| 状态 | 触发 | 徽标样式 | 主文案（用户语言，禁内部术语） | 下一步操作 |
| --- | --- | --- | --- | --- |
| queued | 提交成功 | 中性灰 | 已排队，等待后台执行 | 取消 |
| running | 轮询到 status=running | 品牌橙（呼吸点+文字） | 正在核对 {n} 篇文档 | 取消 |
| completed | status=completed | 苔绿 | 核对完成：命中 {n} 篇，无版本冲突 | 查看/导出证据包 |
| completed_with_conflicts | result_state 含冲突 | 红系边框（唯一强强调） | 核对完成，但 {m} 篇文档存在多个同时生效版本，已停止给出结论 | 查看冲突版本 · 导出证据包 · 联系制度责任人 |
| completed_empty | 命中 0 | 中性灰 | 没有找到可见的制度文档 | 调整检索词重试 |
| failed | error_code 非空 | 红系 | 任务未完成：{一句话原因} | 重试（同参数新任务） |
| cancelled | status=cancelled | 中性灰 | 已按你的要求取消 | 重新提交 |

**规则**：completed_with_conflicts 是唯一允许高强调的状态（风险争夺注意力原则）；queued/running/普通 completed 一律低对比；状态不单靠颜色（图标+文字）；轮询失败显示"连接中断，正在重试"横幅而非把所有任务显示为失败。

## 3. 文案表（COPY-DECK 增补，禁 AI 味）

| 场景 | 文案 |
| --- | --- |
| 提交按钮 | 开始后台核对 |
| 提交中 | 已提交，排队中 |
| Idempotency-Key 冲突提示 | 相同任务已在执行（无需重复提交） |
| 执行记录折叠标题 | 执行记录 |
| 单步描述 | 检索相关制度 / 核对版本有效期 / 汇总证据包 |
| artifact 说明 | 证据包为私有存档，仅你可见；导出后可提交给制度责任人复核 |
| 导出按钮 | 导出证据包 |
| 空态引导 | 没有后台任务。试试：核对"差旅报销"相关制度在 {today} 的有效版本 |
| 轮询断连 | 连接中断，正在重试获取最新状态… |

## 4. SSE 事件 → UI 映射

M4-A 用轮询（无任务 SSE）。轮询节奏：running 时 3s，queued 时 5s，终态停止；页面失焦降频到 15s；手动刷新按钮常驻。`GET /agent/tasks/{id}` 响应字段 → TaskCard：status/progress_hint/result_state/error_code/artifacts[]。

## 5. 可访问性与响应式

- 徽标 aria-live="polite"；取消按钮双确认（原生 confirm 或两步按钮，防误触——后台任务取消不可撤销）。
- 键盘：Tab 顺序 = 提交区 → 列表 → 卡片操作；折叠区原生 details。
- 320px 无横向溢出；长查询词截断 + title 悬浮全文。

## 6. 设计条目 → M4-A 验收标准覆盖表

| M4-A 前端验收 | 本文档条目 |
| --- | --- |
| 状态、约束、脱敏执行记录、artifact 预览与证据导出 | §1 TaskCard 全字段；§3 文案表 |
| 状态不依赖纯颜色 | §2 规则 |
| 取消语义（协作式，不显示不存在的审批） | §2 操作列；§3 取消双确认 |
| feature flag 关闭时不出现 | §0 入口规则（非主导航） |
| 窄屏与键盘 | §5 |
| 冲突差异化呈现 | §2 completed_with_conflicts |
