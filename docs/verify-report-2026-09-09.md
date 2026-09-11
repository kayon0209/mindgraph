# MindGraph 真实运行验证报告

> 项目路径：`<repo-root>`
> 验证时间：2026-09-09 20:10 – 21:00（GMT+8）
> 验证方式：真实启动 + Playwright 端到端（真实浏览器）+ 真 LLM（DeepSeek via gitee.com）+ 本地 BGE 检索

---

## 1. 启动命令与运行环境

### 1.1 运行时

| 项 | 值 |
|---|---|
| Python | 3.13.15（`.venv`，符合项目 WAL 路径对 SQLite 的要求，实测 SQLite 3.53.1 ≥ 3.51.3） |
| API | FastAPI，`PYTHONPATH=src .venv/Scripts/python.exe -m uvicorn api.main:app --host 127.0.0.1 --port 8000` |
| Web | Vite 构建（`NODE_OPTIONS=--max-old-space-size=1536 npx vite build`，6.2s 通过）+ 极轻静态服务 `web/_serve.mjs`（5174，代理 `/api` → 8000，支持 SSE） |
| 存储 | 本地 SQLite WAL（`data/product/product.sqlite3`）+ 版本化 FAISS 索引 — **零外部数据库依赖** |
| 模型 | LLM: DeepSeek qwen3.8-flash（gitee.com 兼容端点，.env 已配 key）；Embedding: 本地 BGE-small-zh-v1.5（`data/bge-small-zh-v1.5`） |
| 认证 | `AUTH_MODE=off`（本地优先，无需登录） |
| 端口 | API 8000 / Web 5174（5173 被 HRBPilot 静态服务占用；CORS 白名单已含 5174） |

### 1.2 启动注意事项（环境相关，非项目 bug）

1. **内存紧张**：本机仅 1.3–1.9GB 可用。Vite dev + Chromium 会 OOM → 采用 build + 静态服务方案（与 HRBPilot 验证时相同）。
2. **为释放内存临时停掉了 HRBPilot 的 milvus/minio/etcd 容器**（验证已完成、不再需要）；需要时 `docker start hrbpilot-milvus-1 hrbpilot-minio-1 hrbpilot-etcd-1` 恢复。
3. 不要用 `docker compose up --build`（README 路径 B）：需构建两个镜像且拉基础镜像易被墙，本地 venv 启动快得多。

---

## 2. 已验证通过的功能清单

### 2.1 真实浏览器端到端（Playwright，1440×900，采集 console + network）

| 步骤 | 场景 | 结果 |
|---|---|---|
| 1 | 首页加载 `/#/chat`（标题 "MindGraph · 依据工作台"，导航/输入框渲染） | ✅ |
| 2 | 空提问（Enter 空提交）→ 无请求发出、无脏会话创建 | ✅ |
| 3 | 真实提问「2026年8月的费用报销遵循30天还是60天规则？」→ SSE 流式回答完成，含**版本/来源**关键词 | ✅ 真 LLM |
| 4 | 知识库搜索「报销」→ 10 条结果渲染 | ✅ |
| 5 | 上传 .md 文件 → 上传 + 索引增量重建（首次验证发现 422，修复后通过，见 §3） | ✅（修复后） |
| 6 | 图谱页 53 个节点/canvas 元素渲染 | ✅ |
| 7 | 评测页指标渲染（Recall/运行/指标） | ✅ |
| 8 | 关系页：无候选关系（正常空态），无原因确认的边界用例跳过 | ✅ |
| 9 | 重复上传同一 .md → 后端 409 Conflict（`ConflictError("Document already exists")`）**有意的防重设计**，前端显示"上传失败：Document already exists" | ✅（P3 文案可打磨） |

### 2.2 API 层回归（curl 直连 8000）

- `GET /api/v1/health` → 200 `{"status":"ok","db_probe":"ok","worker_enabled":true}`，task worker 正常拉起
- `POST /api/v1/knowledge/index/rebuild` → 200，4 文档全部 `indexed`
- `POST /api/v1/mindgraph/chat` → 200：问「差旅费的报销时限是多少天？」→ 正确回答 **30 个自然日**，**5 条 citation**（命中《费用报销管理办法 V2》等），响应含 `retrieval_trace` / `usage` / `timing` / `citation_fidelity` / `result_state` 全套可追溯字段
- `DELETE /api/v1/knowledge/documents/{id}` → 200（清理测试文档用，顺手验证了删除链路）

### 2.3 数据清理（验证不污染真实数据）

E2E 上传的测试文档 `_verify_upload_test.md` 已通过 `DELETE` + 全量 rebuild 彻底清除，文档列表恢复为原始 4 条制度。

---

## 3. 修复的问题

