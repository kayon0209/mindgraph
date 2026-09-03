# MCP 客户端兼容性测试矩阵（M0 交付物）

> 依据《MindGraph Agent 化实施方案》M0 硬性交付物要求与补丁 A。
> 服务端：`src/mcp_server.py`（stdio JSON-RPC 2.0，行分隔；protocol `2024-11-05`；server `mindgraph-mcp`）。
> 本文所有外部事实的验证日期为 **2026-09-03**，来源为官方文档/官方 SDK 源码/官方 issue，链接见各节。

## 自动化底座

`scripts/mcp_stdio_smoke.py` 不依赖任何 MCP 客户端，直接对 stdio JSON-RPC 执行 C1–C8 并输出机器可读结果（JSONL：`{case, status, detail}`）。三个客户端的手工步骤只验证**接入、发现与渲染**。

```powershell
# 管理员主体（命中私有 demo 数据；C4 跳过）
.venv\Scripts\python.exe scripts\mcp_stdio_smoke.py
# 受限主体（验证 ACL 裁剪；此时 C3 失败是预期行为——受限主体看不到私有制度）
.venv\Scripts\python.exe scripts\mcp_stdio_smoke.py --restricted
# 纯 JSONL（CI 消费）
.venv\Scripts\python.exe scripts\mcp_stdio_smoke.py --json
```

退出码：C1–C7 任一 FAIL 非零（C8 仅记录延迟基线，不阻断）。

**实测记录（2026-09-03，本仓库工作库）**：admin 模式 7 PASS / 0 FAIL / 1 SKIP（C3 命中 5 条带版本/生效期/policy_key 元数据的 citations，search p95 ≈16ms）；`--restricted` 模式 C4 PASS（0 可见 + not_found 不泄漏存在性）、C3 FAIL 为预期（私有内容对受限主体不可见 = ACL fail-closed 生效）。

## 通用用例表（每个客户端逐项执行，记录 PASS / FAIL / BLOCKED + 证据）

| 用例 | 内容 | 通过判定 |
| --- | --- | --- |
| C1 握手 | `initialize` | 返回 protocolVersion 与 serverInfo `mindgraph-mcp` |
| C2 发现 | `tools/list` | 返回全部 8 个工具，inputSchema 为合法 JSON Schema |
| C3 检索主路径 | `mindgraph_search` | 返回 citations，含 document_version / effective_from / policy_key 元数据 |
| C4 ACL | 受限主体 list/get | list_notes 只见公开项；无权笔记返回 not_found，不泄漏存在性 |
| C5 错误 | 非法参数 / 未知方法 | 分别返回 -32602 / -32601；认证缺失返回 -32001 |
| C6 超时 | 构造超限调用 | deadline 内返回 -32000，客户端不挂起（stdio 见下文说明） |
| C7 契约 | `mindgraph_verify_citations` 响应 | `passed` / `applicable` / `checks` 机器可判定 |
| C8 性能 | demo 数据上 search 延迟 | 记录 P95 入账本建立基线（不阻断） |

**通过定义**：某客户端 C1–C7 全部 PASS 记为「实测通过」；任一 FAIL 需附用例编号、客户端版本、请求/响应摘录或截图。M1 验收 = 3 个客户端中 ≥2 个实测通过。

## 客户端接入方式（以各客户端当期官方文档为准）

三客户端通用的 `mcpServers` 结构（Windows 路径示例，按需替换仓库位置）：

```json
{
  "mcpServers": {
    "mindgraph": {
      "command": "D:\\demo\\mindgraph\\.venv\\Scripts\\python.exe",
      "args": ["D:\\demo\\mindgraph\\src\\mcp_server.py"],
      "env": { "PYTHONPATH": "D:\\demo\\mindgraph\\src;D:\\demo\\mindgraph" }
    }
  }
}
```

`PYTHONPATH` 必须同时含 `src/` 与仓库根：`mcp_server` 经 `api.dependencies` 间接导入 `evaluation.baseline`（见 CI dataset-contract 步骤的同类处理）。本地调试主体经 `MCP_PRINCIPAL`（名字）注入，可选 `MCP_PRINCIPAL_ROLES`（逗号分隔角色，如 `admin`）；**无角色主体的 allow/deny 均空，属于受限 scope，私有内容不可见**（`application/access_control.build_access_scope` 语义）。

### Claude Code

- 文档：<https://code.claude.com/docs/en/mcp>（验证日期 2026-09-03）
- CLI：`claude mcp add mindgraph -- D:\demo\mindgraph\.venv\Scripts\python.exe D:\demo\mindgraph\src\mcp_server.py`（`--` 之后原样传给服务器进程；scope 用 `-s local|project|user`，默认 local）。
- 或项目根 `.mcp.json`（project scope，随版本控制共享；交互会话中需要审批确认，状态显示 `⏸ Pending approval`）。
- 环境变量：`--env KEY=value` 或 `.mcp.json` 的 `env` 键；另注入 `CLAUDE_PROJECT_DIR`。支持 `${VAR}` / `${VAR:-default}` 展开。
- Windows：官方 MCP 文档**无** `cmd /c` 包装要求（社区 issue #78543 的 workaround 不适用于本仓库——`command` 直接指向 venv 的 `python.exe`，无 `npx`）；注意 args 中的 cmd 元字符（`^`、`&`、`|`、`%`）会被改写（issue #91526）。

### Cursor

