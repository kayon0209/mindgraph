# Changelog

## v2 — 知识库补充 + 安全加固（2026-04-13）

### 改动内容
- 补充 `报销材料清单.md`：新增必备材料、打车费、出差材料清单
- 补充 `超标准报销审批流程.md`：覆盖超标审批路径
- Prompt 加强拒答逻辑：新增薪资/请假/年假/奖金等明确拒答词
- `ask()` 前增加关键词前置拦截：覆盖角色扮演攻击模式

### 评估结果（34题）
- 问题覆盖率：97.1%（+11.8%）
- 拒答正确率：100%（+20%）
- 幻觉率：~1%

### 遗留问题
- id18「出差超标准补什么手续」：知识库缺口，制度文件中无此条款，建议后续补充或标注为"需联系HR确认"

## v4 — 多 Embedding 后端支持（2026-04-13）

- 新增 `src/embedder.py`：统一 Embedding 接口，支持多种后端（zhipu / local / openai）。
- 新增 `src/local_embedder.py`：本地 BGE-large-zh-v1.5 模型支持，无需 API 额度。
- 更新 `src/rag_engine.py`：使用统一 embedder 接口。
- 更新 `.env.example`：添加 `EMBED_BACKEND` 配置说明。
- 更新 `README.md`：详细说明 Embedding 后端切换方法。
- 更新 `requirements.txt`：添加 `sentence-transformers` 依赖。

## v3 — 评测模块 + 安全加固（2026-04-13）

- 生成模型从 `glm-5.1` 降级为 `glm-4.5-air`，大幅降低 API 调用成本。
- 新增 `evaluation/test_cases.py`：30 题标准测试集（直接查规则 10、边界/特殊 8、知识库外 6、模糊/口语化 6）+ 4 题对抗性测试。
- 新增 `evaluation/scorer.py`：关键词匹配 + LLM 幻觉检测混合评分（0.6:0.4 权重）。
- 新增 `evaluation/runner.py`：CLI 评测运行器，支持按类别/题号筛选、关闭 LLM 检测，输出 JSON 报告。
- `config.py` 新增 API Key 缺失警告（启动时即提示，不静默失败）。
- `.gitignore` 新增 `evaluation/results/` 忽略评测结果文件。
- README 重写：强调用户需自行申请 API Key 并在 `.env` 中配置，确保 clone 后可直接运行。

## v2 — RAG 与智谱接入（2026-04-11）

- 新增 `src/config.py`：加载 `.env` 中的 `ZHIPU_API_KEY`，配置 `docs/`、`data/chroma/` 路径及模型名（`glm-4-flash`、`embedding-3`）。
- 实现 `document_loader.py`：扫描 `docs/*.md` 并按字符窗口切分。
- 实现 `rag_engine.py`：智谱向量写入 Chroma、检索、`glm-4-flash` 生成回答；Streamlit 侧栏支持「重建索引」与「强制重建」。
- 更新 `app.py`：对话界面与参考片段展示；提供 `.env.example`、`.gitignore`、`python-dotenv` 依赖。

## v1 — 初始化（2026-04-11）

- 建立项目目录：`src/`（Streamlit 入口、`rag_engine`、`document_loader` 占位）、`docs/`、`evaluation/`。
- 添加 `README.md`、`PRD.md`、`requirements.txt`。
- Streamlit 首页为 Hello World 占位；RAG 与文档管线尚未实现，见各模块文件内注释说明。