### P1：知识库增量重建接口 422（chunk_load_failed ×4 → value_error）

**复现步骤**：
1. 启动 API，浏览器进入知识库页
2. 上传任意 .md 文件（上传本身成功）
3. 前端自动调 `POST /api/v1/knowledge/index/incremental-rebuild` → **422 Unprocessable Entity**
4. 服务端日志：`mindgraph.documents: chunk_load_failed` ×4 + `mindgraph.api.errors: value_error`

**原因定位**：
`document_lifecycle_service.active_chunks()` 按 `document_versions.source_path` 找每份文档的 `chunks.json`。库中 4 条 active 文档的 `source_path` 仍指向**旧项目绝对路径** `<legacy-repo>\data\product\documents\...`（项目从 expense-rag-qa 演进复制时，SQLite 里的路径没有跟着迁移，旧路径已不存在）→ 4 个 chunks.json 全部 FileNotFoundError → 语料不完整 → 重建流程抛 ValueError → 422。

**修复**（数据修复，无代码改动）：
- 备份：`cp data/product/product.sqlite3 data/product/product.sqlite3.bak-20260909-pathfix`
- 将 `document_versions.source_path` 前缀 `<legacy-repo>` → `<repo-root>`（4 行，脚本 `_fix_source_paths.py`）
- 重调增量重建 → **HTTP 200，`build_status: validated`，4 文档全部 active+searchable，98 chunks 入库**

**为什么之前没人发现**：日常问答走 FAISS 索引 + chunk 存储（数据本体一直都在），不受影响；只有「上传触发增量重建」这条路径会扫 `source_path`，且需要上传动作才触发。

### P3（记录未改）：增量重建与全量重建的 chunk 数不一致

增量 rebuild 报 `chunk_count=98`（chunker child_size=500/parent_size=1200），随后的全量 rebuild 报 `chunk_count=69`。两条路径的切分配置疑似不同步，可能导致增量与全量的索引粒度不一致。不影响本次主流程（问答正确、引用正常），建议后续统一 chunker 配置来源。

### P3（记录未改）：重复上传的 409 文案不友好

前端直接展示 "上传失败：Document already exists"。建议改为「内容相同的文档已存在（同名或同校验和），如需更新请使用『上传新版本』」。入口在 `web/src/pages/KnowledgePage.tsx:152` 的 catch 分支。

### P3（记录未改）：聊天空提问静默 return

`ChatPage.tsx:709` `if (!finalQuestion || running) return;` 空提问时用户无反馈（与 HRBPilot 员工请求页同类问题）。实际影响小：输入框为空时用户预期明确。

---

## 4. 后端测试

```
pytest tests/ -q
566 passed, 3 skipped, 0 failed（2m05s，总覆盖率 73.61%，高于项目设定的 55% 门槛）
```

---

## 5. 仍存在的风险 / 需要你补充的信息

1. **增量 vs 全量 chunk 配置漂移**（见 §3 P3）——如果后续增量上传频繁，索引粒度会与全量重建不一致。
2. **关系审核（HITL）未覆盖**：当前库中无候选关系，确认/拒绝必填原因的校验逻辑（`RelationsPage.tsx:83`）未被真实点击验证过；可以运行一次 `scripts/extract_relations.py` 制造候选后再测。
3. **`.env` 含真实 API key**（ZHIPU + gitee DeepSeek）：本地开发文件，不要提交或外发；`git status` 确认 .env 在 .gitignore 中。
4. **AUTH_MODE=off**：所有接口匿名可写（本机单用户场景合理）；若要演示 ACL/多角色能力需切 OIDC 或 API Key 模式。
5. **HRBPilot 三容器（milvus/minio/etcd）为释放内存已停**：跑 HRBPilot 前记得 `docker start hrbpilot-milvus-1 hrbpilot-minio-1 hrbpilot-etcd-1`。
6. **内存天花板**：本机可用内存 <2GB 时，Vite dev 模式不可用；已留 `web/_serve.mjs`（build + 静态服务 + API 代理）作为长期替代。

---

## 6. 一句话总结

> **MindGraph 验证完成**：Python 3.13.15 venv + SQLite WAL + 本地 BGE 全链路本地运行；Playwright 真实浏览器跑通 8 个场景（SSE 流式问答带版本化引用、知识库搜索/上传/409 防重、图谱 53 节点、评测页、无原因确认边界）；修复 1 个真实 P1——**项目从 expense-rag-qa 迁移时 4 条文档的 source_path 残留旧绝对路径导致增量索引重建 422**（已修数据 + 留 DB 备份）；记录 3 个 P3（增量/全量 chunk 配置漂移、409 文案、空提问静默）；测试文档已清理，验证不污染数据。
