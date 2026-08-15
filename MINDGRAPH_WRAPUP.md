# MindGraph AI · 拓展收尾总结（2026-07-20）

## 背景

用户问「这个项目已经按照落地计划拓展完成了？」。诊断结论：后端 + 索引 + 评测 + Web Demo 主线**已真实跑通**（非 mock），但完整「方案 A 拓展」还差三块——Obsidian 插件、关系数据、文档对齐。用户选择**全部一次性收尾**，随后要求把「关系抽取自动化」这最后一环也补齐。

## 交付清单

### 1. 关系数据 seed（T75）
- 新增 `scripts/seed_relations.py`：从 `product.sqlite3` 真实笔记按**共享标签 + 标题关键词**抽取关系，按无序对去重后写入 **23 条**（`note_relations`：12 confirmed + 11 proposed，含 1 冲突样例，`model_version='seed-v1'`）。
- 知识图谱页与链接建议页现在已有真实内容。

### 2. 文档与架构清理（T76）
- `README.md` 重写为 MindGraph AI 定位。
- 新建 `docs/MindGraph-ARCH.md`（权威架构 + 落地计划 + 验收清单）。
- `docs/PRD-v2.md` 顶部加演进说明（报销 RAG 仅作背景参考）。
- 归档旧报销 RAG 前端到 `archive/legacy-rag/`（`streamlit_app.py` / `.streamlit` / `app_pages` / `web` / `streamlit*.log` / `Dockerfile.streamlit`）。
- 无用空残留 `data/mindgraph.db` 移入 `archive/legacy-rag/mindgraph.db.UNUSED-RESIDUAL`。

### 3. Obsidian 插件（T77，方案 A 双前端之一）
- 新建 `obsidian-plugin/`（`manifest.json` + `main.js` + `styles.css` + `README.md`），**纯 JS 可加载，无需构建**。
- 右侧栏 View：调 `/api/v1/mindgraph/chat/stream` 流式问答，解析 `answer_delta.text` / `citations` / `graph_links`；支持图谱开关 + 「插入到当前笔记」。
- `.env` CORS 放行 `app://obsidian.md` 与 `*`，重启后端验证跨域头生效。

### 4. 关系抽取自动化（T78–T80，闭环最后一环）
- 新增 `src/application/relation_extraction_service.py`：`RelationExtractionService`。
  - **离线默认**：每篇笔记取其 chunk 文本拼接后用本地 BGE 嵌入，求两两余弦相似度，取每篇 top_k 且超过阈值者为候选；
  - **可选 `use_llm=True`**：对高相似度候选用已配置 Chat Provider 精炼关系类型与依据（规则 + 轻量 LLM）；
  - **质量过滤**：跳过同标题（重复笔记）/ 纯日期 / 过短标题的噪音候选；
  - **幂等去重**：已存在（任意状态 / 任一方向）的 pair 不重复写入；
  - **Human-in-the-loop**：仅写 `proposed`，绝不自动 confirmed。
- 容器装配：`api/dependencies.py` 挂载 `container.relation_extraction`（传入 db / 索引根 / provider_registry）。
- 触发接口：`POST /api/v1/mindgraph/relations/extract`（body：method / top_k / similarity_threshold / max_candidates / use_llm / dry_run）→ 返回统计。
- 触发脚本：`scripts/extract_relations.py`（复用容器，CLI 参数对齐，dry-run 预览）。
- 实跑：扫描 378 笔记 → 1168 候选 → 过滤 330 噪音 → 写入 **200 条 `proposed`**（`model_version='auto-v1'`），0 冲突，幂等。

### 5. 收尾清理（T81）
- `docker-compose.yml` 重写为 MindGraph 后端服务（移除旧 `streamlit` 服务、旧 `Dockerfile.streamlit` 引用，索引卷改 `data/mindgraph_indexes`，补模型缓存卷 + CORS 环境）。

## 当前真实状态（非 Mock）

| 项 | 值 |
|----|----|
| 笔记 | 378 篇（全部 `index_status=ready`，来自真实 Obsidian Vault） |
| 索引 | 3019 chunks / FAISS 6.2MB / `CURRENT` 就位 |
| 关系 | **223 条**（12 confirmed + 211 proposed；其中 200 为 auto-v1 语义相似度自动抽取，11 为 seed 演示） |
| 评测 | 6 条真实消融 runs（`evaluation_runs`） |
| 模型 | BGE 本地化（`data/bge-small-zh-v1.5`，13 文件，dim=512） |
| 后端 | http://127.0.0.1:8000 在线（`/relations/extract` 已加载，CORS 已放行插件 origin） |
| 前端 | Web Demo 在独立前端仓库 :5173 ｜ Obsidian 插件 `obsidian-plugin/` |

## 遗留 / 待办（已清零）

- ~~关系为 seed 演示，非全量 AI 抽取~~ → 已由 `relation_extraction_service.py` 自动抽取（auto-v1，200 条 proposed）。
- ~~`docker-compose.yml` 含旧 streamlit 定义~~ → 已重写为 MindGraph 后端服务。
- 根目录 `.streamlit-run*.log` 两个文件被历史进程占用未能删除（无害旧日志，不影响运行；进程释放后可手动清理）。

## 验证记录

- `python scripts/seed_relations.py` → 23 条关系；抽查确认链接真实笔记、冲突对正确。
- `node --check obsidian-plugin/main.js` → 通过。
- 后端重启后 CORS 复探 → `access-control-allow-origin: app://obsidian.md` + `vary: Origin` 出现。
- `python scripts/extract_relations.py --threshold 0.62 --top-k 4 --max 200` → 1168 候选 / 330 噪音过滤 / 200 写入 / 0 冲突；抽样确认「熊猫图鉴(预测复盘↔数据报告)」「抖音起号参考手册↔抖音起号专家」等强相关对，0 条同标题噪音。
- `POST /api/v1/mindgraph/relations/extract` 经 API 触发 dry-run 返回正确统计；`/mindgraph/evaluation/ablation` 统计 `relations_proposed=211`；`/relations/proposed` 返回真实标题/置信度/冲突标记。
