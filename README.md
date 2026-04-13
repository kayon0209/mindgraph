# 企业报销知识问答系统

基于 RAG 技术的企业报销政策智能问答助手，支持本地 Embedding 模型，无需 API 额度即可运行。

[![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://python.org)
[![Streamlit](https://img.shields.io/badge/Streamlit-1.28+-red.svg)](https://streamlit.io)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

## 功能特性

- 💬 **自然语言查询**：员工用日常语言提问，无需学习关键词
- 📚 **答案带引用**：每个回答都标注来源文档，防止 AI 幻觉
- 🛡️ **安全防护**：自动拒绝薪资、考勤等非报销类问题
- 💰 **零 API 成本**：本地 BGE 模型，无需购买 Embedding 额度
- 📊 **97% 问题覆盖率**：34 题评测集验证，企业级可靠性

## 快速开始

```bash
# 1. 克隆项目
git clone https://github.com/pleaselikeme/expense-rag-qa.git
cd expense-rag-qa

# 2. 创建虚拟环境
python -m venv .venv
.venv\Scripts\activate  # Windows

# 3. 安装依赖
pip install -r requirements.txt

# 4. 配置 API Key
cp .env.example .env
# 编辑 .env，填入 ZHIPU_API_KEY

# 5. 启动应用
streamlit run src/app.py
```

访问 http://localhost:8501

## 项目结构

```
expense-rag-qa/
├── knowledge/          # 知识库文档（Markdown）
├── src/               # 源代码
├── evaluation/        # 评测系统
├── docs/              # 项目文档
│   ├── project/       # PRD、架构设计
│   └── CHANGELOG.md   # 版本历史
└── assets/            # 图片资源
```

## 技术栈

| 组件 | 技术 |
|------|------|
| 前端 | Streamlit |
| 向量库 | ChromaDB |
| Embedding | BAAI/bge-large-zh-v1.5（本地）|
| 大模型 | 智谱 GLM-4.5-Air |

## 文档

- [项目文档](./docs/)
- [版本历史](./docs/CHANGELOG.md)
- [知识库文档](./knowledge/)

## 截图

![界面预览](./assets/images/screenshot.png)

## 许可证

[MIT](LICENSE)

---

**GitHub**: https://github.com/pleaselikeme/expense-rag-qa
