# Changelog

## v2 — RAG 与智谱接入（2026-04-11）

- 新增 `src/config.py`：加载 `.env` 中的 `ZHIPU_API_KEY`，配置 `docs/`、`data/chroma/` 路径及模型名（`glm-4-flash`、`embedding-3`）。
- 实现 `document_loader.py`：扫描 `docs/*.md` 并按字符窗口切分。
- 实现 `rag_engine.py`：智谱向量写入 Chroma、检索、`glm-4-flash` 生成回答；Streamlit 侧栏支持「重建索引」与「强制重建」。
- 更新 `app.py`：对话界面与参考片段展示；提供 `.env.example`、`.gitignore`、`python-dotenv` 依赖。

## v1 — 初始化（2026-04-11）

- 建立项目目录：`src/`（Streamlit 入口、`rag_engine`、`document_loader` 占位）、`docs/`、`evaluation/`。
- 添加 `README.md`、`PRD.md`、`requirements.txt`。
- Streamlit 首页为 Hello World 占位；RAG 与文档管线尚未实现，见各模块文件内注释说明。
