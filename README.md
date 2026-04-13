# 企业报销知识问答系统（Expense RAG QA）

基于检索增强生成（RAG）的企业报销政策与流程问答应用：将报销制度文档向量化存入本地 ChromaDB，用户提问时检索相关片段，再通过智谱 GLM-4-Flash 生成回答。

## 技术栈

| 层级 | 技术 |
|------|------|
| 前端 | Streamlit |
| 后端 / RAG | Python（`rag_engine` 直接调用智谱 SDK） |
| 向量库 | ChromaDB（本地持久化） |
| 大模型 | 智谱 GLM-4.5-Air（生成） |
| Embedding | 智谱 embedding-3（默认）/ 本地 BGE（可选） |

## 目录结构

```
expense-rag-qa/
├── README.md
├── PRD-v1.md
├── changelog.md
├── requirements.txt
├── .env.example       # 环境变量模板（复制为 `.env`）
├── .gitignore
├── docs/              # 报销制度 Markdown（入库来源）
├── data/chroma/       # Chroma 持久化数据（自动生成，已 gitignore）
├── evaluation/        # 评测脚本与数据
│   ├── __init__.py
│   ├── test_cases.py  # 30+4 道评测题
│   ├── scorer.py      # 关键词匹配 + LLM 幻觉检测评分
│   └── runner.py      # 评测运行器
└── src/
    ├── app.py              # Streamlit 入口
    ├── config.py           # 路径与环境变量
    ├── rag_engine.py       # Chroma + 智谱嵌入/对话
    ├── document_loader.py  # 文档加载与切分
    └── special_cases.py    # 特殊情况固定话术 + 超范围拒答
```

## 快速开始

> **前提条件**：你需要拥有智谱开放平台的 API Key。
> 如未申请，请前往 [智谱 AI 开放平台](https://open.bigmodel.cn/) 注册并创建 API Key。

### 1. 克隆项目

```bash
git clone https://github.com/<你的用户名>/expense-rag-qa.git
cd expense-rag-qa
```

### 2. 创建并激活虚拟环境

```bash
python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS / Linux
source .venv/bin/activate
```

### 3. 安装依赖

```bash
pip install -r requirements.txt
```

### 4. 配置 API Key

复制环境变量模板并填入你的 API Key：

```bash
cp .env.example .env
```

编辑 `.env` 文件：

```env
# 必需：智谱 API Key（用于对话生成）
ZHIPU_API_KEY=你的智谱API密钥

# 可选：Embedding 后端选择（默认 zhipu）
# - zhipu: 使用智谱 embedding-3（需要 embedding 额度）
# - local: 使用本地 BGE 模型（免费，首次需下载 1.2GB）
EMBED_BACKEND=zhipu
```

> **重要**：`.env` 文件已在 `.gitignore` 中忽略，不会被提交到版本库。请勿将 API Key 硬编码在任何源代码文件中。

#### Embedding 后端选择

如果你的智谱 embedding-3 额度不足，可以切换到本地模型：

```env
EMBED_BACKEND=local
```

首次使用本地模型时会自动下载（约 1.2GB），后续无需联网即可使用。

### 5. 启动应用

```bash
streamlit run src/app.py
```

浏览器会自动打开 `http://localhost:8501`。

### 6. 构建知识库

首次使用时，点击侧边栏的 **重建索引** 按钮，将 `docs/` 目录下的制度文档向量化并写入 ChromaDB。

## 评测

系统提供 34 道评测题（30 题标准测试集 + 4 题对抗性测试），采用关键词匹配与 LLM 幻觉检测的混合评分方式。

```bash
# 运行全部 34 题评测
python -m evaluation.runner

# 只跑 30 题标准集（省去对抗测试）
python -m evaluation.runner --standard-only

# 只跑 4 题对抗性测试
python -m evaluation.runner --adversarial-only

# 跳过 LLM 幻觉检测（节省 API 调用费用）
python -m evaluation.runner --no-llm-check

# 指定题号范围
python -m evaluation.runner --cases 1-10
python -m evaluation.runner --cases 11,15,20
```

评测结果会保存在 `evaluation/results/` 目录下（已 gitignore）。

## 环境变量说明

| 变量名 | 必填 | 说明 |
|--------|------|------|
| `ZHIPU_API_KEY` | 是 | 智谱开放平台 API Key |

## 许可证

按项目需要自行补充。
