# 企业报销知识问答系统（Expense RAG QA）

基于检索增强生成（RAG）的企业报销政策与流程问答应用：将报销制度文档向量化存入本地 ChromaDB，用户提问时检索相关片段，再通过智谱 GLM-4-Flash 生成回答。

## 技术栈

| 层级 | 技术 |
|------|------|
| 前端 | Streamlit |
| 后端 / RAG | Python（`rag_engine` 直接调用智谱 SDK；可按需接入 LangChain） |
| 向量库 | ChromaDB（本地持久化） |
| 大模型 | 智谱 GLM-4-Flash（API） |

## 目录结构

```
expense-rag-qa/
├── README.md
├── PRD.md
├── changelog.md
├── requirements.txt
├── .env.example       # 环境变量模板（复制为 `.env`）
├── docs/              # 报销制度 Markdown（入库来源）
├── data/chroma/       # Chroma 持久化数据（自动生成，已 gitignore）
├── evaluation/        # 评测脚本与数据
└── src/
    ├── app.py         # Streamlit 入口
    ├── config.py      # 路径与环境变量
    ├── rag_engine.py  # Chroma + 智谱嵌入/对话
    └── document_loader.py  # 文档加载与切分
```

## 环境要求

- Python 3.10+
- 智谱开放平台 API Key（[智谱 AI 开放平台](https://open.bigmodel.cn/)）

## 本地运行

1. **创建并激活虚拟环境（推荐）**

   ```bash
   python -m venv .venv
   .venv\Scripts\activate
   ```

2. **安装依赖**

   ```bash
   pip install -r requirements.txt
   ```

3. **配置环境变量**

   复制 `.env.example` 为 `.env`，填写智谱 API Key：

   ```env
   ZHIPU_API_KEY=你的智谱API密钥
   ```

   启动时 `src/config.py` 会自动加载项目根目录的 `.env`。密钥不在网页上配置。

4. **（可选）放入制度文档**

   将报销相关 `.md` 文件放入 `docs/`。侧边栏点击 **重建索引** 后，会使用智谱 **embedding-3** 向量化并写入 `data/chroma/`。

5. **启动 Streamlit**

   ```bash
   streamlit run src/app.py
   ```

   浏览器会自动打开本地页面（默认 `http://localhost:8501`）。

## 许可证

按项目需要自行补充。
