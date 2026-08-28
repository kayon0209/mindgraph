# AGENTS.md - MindGraph

## 项目概述

MindGraph 是本地优先的企业制度与决策依据知识服务。首个垂直场景聚焦报销、财务与制度合规问答，核心承诺是提供带来源、版本和证据链的可审计答案。

项目由 Expense RAG QA 演进而来。历史报销领域代码与文档用于保留演进证据，但不得作为当前默认入口；当前生产入口是 FastAPI 与 `application/domain/infrastructure/retrieval` 分层。

## 当前技术栈

- API：FastAPI + SSE
- 检索：FAISS Dense + BM25 Sparse + RRF，可选 Cross-Encoder
- 嵌入：本地 BGE
- 存储：SQLite(WAL) + 版本化向量索引
- 图关系：SQLite `note_relations`，仅 confirmed 关系进入一跳扩展
- 客户端：React + TypeScript + Vite Web 工作台；仓库内 Obsidian 插件

## 当前权威目录

```text
mindgraph/
├── src/api/                 # FastAPI 入口与路由
├── src/application/         # 应用服务
├── src/domain/              # 领域模型与错误
├── src/infrastructure/      # 数据库、Provider、解析器、配置
├── src/retrieval/           # 当前 Hybrid RAG 与 MindGraph 扩展核心
├── obsidian-plugin/         # Obsidian 客户端
├── web/                     # React 企业 Web 工作台
├── demo-vault/              # 可公开的合成企业制度演示库
├── archive/legacy-rag/      # 已归档的历史交互界面
├── scripts/                 # 同步、抽取、评测与运维脚本
├── tests/                   # 自动化测试
├── evaluation/              # Golden Set 与评测逻辑
└── docs/                    # 产品、架构与历史文档
```

旧 `src/app.py` 已归档为 `archive/legacy-rag/expense_rag_monolith.py`。`src/rag_engine.py`、`src/vector_store.py` 等仍被评测基线依赖，迁移完成前不得假设它们可直接删除。

## 常用命令

```powershell
# API（推荐：一键启动脚本，自动设置 PYTHONPATH 并使用项目 venv）
powershell -ExecutionPolicy Bypass -File scripts\start-dev.ps1

# API（手动启动；AUTH_MODE 等配置按 "进程环境变量 > .env > 默认" 动态解析）
.\.venv\Scripts\python.exe -m uvicorn api.main:app --app-dir src --host 127.0.0.1 --port 8000

# 测试
.\.venv\Scripts\python.exe -m pytest

# 当前 CI 的运行时致命错误 gate（全量 Ruff 债务见产品路线）
.\.venv\Scripts\python.exe -m ruff check src scripts tests --select F821,F822,F823,E902

# 无密钥离线演示
.\.venv\Scripts\python.exe scripts\validate_mindgraph_offline.py

# Web
cd web
pnpm typecheck
pnpm test
pnpm build

# 使用现有 Vault 构建索引
.\.venv\Scripts\python.exe scripts/sync_vault.py --vault "D:\path\to\vault"
```

## 开发规范

1. 新能力必须说明它解决的企业决策问题，不因技术可用而增加功能。
2. 对外文档必须区分当前能力、历史能力和计划能力。
3. 向量相似度只能生成候选关系，不得宣传为已验证的业务关系。
4. 只有带证据且 confirmed 的关系才能进入图扩展检索。
5. 行为变更先写失败测试，再写实现；完成后运行相关测试与 lint。
6. 不提交 `.env`、API Key、真实 Vault、真实企业资料或生成索引。
7. 历史 Expense RAG 命名仅允许出现在迁移说明、历史文档和兼容代码中。

## 当前产品路线

产品边界、阶段路线与品牌迁移清单见 `docs/PRODUCT_STRATEGY.md`。实现顺序为：

1. 产品边界与品牌迁移
2. 可复现 Demo 与可信 UI
3. 独立评测与证据治理
4. 自适应检索路由
5. 企业制度断言图
6. 企业连接器、ACL、审计与受控 MCP
