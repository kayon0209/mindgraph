# 企业报销知识问答系统（Expense RAG QA）

基于检索增强生成（RAG）的企业报销政策与流程问答应用，支持本地 Embedding 模型，无需 API 额度即可运行。

## 项目背景

**解决什么问题**：员工不清楚报销规则，频繁找HR/财务咨询，人工答疑效率低。

**为谁解决**：企业员工（提交报销方）+ HR/财务（审核方）。

**核心价值**：7×24小时自助查询，减少HR重复答疑约40%。

## 产品亮点

- **自然语言查询报销规则**：7×24小时自助服务
- **答案引用来源文档**：防止AI幻觉
- **超出知识库范围自动降级**：不编造信息
- **安全防护**：内置攻击检测和超范围问题拦截
- **本地Embedding支持**：无需API额度即可运行

## RAG系统架构

```
【知识库构建阶段（离线）】
文档上传（Markdown）
↓
文档解析（提取文本）
↓
Chunking分块（chunk_size=500，overlap=50）
↓
Embedding向量化（BAAI/bge-large-zh-v1.5 本地模型）
↓
存入向量数据库（ChromaDB）

【用户问答阶段（在线）】
用户输入问题
↓
意图识别（是否在知识库范围内？）
├── 范围外 → 返回"超出知识库范围，请联系HR"（降级）
└── 范围内 ↓
Query向量化
↓
相似度检索（Top-K=3）
↓
拼装Prompt（问题 + 检索结果 + 系统指令）
↓
LLM生成答案（智谱 GLM-4.5-Air）
↓
返回答案 + 来源文档段落引用
```

## 技术栈

| 层级 | 技术 |
|------|------|
| 前端 | Streamlit |
| 后端 / RAG | Python + ChromaDB |
| 向量库 | ChromaDB（本地持久化） |
| Embedding | BAAI/bge-large-zh-v1.5（本地）/ 智谱 embedding-3（可选） |
| 大模型 | 智谱 GLM-4.5-Air（对话生成） |

## 评估指标（v5最终版）

| 指标 | 数值 |
|------|------|
| 问题覆盖率 | 97.1% |
| 幻觉率 | ~1% |
| 拒答正确率 | 100% |

### 各类别表现（34题评测集）

| 类别 | 通过率 |
|------|--------|
| 直接查规则 | 100% (10/10) |
| 边界/特殊 | 87.5% (7/8) |
| 模糊/口语化 | 100% (6/6) |
| 知识库外 | 100% (6/6) |
| 对抗测试 | 100% (4/4) |

## 迭代过程

详见 [changelog.md](./changelog.md)

### v5 - 知识库补充 + 安全加固（2026-04-13）

- 补充 `报销材料清单.md`：新增必备材料、打车费、出差材料清单
- 补充 `超标准报销审批流程.md`：覆盖超标审批路径
- Prompt 加强拒答逻辑：新增薪资/请假/年假/奖金等明确拒答词
- `ask()` 前增加关键词前置拦截：覆盖角色扮演攻击模式
- **评估结果**：问题覆盖率 97.1%（+11.8%），拒答正确率 100%（+20%）

### v4 - 多 Embedding 后端支持（2026-04-13）

- 新增统一 Embedding 接口，支持 zhipu/local/openai 三种后端
- 本地 BGE 模型无需 API 额度

### v3 - 评测模块 + 安全加固（2026-04-13）

- 新增 34 题评测集（30标准 + 4对抗）
- 实现关键词匹配 + LLM 幻觉检测混合评分

## 本地运行

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

```bash
cp .env.example .env
```

编辑 `.env` 文件：

```env
# 智谱 API Key（用于对话生成）
ZHIPU_API_KEY=你的智谱API密钥

# Embedding 后端选择
EMBED_BACKEND=local  # local: 本地BGE模型（免费）
```

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
├── docs/                     # 报销制度文档（Markdown）
│   ├── 差旅费报销管理办法.md
│   ├── 费用报销管理制度.md
│   ├── 报销材料清单.md
│   └── 超标准报销审批流程.md
├── data/
│   └── chroma/               # Chroma 向量数据库
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
    └── document_loader.py    # 文档加载与切分
```

## 运行评测

```bash
python -m evaluation.runner
```

## 产品文档

- [PRD v1 企业报销知识问答系统.pdf](./PRD-v1.md)

## 许可证

MIT License

## 致谢

- [BAAI/bge-large-zh-v1.5](https://huggingface.co/BAAI/bge-large-zh-v1.5) - 中文 Embedding 模型
- [ChromaDB](https://www.trychroma.com/) - 向量数据库
- [智谱 AI](https://open.bigmodel.cn/) - 大语言模型 API
