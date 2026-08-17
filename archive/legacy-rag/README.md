# Legacy Expense RAG interfaces

本目录保存 MindGraph 前身 Expense RAG QA 的历史交互界面，仅用于回溯和迁移参考，不属于当前产品入口。

- `expense_rag_monolith.py`：最早的单文件 Streamlit 应用。
- `streamlit_app.py` + `app_pages/`：后续拆页版 Streamlit 客户端。
- `web/`：历史静态原型，不是当前承诺的 React Web 客户端。
- 日志和 `mindgraph.db.UNUSED-RESIDUAL`：历史排障残留，不参与运行。

当前生产入口固定为 `src/api/main.py`。顶层 `src/rag_engine.py`、`src/vector_store.py`、`src/embedder.py` 等尚未移入本目录，是因为 `evaluation/baseline.py` 与 `evaluation/runner.py` 仍依赖它们；必须先将评测迁移到当前 retrieval/application 接口，再完成物理隔离。

归档代码不进入 CI 覆盖率与发布镜像，也不保证可独立运行。
