# MindGraph · 企业制度与决策依据知识服务

> 本地优先的 Hybrid RAG：Dense + Sparse + RRF、一跳受控关系扩展、SSE 流式回答与 citation 溯源。

[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688.svg)](https://fastapi.tiangolo.com)
[![FAISS](https://img.shields.io/badge/FAISS-Vector_Index-blue)](https://github.com/facebookresearch/faiss)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](./LICENSE)

![MindGraph Hero Banner](assets/hero-banner.jpg)

MindGraph 面向制度密集、版本频繁、错误代价高的企业知识场景。首个垂直方向是报销、财务与制度合规问答：回答不仅要“像是正确”，还必须能够回到原文证据，并逐步支持版本、生效期、例外和冲突治理。

本项目由 Expense RAG QA 演进而来。历史报销领域代码与评测经验被保留，但当前产品入口和新增能力统一使用 MindGraph 命名。

## 当前能力

- **混合检索**：FAISS/BGE Dense + BM25 Sparse + RRF 融合
- **按需重排**：Cross-Encoder 不默认开启，避免不必要的延迟成本
- **受控关系扩展**：只有人工确认的 confirmed 关系进入一跳补充检索
- **可溯源回答**：SSE 流式生成，回答携带 citation 与检索 trace
- **本地优先**：SQLite(WAL) + 版本化 FAISS 索引 + 本地 BGE
- **人工审核闭环**：候选关系 proposed → confirmed/rejected
- **可复现质量评测**：检索消融 + 确定性答案级可信评分，结果进入同一评测账本
- **同仓 Web 工作台**：可信问答、制度台账、评测对比和关系审核

当前关系候选默认主要来自笔记级语义相似度。因此准确描述是“带人工确认关系扩展的 Hybrid RAG”，不是已经完成实体消歧、多跳推理和社区摘要的完整知识图谱系统。

## 架构

```text
Markdown / Obsidian Vault
    │ 扫描、稳定 ID、内容哈希
    ▼
FastAPI（src/api）
    ├─ application       应用服务与用例编排
    ├─ domain            领域模型与错误
    ├─ infrastructure    SQLite、Provider、解析器与配置
    └─ retrieval         Dense + Sparse + RRF + 受控关系扩展
          │
          ├─ SQLite(WAL)：notes / note_relations / evaluation_runs
          └─ 版本化索引：FAISS / chunks / manifest / CURRENT
```

当前生产 API 入口为 `src/api/main.py`。`src/retrieval/` 是 MindGraph 活跃核心，不是历史遗留目录。

## 快速开始

### 1. 创建环境

PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

Bash：

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

### 2. 先跑公开离线演示（无需密钥、无需私人 Vault）

```powershell
python scripts/validate_mindgraph_offline.py
```

该命令用 `demo-vault/` 的合成企业制度，在临时目录中完成 Vault 同步、索引构建、Hybrid 检索、confirmed 关系一跳扩展及开关消融，运行结束自动清理。

如需生成供只读 API/数据检查使用的独立演示数据库：

```powershell
python scripts/sync_offline_demo.py
```

默认输出为 `data/demo/product.sqlite3`，不会覆盖产品数据库。

### 3. 同步自己的 Markdown/Vault 并建立真实索引

建议为企业制度登记以下 Frontmatter；缺失字段不会阻断同步，但会在 Web 台账中标记为“待治理”：

```yaml
---
owner: 财务运营部
version: "2.0"
status: active
effective_from: 2026-07-01
effective_to: 2027-06-30  # 长期有效可省略
---
```

`status` 当前接受 `draft`、`active`、`expired`、`superseded`、`archived`。同步服务会把这些字段规范化写入治理列，元数据变更会把文档重新标记为待索引；旧数据库在启动初始化时原位升级，不重建 `notes` 表。

```powershell
python scripts/sync_vault.py --vault "D:\path\to\vault"
```

### 4. 启动 API

```powershell
python -m uvicorn api.main:app --app-dir src --host 127.0.0.1 --port 8000
```

- API 文档：<http://127.0.0.1:8000/api/docs>
- 健康检查：<http://127.0.0.1:8000/api/v1/health>

### 5. 启动 React Web

```powershell
cd web
corepack enable
corepack prepare pnpm@11.19.0 --activate
pnpm install
pnpm dev
```

打开 <http://127.0.0.1:5173>。开发服务器会把 `/api` 代理到本机 `8000` 端口。

也可以从仓库根目录构建完整容器：

```powershell
docker compose up --build
```

Web：<http://127.0.0.1:3000>；API：<http://127.0.0.1:8000>。

### 6. Obsidian 插件

仓库内客户端见 [obsidian-plugin/README.md](obsidian-plugin/README.md)。插件消费 `/api/v1/mindgraph/chat/stream`，支持流式问答和插入当前笔记。

## 当前开箱边界

仓库已附带公开 `demo-vault/`，无需模型密钥即可验证同步、索引、Hybrid 检索和一跳关系扩展。离线演示使用确定性的 Fake Embedding/Fake LLM，只证明工程链路，不代表真实模型质量。

React Web 已并入 `web/`，并由同一 CI 和 Docker Compose 构建。真实回答仍需配置模型 Provider，真实语义索引需准备本地 BGE 模型或允许首次下载；离线脚本中的 Fake 模型不会被 Web 冒充为生产能力。

制度台账会展示 owner、version、生效区间、制度状态和治理缺口；这些字段也会进入检索 chunk 与 citation。基础 Hybrid 检索和 confirmed 关系扩展共用状态、生效期与分类过滤，避免历史制度通过图扩展重新进入当前答案。

## 评测

```powershell
# 检索层：BM25 / Hybrid / Hybrid + Graph
python scripts/run_ablation.py

# 回答层：直接运行 12 条 Golden case，保存预测并写入 evaluation_runs
.\.venv\Scripts\python.exe scripts\run_answer_evaluation.py --live --strategy hybrid

# 对已有预测文件复评分，不写 evaluation_runs
.\.venv\Scripts\python.exe scripts\run_answer_evaluation.py `
  --predictions evaluation\results\answer_predictions_YYYYMMDDTHHMMSSZ.jsonl `
  --dry-run
```

默认使用 `evaluation/datasets/mindgraph_golden.jsonl`：12 条人工编写、与运行关系库独立的企业制度样本，覆盖版本替代、审批阈值、例外、跨制度组合、无答案和歧义场景。答案评测会量化 citation F1、拒答正确性、版本有效性、必需事实覆盖与禁用事实规避，并在同一账本聚合 P95 总延迟、平均 Token、平均估算成本及各项数据覆盖率；成本缺少币种或混用币种时评测会失败。版本比较样本只允许 Golden 明确标注的历史来源。`--live --dry-run` 只禁止写入 `evaluation_runs`，问答服务仍会按现有隐私配置记录 `query_logs`。

这些指标是可重复的字符串和元数据回归，不等同于语义正确性或人工判断。当前 12 条规模只够做确定性回归，不能作为生产效果或多跳 GraphRAG 增益证明。

## 文档

- [产品边界与升级路线](docs/PRODUCT_STRATEGY.md)
- [当前架构与落地说明](docs/MindGraph-ARCH.md)
- [检索成本效率分析](docs/MindGraph-cost-efficiency.md)
- [历史报销 RAG PRD](docs/PRD-v2.md)

## 开发验证

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\ruff.exe check src scripts tests --select F821,F822,F823,E902
cd web
pnpm typecheck
pnpm test
pnpm build
```

当前测试覆盖率门槛按已测基线暂设为 55%，用于保证 CI 不虚假标绿或永久红灯；全量 Ruff/格式遗留与覆盖率提升属于 Phase 1 的显式偿债项，目标是将活跃核心覆盖率提升到至少 60%。

## License

[MIT](LICENSE)
