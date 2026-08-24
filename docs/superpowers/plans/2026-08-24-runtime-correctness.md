# Runtime Correctness Repair Plan

> **For Codex:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 修复升级审查中剩余的运行时正确性阻断项：真正的 SSE 流式传输、Obsidian 插件事件契约、MindGraph 索引原子激活与缓存失效、OIDC discovery/JWKS 客户端复用。

**Architecture:** 为同步生成器与异步 SSE 响应之间增加有界队列桥接；将插件事件归并抽成可测试的纯函数；让索引服务在激活失败或激活后持久化失败时恢复旧指针，并在成功后通知容器清理缓存；将 OIDC discovery 与 `PyJWKClient` 放入同一个 TTL 缓存路径。

**Tech Stack:** Python 3.11、FastAPI/Starlette、pytest、FAISS、PyJWT、Node.js `node:test`、Obsidian CommonJS 插件。

---

### Task 1: 真正的 SSE 流式桥接

**Files:**
- Create: `src/api/sse.py`
- Modify: `src/api/routes/chat.py`
- Modify: `src/api/routes/mindgraph_chat.py`
- Test: `tests/test_sse_streaming.py`

1. 先写测试：同步生成器产出首事件后阻塞，异步消费者必须在生成器结束前收到首事件。
2. 运行 `pytest -q tests/test_sse_streaming.py`，确认因缺少桥接实现而失败。
3. 实现有界 `asyncio.Queue` 桥接、异常传播、断连/取消停止信号。
4. 两个聊天路由改为逐项消费桥接器，移除 `list(service.stream(...))`。
5. 运行 `pytest -q tests/test_sse_streaming.py tests/test_api.py tests/test_mindgraph_api.py`。

### Task 2: 修复 Obsidian 插件 SSE 事件契约

**Files:**
- Create: `obsidian-plugin/sse-events.js`
- Create: `obsidian-plugin/sse-events.test.js`
- Modify: `obsidian-plugin/main.js`
- Modify: `obsidian-plugin/styles.css`

1. 使用真实后端 envelope（`event` + `data`）写 `node:test`，覆盖 answer delta、citations、graph links、completed 与 error。
2. 运行 `node --test obsidian-plugin/sse-events.test.js`，确认模块缺失导致失败。
3. 实现纯函数 reducer，并让 `main.js` 仅从 `event.data` 归并状态。
4. 对齐 fail-closed API 边界：提供不落盘的会话级 API Key 输入，并按需发送 `X-API-Key`。
5. 运行 `node --test obsidian-plugin/sse-events.test.js` 和 `node --check obsidian-plugin/main.js`。

### Task 3: 索引原子激活、空索引与缓存失效

**Files:**
- Modify: `src/application/mindgraph_index_service.py`
- Modify: `src/api/dependencies.py`
- Test: `tests/test_mindgraph_index_consistency.py`

1. 先写测试覆盖：删除最后一条笔记可激活空索引；激活后数据库写失败恢复旧 `CURRENT`；成功激活触发回调；容器清理两类 pipeline 缓存。
2. 运行目标测试，确认现状失败。
3. 支持维度已知的空 FAISS 索引；记录激活状态并在后续失败时恢复旧指针；成功后调用缓存失效回调。
4. 容器装配 callback，统一清理普通与 MindGraph pipeline 缓存。
5. 运行 `pytest -q tests/test_mindgraph_index_consistency.py tests/test_mindgraph_index_service.py tests/test_directory_connector.py`。

### Task 4: OIDC discovery 与 JWKS 客户端 TTL 复用

**Files:**
- Modify: `src/api/oidc.py`
- Test: `tests/test_oidc_discovery.py`

1. 先写测试证明自定义 discovery `jwks_uri` 被使用，且 TTL 内两次调用只 discovery/构造一次。
2. 运行目标测试，确认硬编码 JWKS URL 或重复构造导致失败。
3. 让 discovery 结果与 `PyJWKClient` 共用 TTL 缓存；验签只通过缓存客户端获取签名 key。
4. 运行 `pytest -q tests/test_oidc_discovery.py tests/test_auth.py tests/test_api_auth.py`。

### Task 5: 全量验证与提交

**Files:**
- Modify only if verification exposes an in-scope regression.

1. 运行 `pytest -q`。
2. 运行 `ruff check src tests --select E9,F63,F7,F82`。
3. 运行 `node --test obsidian-plugin/sse-events.test.js` 与 `node --check obsidian-plugin/main.js`。
4. 检查 `git diff --check`、`git status --short` 与最终 diff。
5. 使用英文 commit message 提交本批修复；不 push。
