# MCP 集成指南（M1）

> 面向在 Claude Code / Cursor / OpenHands 等客户端中使用 MindGraph 证据工具的场景。
> 客户端版本兼容矩阵与 C1–C8 用例定义见 `docs/MCP-CLIENT-COMPATIBILITY.md`（外部事实验证日期 2026-09-03）。

## MindGraph MCP 提供什么

MindGraph 把制度知识库暴露为**受治理的只读证据工具**，不是聊天机器人。每个工具调用都经过 ACL（按主体 workspace/department/角色裁剪）、审计（`access_audit`）、协作式超时；版本冲突、权限不足、证据不足都返回机器可判定的状态，绝不编造。

### 工具清单（8 个，全部只读）

| 工具 | 用途 | 关键返回 |
| --- | --- | --- |
| `mindgraph_search` | 语义检索制度证据片段 | citations（含 document_version / effective_from / effective_to / policy_status / policy_key / owner） |
| `mindgraph_list_notes` | 当前主体可见的笔记台账 | items + total |
| `mindgraph_get_note` | 单篇笔记详情（含 confirmed 关系） | note + governance + relations |
| `mindgraph_list_relations` | 双端可见的 confirmed 关系 | relations |
| `mindgraph_evaluation_overview` | 评测运行概览 | runs |
| `mindgraph_get_policy_history` | 按 policy_key 查版本族（M1 新增） | versions[]（只含当前主体可见版本） |
| `mindgraph_concept_gaps` | 高频未收录概念缺口（M1 新增） | gaps + total（聚合，无提问原文） |
| `mindgraph_verify_citations` | 校验回答文本中 [citation-N] 标注完整性（M1 新增） | passed / applicable / checks（不验证语义支持） |

另有 `mindgraph_assist`（受治理问答，返回机器可判定 verdict），默认关闭，需 `ASSIST_MCP_ENABLED=true`。

**写工具（M5-A 起，三个工具各自独立开关）**：

- `mindgraph_save_artifact`（低风险，`AGENT_WRITE_TOOLS_ENABLED`）：把回答+证据快照保存到当前主体的**私有空间**（保存草稿不等于发布；无共享/发布路径）。幂等：相同 `idempotency_key` + 相同内容重复保存返回同一存档；同键不同内容被拒绝（不静默覆盖）。
- `mindgraph_submit_evidence_feedback`（中风险，`AGENT_FEEDBACK_TOOL_ENABLED`）：对一次已完成回答提交质量反馈。**preview/submit 两段确认**：先 `action=preview` 取回答摘要展示给用户，确认后 `action=submit`。一回答一反馈（重复返回 already_submitted）；`not_helpful` 按既有规则进 bad_cases。
- `mindgraph_propose_relation`（高风险，`AGENT_PROPOSE_RELATION_TOOL_ENABLED`）：提出两个笔记之间的关系候选。**preview/submit 两段确认**（preview 展示两端标题/类型/影响范围，确认语义不缓存）；**只创建 proposed**——人工审核确认后才进入图谱检索扩展，该路径绝不自动；source/target/evidence 三端都必须可见（不可见统一 not found）；同一对笔记任意方向/状态均去重。

每个工具 tools/list 过滤 + handler 内 fail-closed 双门控；回滚均为置回 false 即隐藏，已写数据按各自语义保留（存档不删、反馈不撤、proposed 留在审核队列）。

### 治理语义（调用方必读）

- **版本冲突 fail-closed**：同一 policy_key 在查询日期存在多个有效版本时，问答返回 `conflicting_evidence`，不生成结论；用 `mindgraph_get_policy_history` 找到版本族后交人工裁决。
- **权限不足不泄漏存在性**：无权笔记返回 `not_found`；`mindgraph_get_policy_history` 只返回可见版本，不返回隐藏数量。
- **引用完整性 ≠ 语义支持**：`mindgraph_verify_citations` 只检查标注格式/越界/重复/未用引用；"结论是否被证据支持"属于 answer evaluation 的 claim-support 指标，不要把 `passed=true` 当作语义正确的证明。

## 配置

### 前置条件

```powershell
# 1. 已构建 MindGraph 索引（demo vault 或你自己的 vault）
.venv\Scripts\python.exe scripts\sync_demo_vault.py   # 或 scripts\sync_vault.py --vault <path>
# 2. 冒烟自检（不依赖客户端）
.venv\Scripts\python.exe scripts\mcp_stdio_smoke.py
```

### 三客户端通用 `mcpServers` 片段

```json
{
  "mcpServers": {
    "mindgraph": {
      "type": "stdio",
      "command": "D:\\demo\\mindgraph\\.venv\\Scripts\\python.exe",
      "args": ["D:\\demo\\mindgraph\\src\\mcp_server.py"],
      "env": { "PYTHONPATH": "D:\\demo\\mindgraph\\src;D:\\demo\\mindgraph" }
    }
  }
}
```

> `PYTHONPATH` 必须同时含 `src/` 与仓库根（`mcp_server` 间接导入 `evaluation.baseline`）。
> macOS/Linux 把分隔符换成 `:`，路径换成对应位置。

### Claude Code

项目共享（`.mcp.json`，需交互审批）或个人（CLI）：

```powershell
claude mcp add mindgraph -- D:\demo\mindgraph\.venv\Scripts\python.exe D:\demo\mindgraph\src\mcp_server.py
claude mcp add -s project mindgraph -- .venv\Scripts\python.exe src\mcp_server.py   # 团队共享
```

### Cursor

项目 `.cursor/mcp.json`（或全局 `~/.cursor/mcp.json`）——用上面的通用片段即可（建议保留 `"type": "stdio"`）。

### OpenHands

```powershell
openhands mcp add mindgraph --transport stdio --env "PYTHONPATH=D:\demo\mindgraph\src;D:\demo\mindgraph" D:\demo\mindgraph\.venv\Scripts\python.exe -- D:\demo\mindgraph\src\mcp_server.py
```

官方对生产环境推荐经 MCP 代理转 HTTP/SSE；本地开发直连 stdio 即可（详见兼容性文档）。

## 本地调试主体

stdio 服务无 API Key 认证，主体经环境变量注入（仅限本地调试）：

```
MCP_PRINCIPAL=your_name        # 名字；非空即 authenticated
MCP_PRINCIPAL_ROLES=admin      # 可选，逗号分隔角色。admin → 全量可见
```

**注意**：无角色主体的 allow/deny 均空 → 属于受限 scope，**私有内容不可见**（这是 ACL 语义，不是故障）。企业部署请走 HTTP MCP（`/api/v1/mcp`，API Key 认证 + 审计 + 速率限制）。

## 推荐的 Agent 使用模式

```text
用户提问"差旅餐补标准是多少"
  ↓ mindgraph_search(query, top_k=5)
返回 citations：差旅餐补标准 V2 / effective_from 2026-07-01 / policy_key travel.meal
  ↓（检查版本元数据，发现可能存疑）
mindgraph_get_policy_history(policy_key="travel.meal")
返回版本族 [v1(已归档), v2(现行)] → 无冲突，v2 生效
  ↓ 生成回答并用 [citation-N] 标注
mindgraph_verify_citations(answer, citation_ids)
passed=true → 输出；passed=false → 修正标注或只输出证据
```

多有效版本冲突时：停止生成，向用户展示冲突版本族并请求人工裁决（`conflicting_evidence` + `human_review`）。
