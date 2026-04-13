# 企业报销知识问答系统（Expense RAG QA）

基于检索增强生成（RAG）的企业报销政策与流程问答应用，支持本地 Embedding 模型，无需 API 额度即可运行。

## 功能特性

- **智能问答**：基于企业报销制度文档，回答员工关于报销政策的问题
- **本地 Embedding**：支持本地 BGE 模型，无需联网即可进行向量检索
- **安全防护**：内置攻击检测和超范围问题拦截
- **评测系统**：34 题评测集，覆盖直接查询、边界情况、对抗攻击等场景

## 技术栈

| 层级 | 技术 |
|------|------|
| 前端 | Streamlit |
| 后端 / RAG | Python + ChromaDB |
| 向量库 | ChromaDB（本地持久化） |
| Embedding | BAAI/bge-large-zh-v1.5（本地）/ 智谱 embedding-3（可选） |
| 大模型 | 智谱 GLM-4.5-Air（对话生成） |

## 快速开始

### 1. 克隆项目

```bash
git clone https://github.com/pleaselikeme/expense-rag-qa.git
cd expense-rag-qa
```

### 2. 创建虚拟环境

```bash
python -m venv .venv
. .venv\Scripts\Activate.ps1  # Windows
# source .venv/bin/activate   # Linux/Mac
```

### 3. 安装依赖

```bash
pip install -r requirements.txt
```

### 4. 配置环境变量

复制环境变量模板并填入你的 API Key：

```bash
cp .env.example .env
```

编辑 `.env` 文件：

```env
# 智谱 API Key（用于对话生成）
ZHIPU_API_KEY=你的智谱API密钥

# Embedding 后端选择
# - local: 使用本地 BGE 模型（免费，推荐）
# - zhipu: 使用智谱 embedding-3（需要额度）
EMBED_BACKEND=local
```

> **获取 API Key**：前往 [智谱 AI 开放平台](https://open.bigmodel.cn/) 注册并创建 API Key

### 5. 启动应用

```bash
streamlit run src/app.py
```

浏览器会自动打开 `http://localhost:8501`。

### 6. 构建知识库

首次使用时，点击侧边栏的 **重建索引** 按钮，将 `docs/` 目录下的制度文档向量化并写入 ChromaDB。

## 项目结构

```
expense-rag-qa/
├── README.md                 # 项目说明
├── changelog.md              # 版本更新日志
├── requirements.txt          # Python 依赖
├── .env.example              # 环境变量模板
├── .gitignore
├── docs/                     # 报销制度文档（Markdown）
│   ├── 差旅费报销管理办法.md
│   ├── 费用报销管理制度.md
│   ├── 报销材料清单.md
│   └── 超标准报销审批流程.md
├── data/
│   └── chroma/               # Chroma 向量数据库（自动生成）
├── evaluation/               # 评测系统
│   ├── test_cases.py         # 34 题测试集
│   ├── scorer.py             # 评分逻辑
│   └── runner.py             # 评测运行器
└── src/
    ├── app.py                # Streamlit 入口
    ├── config.py             # 配置管理
    ├── rag_engine.py         # RAG 核心引擎
    ├── embedder.py           # Embedding 统一接口
    ├── local_embedder.py     # 本地 BGE 模型
    ├── document_loader.py    # 文档加载与切分
    └── special_cases.py      # 特殊情况处理
```

## 评测结果

运行 34 题评测集：

```bash
python -m evaluation.runner
```

### v2 版本评测结果（2026-04-13）

| 指标 | 数值 |
|------|------|
| **通过率** | 97.1% (33/34) |
| **平均综合分** | 0.98 |
| **拒答正确率** | 100% |

### 各类别表现

| 类别 | 通过率 |
|------|--------|
| 直接查规则 | 100% (10/10) |
| 边界/特殊 | 87.5% (7/8) |
| 模糊/口语化 | 100% (6/6) |
| 知识库外 | 100% (6/6) |
| 对抗测试 | 100% (4/4) |

## 版本历史

### v2 - 知识库补充 + 安全加固（2026-04-13）

- 补充报销材料清单和超标准审批流程文档
- Prompt 加强拒答逻辑，覆盖薪资/请假/年假等超范围问题
- 增加关键词前置拦截，防御角色扮演攻击
- 评测通过率：85.3% → 97.1%

### v4 - 多 Embedding 后端支持（2026-04-13）

- 支持 zhipu/local/openai 三种 Embedding 后端
- 本地 BGE 模型无需 API 额度

### v3 - 评测模块 + 安全加固（2026-04-13）

- 新增 34 题评测集
- 实现关键词匹配 + LLM 幻觉检测混合评分

## 许可证

MIT License

## 致谢

- [BAAI/bge-large-zh-v1.5](https://huggingface.co/BAAI/bge-large-zh-v1.5) - 中文 Embedding 模型
- [ChromaDB](https://www.trychroma.com/) - 向量数据库
- [智谱 AI](https://open.bigmodel.cn/) - 大语言模型 API
