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

# 提交态复核（工作区绿 ≠ 提交可过）：在干净的 HEAD 工作树上再跑一次全量
# 起因：曾把"消费方"提交了、实现留在未提交的工作区改动里，提交态 14 个测试
# ImportError 全红，而本地工作区是绿的（同一失误已发生两次）。
# 脚本代劳：worktree add → 复制两套索引根 → 跑全量 → 清理
.\.venv\Scripts\python.exe scripts\verify_committed_state.py
# 变体：--keep（保留工作树手工排查）/ --ref origin/main / --pytest-args "-q -x"
#       --without-runtime-data（**故意不复制**运行期数据，用来看清"缺数据"时报什么；
#       这是诊断手段，不是复核）

# 手工等价步骤（脚本不可用时）：运行期数据（data/ 被 gitignore）不会随 worktree 检出，
# 缺了它，test_evaluation_v2_migration / test_index_root_registry / test_freeze_baseline
# 会以 "No index version under data/mindgraph_indexes is compatible…" 报 14 个**假失败**
# ——那不是代码问题，是索引根不存在。必须先把两套索引根复制过去（复制而非软链：避免
# 测试把结果写回主工作区）。缺数据时测试会自己把这句话打出来（见 tests/index_data_hint.py）：
git worktree add --detach ..\_verify HEAD
xcopy /E /I /Y data\mindgraph_indexes ..\_verify\data\mindgraph_indexes
xcopy /E /I /Y data\retrieval_indexes ..\_verify\data\retrieval_indexes
cd ..\_verify
..\mindgraph\.venv\Scripts\python.exe -m pytest
cd ..\mindgraph; git worktree remove --force ..\_verify

# 当前 CI 的运行时致命错误 gate（全量 Ruff 债务见产品路线）
.\.venv\Scripts\python.exe -m ruff check src scripts tests --select F821,F822,F823,E902

# 本机 harness 提示（仅个别 AI agent 环境）：`env -u VAR cmd > file` 输出恒为 0 字节，
# 要剥离变量请用 bash 内建 `unset VAR` 后再执行；Linux/CI 不受影响。

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
8. 推送前必须在**提交态**（干净 worktree 上的 HEAD）跑一次全量测试：本地工作区
   变绿只说明工作区自洽，不能说明这次提交自洽——消费方与实现分属两个提交时，
   提交态会直接 ImportError 变红（见"常用命令"里的提交态复核）。

## 当前产品路线

产品边界、阶段路线与品牌迁移清单见 `docs/PRODUCT_STRATEGY.md`。实现顺序为：

1. 产品边界与品牌迁移
2. 可复现 Demo 与可信 UI
3. 独立评测与证据治理
4. 自适应检索路由
5. 企业制度断言图
6. 企业连接器、ACL、审计与受控 MCP
