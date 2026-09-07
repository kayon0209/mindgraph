# MindGraph 运行手册与安全文档（M6，单节点企业版 profile）

> 依据 ADR-005。安装步骤见 `docs/DEPLOYMENT.md`；本文是**运行/应急/安全**手册。
> 对外表述边界：单节点部署的工程预览（Product Signal UNVALIDATED）——可称
> "通过企业级工程验收"，不可称"已验证生产规模/企业采用"。

## 1. 部署边界（硬约束）

- **单实例**：SQLite WAL + 单 API 进程 + 单任务 worker 线程（`TASK_WORKER_ENABLED=true` 时由 lifespan 拉起，双启动防重）。多实例/多 worker 部署在当前持久层下**不安全**，需先升级外部队列/DB（ADR-004/005）。
- 数据目录：`data/product/`（库）、`data/mindgraph_indexes/`（版本化索引）、`data/backups/`（备份，不进 git）。
- 端口：默认 8000（`.env` 可改；避免与其他项目冲突）。

## 2. 启动 / 停机 / 回滚

```powershell
# 启动（推荐）
powershell -ExecutionPolicy Bypass -File scripts\start-dev.ps1
# 或显式端口
.venv\Scripts\python.exe -m uvicorn api.main:app --app-dir src --host 127.0.0.1 --port 8020

# 停机：Ctrl+C（lifespan 依次停 worker 线程 → 关库 WAL checkpoint）
# 任务线程为 daemon + stop event，最长 poll 间隔内退出（默认 2s + 任务步长）
```

**回滚顺序**（方案 §12，全部为 flag 置 false，无破坏性降级）：
1. `AGENT_PROPOSE_RELATION_TOOL_ENABLED` → `AGENT_FEEDBACK_TOOL_ENABLED` → `AGENT_WRITE_TOOLS_ENABLED`（写工具逐个隐藏，已写数据保留各自语义）
2. `AGENT_TASKS_ENABLED` + `TASK_WORKER_ENABLED`（路由不挂载、线程不启动；任务表保留）
3. `AGENT_ASSIST_ENABLED`（assist 面 404）
4. `CONVERSATION_PERSISTENCE_ENABLED`（会话路由不挂载）
5. 数据库新表保留但不读写——**不做 schema 降级**，回退代码版本前先确认旧代码可读新 schema（additive 迁移保证只多列不缺列）。

## 3. 备份策略与恢复（M6-2 已演练验证）

| 项 | 值 |
| --- | --- |
| 内容 | SQLite 库 + 检索索引 + 知识库 + 环境配置（tar.gz） |
| 频率 | `BACKUP_INTERVAL_HOURS`（默认 24h；服务内定时）+ 手动 `scripts/backup.py` |
| 保留 | `BACKUP_RETENTION_DAYS`（默认 30 天自动清理） |
| 恢复 | `python scripts/backup.py --restore <file>`（恢复前自动再备份当前数据，防恢复失败丢数据） |

**故障注入演练**（2026-09-03，`scripts/run_backup_restore_drill.py`，全 PASS）：
- schema 破坏（DROP notes）→ 恢复后表集/行数/指纹完全一致；
- 数据破坏（DELETE 207 行 query_logs）→ 从备份档案还原后全部表行数与基线一致；
- 升级兼容：v8 形态库 → `initialize()` 全链升级到当前 schema（v12），notes 数据不丢。
建议**每次 schema 升级发布前**重跑该 drill。

## 4. SLO（工程目标，非对外承诺）

| 指标 | 目标 | 现状证据 |
| --- | --- | --- |
| `/health` P95 | < 50ms | 本地实测即时返回 |
| Chat TTFT P95 | < 8s | 受 LLM 主导（Gitee AI 真实调用验证通过） |
| MCP 工具 P95 | < 2s | smoke C8 实测 ≈16ms（本地索引） |
| 任务吞吐 | ≥50 tasks/min/worker | 编排层基准 869/s（`evaluation/results/m2_latency_baseline_*.json`） |
| 可用性 | 单节点 99%（进程级） | 不宣称 HA |