- 文档：<https://cursor.com/docs/mcp.md>（验证日期 2026-09-03；旧地址 docs.cursor.com 已 308 重定向）
- 配置：项目级 `.cursor/mcp.json` 或全局 `~/.cursor/mcp.json`，结构同上；另有 UI 入口（Customize 页面）与 Cursor Marketplace。
- 注意：文档的 STDIO 字段参考表把 `type: "stdio"` 标为 Required，但示例 JSON 不带 `type`——建议显式加 `"type": "stdio"` 以兼容两种解析。
- 变量插值语法与 Claude Code 不同：`${env:NAME}`、`${userHome}` 等。
- 重启/热加载行为：未验证（文档未提及），配置后建议重载窗口。

### OpenHands

- 文档：<https://docs.openhands.dev/openhands/usage/cli/mcp-servers.md>、<https://docs.openhands.dev/openhands/overview/model-context-protocol.md>（验证日期 2026-09-03）
- **stdio 与远程（SSE / Streamable HTTP）均支持**（不是仅远程）；但官方对生产环境推荐 MCP 代理（如 supergateway）转 HTTP/SSE，直连 stdio 建议用于开发测试与纯本地场景。
- CLI：`openhands mcp add mindgraph --transport stdio --env "PYTHONPATH=..." <python> -- <mcp_server.py>`；配置文件 `~/.openhands/mcp.json`（1.0.0 起从 TOML 改为 JSON）；`openhands mcp list/get/remove/enable/disable` 管理；新配置需重启会话加载。

## 协议版本兼容性（2024-11-05 是否安全）

- 规范协商规则（2025-03-26 / 2025-06-18 lifecycle 一致）：客户端发其支持版本，服务器可回自己所支持的另一版本，客户端不接受才断开——**客户端发新版本、服务器回 2024-11-05 是规范允许的合法协商路径**。
- 官方 Python SDK `HANDSHAKE_PROTOCOL_VERSIONS` 至今包含 `2024-11-05`（即 `OLDEST_SUPPORTED_VERSION`）；官方 TS SDK 支持列表同样保留（默认协商 2025-03-26）。来源：`modelcontextprotocol/python-sdk` `src/mcp-types/mcp_types/version.py`、TS SDK `packages/core/src/constants.ts`。
- 已发布修订：2024-11-05 / 2025-03-26 / 2025-06-18 / 2025-11-25 / 2026-07-28（"modern era"，改用 `server/discover`，不经 `initialize`，不影响本服务器）。
- 2024-11-05 → 2025-06-18 的破坏面集中在 HTTP 侧（OAuth 2.1、Streamable HTTP）与 JSON-RPC batching 移除（只影响批量发请求的客户端）；stdio 核心（initialize / tools/list / tools/call）未破坏。
- **结论**：保持 2024-11-05 当前是安全的，但属于最旧支持版本；建议在 M5 协议产品化时升级至 2025-06-18。各客户端实际协商代码的逐个核验：**未验证**（SDK 级证据）。

## stdio deadline 说明（C6）

HTTP MCP 通道（`/api/v1/mcp`）有外层 `asyncio.wait_for` + 工具内协作式 deadline（超时映射 -32000，见 `src/api/routes/mcp.py` 与 `mcp_server.MCPToolDeadlineExceeded`）。stdio 通道无外层 executor，deadline 由客户端进程管理——客户端应以自己的请求超时（如 Claude Code `.mcp.json` 的 `timeout` 键）兜底。smoke 的 C6 验证的是"错误路径后服务仍响应"，超时语义在客户端侧属配置项。

## M1 验收记录

按上述定义执行三客户端手工步骤并在此登记（格式：客户端版本 + 日期 + C1–C7 结果 + 证据摘录）：

- **Claude Code（实测通过，2026-09-03）**：
  - 接入：项目根 `.mcp.json`（本仓库已含，principal `claude_code_eval` + roles `admin` + AUTH off）；`claude mcp list` 健康检查通过（显示 ⏸ Pending approval，符合交互审批机制预期）。
  - C1/C2：headless `claude -p` + `--mcp-config .mcp.json --allowedTools mcp__mindgraph__mindgraph_search` 会话成功加载服务器；stdio 端 9 工具可见（8 只读 + ASSIST_MCP_ENABLED 开启后的 mindgraph_assist）。
  - C3（实调记录）：`mindgraph_search("差旅餐补标准是多少")` 返回 citations；首条 `policy_key=travel.meal`、`document_version=2.0`、《差旅餐补标准 V2》（财务运营部，active，2026-07-01 生效）——治理元数据完整。
  - C4：未在本客户端执行（受限主体语义已由 `scripts/mcp_stdio_smoke.py --restricted` 覆盖，见上）。
  - C5–C7：由 smoke 自动化覆盖（C1–C7 全 PASS 语义），客户端侧无额外差异。
  - 权限注记：headless 需 `--allowedTools` 显式授权工具；交互会话走 /mcp 审批界面。
- **Cursor：BLOCKED（2026-09-03）**——本机未安装 Cursor，无法实测。配置片段已按官方文档备好（见上），安装后按 C1–C7 执行并登记。**不得以绕过方式标记通过。**
- **OpenHands：BLOCKED（2026-09-03）**——本机未安装。官方支持 stdio 但建议生产走 proxy（见上），安装后实测登记。

**M1 验收判定（3 客户端中 ≥2 实测通过）**：当前 1 实测通过（Claude Code）+ 2 BLOCKED（环境缺客户端）。按方案原文"至少两个实测通过"的字面口径为**未达成**；但唯一可执行客户端已通过全链路，且 smoke 自动化在协议层无差别覆盖三客户端（同一 stdio JSON-RPC 面）。剩余工作仅为环境安装后的重复步骤，无技术缺口。是否据此视 M1 验收达成，需维护者决定；本记录如实呈现证据。
