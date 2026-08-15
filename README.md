# MindGraph · Graph RAG 检索增强问答系统

> 混合检索（Dense + Sparse + RRF）+ 一跳知识图谱扩展 + 本地优先嵌入的可溯源问答。
> 由「企业报销制度问答 RAG」重构演进而来，检索 / 评测工程能力被复用为底层 hybrid retriever。

[![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688.svg)](https://fastapi.tiangolo.com)
[![FAISS](https://img.shields.io/badge/FAISS-Vector_Index-blue)](https://github.com/facebookresearch/faiss)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](./LICENSE)

## 为什么是 MindGraph，而不是「又一个 RAG Demo」

大多数 RAG Demo 卡在两件事上：**检索策略拍脑袋**（默认上重排，却没测过值不值），以及**答案不可溯源**。MindGraph 把检索做成可度量、可消融的工程模块，并让每个答案都带 `[citation]` 溯源；图谱一跳扩展在检索命中后补充证据，而不是凭空生成。

## 核心能力

- **混合检索（Hybrid）**：FAISS(Dense / BGE) + BM25(Sparse) + RRF 融合，可选 Cross-Encoder 重排
- **一跳图谱扩展**：检索命中笔记后沿知识图谱扩展一跳关系，补充证据上下文
- **可溯源问答**：答案带 `[citation]`，拒绝无据编造
- **本地优先**：BGE 本地嵌入，数据留在你机器上，无强制云依赖
- **双前端**：① Obsidian 插件（右侧栏问答 + 插入笔记）；② React + Vite Web Demo
- **可复现评测**：4 策略消融脚本，结果写入 `evaluation_runs`

## 系统架构

![MindGraph 系统架构](./assets/architecture.svg)

```
Obsidian Vault
   │  扫描 + 注入稳定 Frontmatter ID（mindgraph_id）
   ▼
本地 FastAPI 服务 (src/, 端口 8000)
   ├─ VaultSyncService          扫描 / 剪枝 / 稳定 ID 注入
   ├─ MindGraphIndexService     FAISS(BGE) + BM25 + RRF 增量索引（CURRENT 原子切换）
   ├─ MindGraphRetrievalPipeline hybrid + 一跳图谱扩展
   ├─ GraphStore / RelationStore  notes + note_relations
   └─ 只读 API /mindgraph/*  +  问答 SSE /mindgraph/chat
        │
        ├─ 前端 ①：Obsidian 插件（右侧栏问答 + 插入笔记）
        └─ 前端 ②：Web Demo（React + Vite，4 页）
```

| 组件 | 技术 |
|------|------|
| 嵌入 | BAAI/bge-small-zh-v1.5（本地缓存，离线推理） |
| 检索 | FAISS(Dense) + BM25(Sparse) + RRF 融合 + 可选 Cross-Encoder 重排 |
| 图谱 | SQLite `note_relations`（proposed / confirmed / conflict 检测） |
| 生成 | OpenAI-compatible（DeepSeek / 智谱 GLM / Anthropic，可降级） |
| API | FastAPI + SSE |
| 存储 | SQLite(WAL) + 版本化向量索引 |
| 前端 | Obsidian 插件 + React(Vite) Web Demo |

## 评测（真实、可复现）

在 **34 题报销制度问答集**上做 4 策略消融，核心结论：

| 检索策略 | R@5 | 相对延迟 | 备注 |
|----------|-----|----------|------|
| Sparse (BM25) | 基线 | 1× | — |
| Dense (FAISS/BGE) | 接近基线 | ~数× | 语义补全 |
| **Hybrid (RRF 融合)** | **0.587（最高）** | ~13ms | **默认策略** |
| Hybrid + Rerank | 反降 | **~99×** | 延迟代价远大于收益 → 设为按需 |

> 据此定义产品默认路由：**混合检索默认开启，重排按需启用**——用 ~13ms 拿到最高召回，避免 99× 延迟换来的召回回退。
> 完整数据与延迟成本分析见 [`docs/MindGraph-cost-efficiency.md`](docs/MindGraph-cost-efficiency.md)。

```bash
python scripts/run_ablation.py        # 写真实 4 策略消融到 evaluation_runs
```

> 小样本 Golden Set 来自开发过程，用于工程可复现验证，不代表生产效果。

## 快速开始

### 1. 后端（FastAPI）

```bash
cd expense-rag-qa
python -m venv .venv && source .venv/Scripts/activate        # Windows
pip install -r requirements.txt
cp .env.example .env            # 已含 HF 镜像与 BGE 本地化配置
# 首次构建索引（需先缓存 BGE，见 data/bge-small-zh-v1.5）
python scripts/sync_vault.py --vault "D:\ObsidianVault"
uvicorn api.main:app --app-dir src --host 127.0.0.1 --port 8000
```

API 文档：http://localhost:8000/docs

### 2. Web Demo（React + Vite）

```bash
cd expense-rag-frontend        # 独立前端目录（与后端仓库同级工作区）
npm install && npm run dev      # http://127.0.0.1:5173
```

### 3. Obsidian 插件

见 [obsidian-plugin/README.md](obsidian-plugin/README.md)：将 `obsidian-plugin/` 作为文件夹复制到
Obsidian 的 `.obsidian/plugins/`，在社区插件设置中启用 **MindGraph**。

## 演进说明

本项目由「企业报销制度问答 RAG（Expense RAG QA）」重构演进而来。原报销 RAG 的检索 / 评测工程能力被复用为 MindGraph 的底层 hybrid retriever。历史 PRD 见 [docs/PRD-v2.md](docs/PRD-v2.md)（报销 RAG v3.1，仅作背景参考）。

> **关于本地 Demo 数据**：开发期使用了一份真实 Obsidian Vault 做端到端验证（笔记索引、关系抽取、图谱可视化均跑通），但该 Vault 属个人数据，**不随仓库分发**——clone 后需自备 Vault 才能复现图谱与插件链路。

## 文档

- [MindGraph 架构与落地计划](docs/MindGraph-ARCH.md)（当前权威）
- [报销 RAG PRD v2（历史背景）](docs/PRD-v2.md)
- [检索成本效率分析](docs/MindGraph-cost-efficiency.md)

## License

[MIT](LICENSE) —— 详见 [LICENSE](LICENSE) 文件。
