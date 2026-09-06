# ADR-004：企业级后台任务基础（M4-A 单任务垂直切片）

日期：2026-09-03 · 状态：已接受 · 关联：ADR-003（governed Agentic Evidence Layer）

## 决定

实现一个受限的企业级后台任务类型：**「批量制度/文档核对 → 生成 private evidence bundle」**，作为 at-least-once 可恢复任务的垂直切片。不做任意 goal 执行、不做代码执行/浏览器操作/多 Agent/外部发布/MCP 写工具。

## 代表性企业任务（G1-E 条件 2，合成/脱敏 fixture 定义）

### 任务 A：批量制度核对（本轮实现）
- **输入**：`document_query`（检索词或 vault 路径列表）、`as_of`（版本判定日）、`constraints`（top_k 上限、是否含历史版本）
- **输出**：private artifact（证据包：每篇命中文档的 citation 快照 + 版本/生效期/状态 + 冲突列表 + checksum）
- **权限场景**：owner 主体提交；任务执行中每个文档单独按当前 ACL 裁剪——历史曾可见不豁免（与 M3-E 会话回放同一语义）
- **冲突场景**：命中 policy_key 在 as_of 有多个有效版本 → 该文档条目标记 `conflicting_evidence`，任务整体降为 `completed_with_conflicts`，不生成确定性结论段落
- **失败场景**：检索不可用（重试后失败 → 任务 failed + error_code）；文档全部不可见（→ completed_empty，不是错误）

### 任务 C：目录增量同步 → 版本变化摘要（后端已实现，当前 UI 不暴露）
- 输入：`directory_root`（可选；提供时必须位于 worker `allowed_roots`）、`since` 时间戳；不提供目录时使用当前库快照
- 输出：按当前主体 ACL 裁剪后的新增/变更/归档文档清单 private artifact
- 目录模式只读源文件，不写回 ID、不剪枝；会更新产品库笔记快照并写 `connector_syncs` 审计记录
- 当前 `TasksPage` 只提交任务 A；任务 C 仅能经受认证 API 显式提交，避免界面宣称尚未提供的目录选择与授权流程

## 数据模型（schema v11）

`agent_tasks`：principal_id、workspace/department scope、可空 conversation_id、task_type、constraints_json、status（queued/running/completed/completed_with_conflicts/completed_empty/failed/cancelled）、result_state、idempotency_key（UNIQUE(principal_id, idempotency_key)）、lease_owner、lease_expires_at、attempt_count、cancel_requested_at、error_code/message、created/updated_at。不预建 approval 字段——首个需审批写工具（M5-A）时增量加。

`artifacts`：owner principal_id、task_id、kind、title、content_json、visibility=private（唯一允许值）、evidence_snapshot_json、citations_json、checksum、created/updated_at。共享/发布字段留待 M5/M6 增量。

## Worker 语义（SQLite-backed at-least-once）

- **claim**：短事务 `UPDATE ... SET status='running', lease_owner=?, lease_expires_at=? WHERE task_id=(SELECT ... WHERE status='queued' OR (status='running' AND lease_expires_at < now) ... LIMIT 1)`；单实例单 worker，启动时检测同机重复 worker（pid 文件/端口语义在部署文档），不假称多实例安全。
- **续租**：长步骤间检查剩余时间，低于阈值续租；`TASK_LEASE_SECONDS=120` 默认。
- **重启恢复**：只恢复 lease 已过期的 running 任务（重新入队或按 attempt_count 决定重试/失败）；不把所有 running 盲目重跑。
- **幂等**：任务级由 UNIQUE(principal_id, idempotency_key) 保证（重复提交返回同一 task）；步骤级副作用（artifact 写入）按 task_id 幂等（先查后写 + checksum）。
- **取消**：协作式——cancel 只设置 cancel_requested_at；worker 在每个步骤边界检查，不再启动新步骤；已启动步骤跑完（无硬中断）。
- **重试**：attempt_count < TASK_MAX_ATTEMPTS 才重试；超限 → failed。

## 威胁模型（M4-A 范围）

| 威胁 | 缓解 |
| --- | --- |
| 越权读：提交者构造他人 workspace 的查询 | 执行时逐文档按提交者当前 ACL 裁剪（非提交时快照）；不可见条目不出现、不计数泄漏 |
| 跨主体/租户访问任务或 artifact | 列表/详情/下载一律 owner 校验，统一 not found，不暴露存在性（与 conversations 同语义，测试覆盖） |
| 伪造 idempotency_key 抢占他人任务 | 唯一约束含 principal_id——不同主体相同 key 互不冲突；同主体重复提交幂等返回原任务 |
| prompt/参数注入改变任务行为 | constraints 是结构化 JSON（白名单字段校验），不是自由文本 prompt；任务类型只有一种固定流程 |
| artifact 泄漏（唯一 private 但被枚举） | artifact ID 不可猜测（uuid hex）+ owner 校验；无列表跨主体枚举接口 |
| 敏感日志沉淀 | tool/step 轨迹只记步骤名、状态、耗时、checksum；不记检索词全文与证据正文（沿用 M2 assist 落库策略） |
| worker 崩溃留下僵尸任务 | lease 过期 + 重启恢复；cancel 在恢复时同样生效 |
| 迁移失败后继续启动 | v10→v11 additive；失败测试覆盖（migration 失败 → 启动中止） |

## API（全部 /api/v1/mindgraph，flag 门控）

POST `/agent/tasks`（头 `Idempotency-Key`）· GET `/agent/tasks`（cursor 分页，owner 过滤）· GET `/agent/tasks/{id}`（详情+轨迹+artifacts 元数据）· POST `/agent/tasks/{id}/cancel`。轮询详情，不做任务 SSE（事件持久化回放后才有）。M4-A 无 approve/reject——契约保留 ToolSpec 层的 approval policy 扩展点（M5-A 接入）。

## 回滚

`AGENT_TASKS_ENABLED=false` → 路由不挂载、worker 不启动、TasksPage 不渲染；数据库表保留不读写；不破坏性降级。回滚顺序遵循方案 §12。

## 工程验收（G1-R，样本无关）

must-pass：正常完成、重复提交幂等、并发 claim 唯一、取消（含运行中取消）、超时、进程中断后 lease 恢复、ACL/tenant deny、冲突降级、引用失败降级、迁移/备份/恢复、flag 回退；≥20 次固定基准运行记录吞吐/P50/P95/资源。真实使用价值记 Product Signal P1=UNVALIDATED。
