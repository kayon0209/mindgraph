# CLAUDE.md - 企业报销知识问答系统

## 项目概述
基于 RAG 的企业报销政策问答系统，使用 ChromaDB + 智谱 GLM-4-Flash。

## 技术栈
- 前端：Streamlit
- 向量库：ChromaDB（本地持久化）
- 大模型：智谱 GLM-4-Flash
- 嵌入模型：智谱 embedding-3

## 目录结构
```
expense-rag-qa/
├── src/
│   ├── app.py           # Streamlit 入口
│   ├── rag_engine.py    # RAG 核心逻辑
│   ├── document_loader.py
│   └── config.py
├── docs/                # 制度文档
├── data/chroma/         # 向量库数据
└── evaluation/          # 评测脚本
```

## 常用命令
```bash
# 启动应用
streamlit run src/app.py

# 重建索引（在侧边栏操作）
# 或 Python 脚本方式：
python -c "from src.rag_engine import build_index; build_index('你的API密钥')"
```

## 开发规范
1. 文档放在 `docs/` 目录，自动被索引
2. 不要提交 `.env` 到 GitHub
3. 测试集放在 `evaluation/` 目录

## 注意事项
- 智谱 API Key 在 `.env` 中配置
- 向量库数据已 gitignore，不会被提交
