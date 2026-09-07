# ADR-005：M6 企业部署与运维硬化（单节点企业版 profile）

日期：2026-09-03 · 状态：已接受 · 关联：ADR-003/004

## 决定

M6 本轮按**单节点企业版 profile** 实施，多节点版明确不做（见部署边界）。范围来自方案 M6 清单中"当前架构下可验证"的子集；多 worker 持久层、远程 MCP 的 Streamable HTTP/OAuth、tenant SSO 属多节点 profile，延后独立立项。

## 部署 profile

| 维度 | 单节点企业版（本轮） | 多节点版（不做，声明边界） |
| --- | --- | --- |
| 实例 | SQLite WAL 单实例 + 单 API 进程 + 单 worker 线程 | 需外部队列/DB，另立项 |
| MCP | stdio（本地）+ HTTP JSON-RPC（同机/内网，API Key/Bearer） | 远程 Streamable HTTP + OAuth |
| 认证 | API Key / OIDC Bearer（现有 AUTH_MODE 四模式） | SSO/身份映射、管理员策略 |
| 会话 | 服务端会话 + localStorage 缓存（M3-E） | — |
| 部署 | Docker Compose（api+web）或裸 venv；8020 端口例 | K8s/HA |

## M6 硬化项（本轮实施拆分）

1. **M6-1 MCP 协议版本协商**：initialize 按规范 echo 客户端请求的版本（在其为我们支持集的交集时），并把支持集扩至 `2024-11-05 / 2025-03-26 / 2025-06-18`；`MCP_SUPPORTED_VERSIONS` 契约化 + smoke 断言升级。背景：现固定回 2024-11-05——虽是规范允许的合法路径（服务可回自己所支持版本），但 echo 交集是最新规范推荐的协商方式，且 2025-06-18 已是官方 SDK 默认协商目标。
2. **M6-2 备份/恢复演练（故障注入）**：用真实运行库做 backup → 篡改 → restore → 数据一致性断言的 drill 脚本，产出机器可读结果；升级兼容验证（v8 库经全链 initialize 到当前版本且数据不变——已有迁移测试，补端到端 drill）。
3. **M6-3 SLO + 运行手册 + 安全文档**：`docs/DEPLOYMENT-ops.md`——启动/停机/回滚、备份策略、SLO（health 响应、SSE P95、MCP 工具 P95、任务吞吐，基准数据已入账本）、单实例边界声明、secrets 清单（.env 管理 + key 轮换流程）、审计导出。既有 `docs/DEPLOYMENT.md` 为安装文档，运行手册另立避免混淆。
4. **客户端兼容矩阵终稿**：MCP-CLIENT-COMPATIBILITY.md 更新为 12 工具口径 + 协商行为；Cursor/OpenHands 保持 BLOCKED（未安装，不得冒充）；Claude Code 实测记录复核。

## SLO（工程目标，非对外承诺）

- `/api/v1/health`：P95 < 50ms（本地）；readiness 含 DB/索引检查
- SSE chat 首字（TTFT）：P95 < 8s（真实 provider 下；受 LLM 主导）
- MCP 工具调用：P95 < 2s（本地索引；smoke C8 已建基线 16-20ms）
- 任务吞吐：单 worker ≥ 50 tasks/min（矩阵实测 869/s 的编排层，端到端受检索限制）
- 可用性：单节点 99%（进程级）；不宣称 HA

## 威胁模型（增量，M6 范围）

| 威胁 | 缓解 | 状态 |
| --- | --- | --- |
| .env 密钥进版本库 | gitignore + 提交事故教训（历史已清理）；key 轮换流程入手册 | 流程 |
| 备份文件含敏感内容 | 备份目录在 data/backups（不进 git）；恢复演练验证完整性 | M6-2 |
| HTTP MCP 被内网横向调用 | API Key/Bearer 必填（AUTH_MODE≠off 时）；限流中间件 | 既有 |
| 协议降级攻击（客户端声称旧版本） | 版本协商仅 echo 支持集内版本；未支持版本回退到我们的最新支持版本 | M6-1 |
| 升级中断导致 schema 半迁移 | initialize 幂等 + 失败不抬版本（既有测试）；M6-2 端到端演练 | 既有+drill |
| 日志泄漏 secrets | M0 脱敏层（既有）；backup drill 不打印库内容 | 既有 |

## 非目标（本轮明确不做）

远程 MCP transport、OAuth、SSO/tenant 身份映射、多 worker/外部队列、K8s 部署、正式生产规模声明（无真实部署证据，只标"工程预览"——Product Signal UNVALIDATED 不阻断工程验收，但约束对外表述）。
