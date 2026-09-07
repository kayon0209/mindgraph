# ADR-003：受治理的 Agentic Evidence Layer（M0/M1）

## 决定

保留现有的证据优先、确定性治理链路，并把面向 agent 的交付面（Assist）定义为**受治理的只读交付层**，而不是自主 agent。

- 现有 `ChatService` 继续承担唯一的生成编排入口：路由、检索、ACL、冲突拦截、审计、SSE 事件与落库仍然在应用层内完成。
- 新增的 `Assist` 只做契约封装：
  - REST：`/api/v1/assist`（M1）
  - SSE：`/api/v1/assist/stream`（M1）
  - MCP 只读工具：`mindgraph_assist`（flag 门控，M1 可选）
- `Assist` 返回机器可判定的 `verdict`，不引入函数调用、不引入写回、不引入第二套治理逻辑。
- `M0` 只做治理/契约/审计/测试加固，不改变现有运行时行为；`M1` 仅在显式开关开启时暴露 Assist 面。

## 背景

本仓库没有独立的 M0/M1/M2 规划产物；当前最接近的约束来自：

- `docs/ADR-001-agent-ready-evidence-layer.md`：强调“不是自主 agent”；
- `docs/ARCH-REVIEW-multi-agent-multi-hop-2026-08-28.md`：建议用确定性的引用一致性检查，而不是多智能体编排；
- `src/api/dependencies.py`：`ServiceContainer` 已经把 REST 与 MCP 收敛到同一应用服务层。

因此，本 ADR 把“agent 面”定义为**可治理、可审计、只读、复用现有应用层**的交付面，而不是新的一套编排引擎。

## 约束

1. **不新增 provider function calling 面。** 现有 provider 保持 text-in/text-out。
2. **M0/M1 不新增表/列。** 如需持久化，优先使用现有 JSON/blob 列；任何真正的写回/账本化，留到 M2 及以后。
3. **REST / MCP / Chat / Assist 必须复用同一应用层。** 不允许 HTTP 自调用或 MCP 自调用。
4. **默认关闭。** 新能力一律 flag-gated，off 态必须与当前行为字节兼容。
5. **引用保真检查先警告，后硬化。** M0 只在 trace / evaluation 中提示，不直接阻断回答。

## M0 范围（治理与契约基线）

- 冻结 SSE 事件名、信封键、`stream_mode` 取值、`ResultState` / `ErrorCode` 取值、MCP JSON-RPC 错误码、`access_audit` 决策面。
- 增加 deterministic citation fidelity checker。
- 强化结构化日志脱敏，避免泄漏 API key / Bearer / token / 密钥片段。
- 将 `SCHEMA_VERSION=9` 与在线 v8 漂移纳入兼容性测试，验证幂等 `initialize()`。
- 将上述契约与测试写回 CI，保证未来改动可回归。

## M1 范围（受治理的 Assist 交付面）

- `ASSIST_ENABLED=false` 默认关闭，仅在显式开启时挂载 REST / SSE Assist 路由。
- `AssistRequest` / `AssistResult` 作为独立契约冻结，但运行时复用 `ChatService.answer/stream`。
- 可选 `ASSIST_MCP_ENABLED=true` 时，MCP 暴露 `mindgraph_assist` 只读工具。
- 所有 Assist 调用与现有通道一样写 `access_audit`，并沿用 ACL fail-closed。

## 验收标准

- 默认配置下，现有 `/chat`、`/mindgraph/chat`、`/mcp` 行为不变。
- `Assist` 关闭时不挂载、不暴露工具、不改变现有契约。
- `Assist` 开启时只做增量暴露，结果中必须携带 `verdict`。
- 引用保真不匹配时，M0 仅警告，不阻断。
- 任何新增事件/错误码/状态值，都必须先更新契约快照测试，再更新实现。

## 后续演进

- M2 才考虑写回、判定账本、或者把引用保真从 warning-first 升级为 fail-closed。
- 若未来真的引入工具调用，必须先证明它不会绕开现有治理层。