## 5. Secrets 管理与 Key 轮换

- 密钥只存在于 `.env`（git 忽略：`.env`、`.env.backup*`）。**轮换记录**：2026-09-03 曾发生 `.env` 备份误提交事故——已从全部 git 历史根除（filter-branch + 对象库验证 0），但涉事 key 建议轮换；**事故后规则：备份文件一律匹配 `.env.backup*` 模式，禁止 `git add -A` 前不检查新增文件**。
- 日志侧防线：`infrastructure/logging_config.py` 按键名与形态（sk-/bearer/zhipu jwt）双路脱敏（M0，测试锁定）。
- 审计侧防线：工具参数 redact_fields（title/comment/evidence 等）不进 access_audit 明文。

## 6. 认证模式

`AUTH_MODE`：`off`（仅本地开发，会话/审计主体为 local-development）/ `api_key`（`X-API-Key` 头）/ `bearer`（OIDC）/ `demo`（匿名公开内容）。企业部署至少 `api_key`；MCP HTTP 通道复用同一认证边界。

## 7. 可观测性

- 结构化 JSON 日志（`logging_config.py`；慢查询阈值 `SLOW_QUERY_THRESHOLD_MS`）；
- `access_audit`：全部通道（REST/MCP/Assist/工具）决策留痕；M5-A 写工具有独立 action 面；
- `query_logs`：chat/assist 轮回放（assist 渠道 `prompt_version=assist-agent-v1`，含 tool_calls_executed/fallback_reason）；
- 评测账本 `evaluation_runs`：含 citation_fidelity/acl_leakage 等治理指标（两轮 live 基线已入账）；
- 审计导出：`GET /api/v1/bad-cases/export`（CSV）。

## 8. MCP 客户端兼容矩阵（2026-09-03 终稿）

服务器：stdio JSON-RPC 2.0，行分隔；协议版本协商支持集 `2024-11-05 / 2025-03-26 / 2025-06-18`（echo 客户端请求版本，未知回退 2025-06-18）；server `mindgraph-mcp 3.2.0`。另有受认证 HTTP JSON-RPC 工具通道，但它不是 Streamable HTTP/OAuth transport。工具面：12 个（5 旧只读 + 3 治理只读 + 1 assist + 3 受控写，各自独立 flag）。

| 客户端 | 状态 | 证据 |
| --- | --- | --- |
| Claude Code | **PASS**（实测） | `.mcp.json` 健康检查过；headless `--mcp-config + --allowedTools` 实调 `mindgraph_search` 返回 policy_key/document_version 治理元数据（2026-09-03）；项目级 `.mcp.json` 走交互审批，headless 需 `--allowedTools` |
| Cursor | **BLOCKED**（未安装） | 配置片段已备（见 `docs/MCP-INTEGRATION.md`，官方文档核实 2026-09-03）；安装后按 C1–C7 执行登记 |
| OpenHands | **BLOCKED**（未安装） | 官方支持 stdio（生产建议 proxy，已核实）；安装后实测登记 |

自动化底座：`scripts/mcp_stdio_smoke.py` C1–C8 全 PASS（admin 模式 8 PASS；`--restricted` 模式 C4 PASS 证明 ACL fail-closed）。**对外声明规则：声称支持的每个客户端必须逐一实测通过；BLOCKED 不得冒充。**

## 9. 已知限制（如实声明）

- 单实例边界（§1）；无 SSO/tenant 身份映射（多节点 profile）；远程 MCP transport（Streamable HTTP/OAuth）未实施；
- 产品信号 UNVALIDATED：无设计伙伴、无真实用户会话数据——影响优先级判断与对外表述，不影响工程验收（双轨门禁）。
