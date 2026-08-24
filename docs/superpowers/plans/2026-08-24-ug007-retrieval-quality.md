# UG-007: 检索质量与证据可观测性（第一实施单元）

> **执行方式：** 按任务逐项执行；每项完成后先审查再进入下一项。

**目标：** 为 MindGraph 建立一个离线、可复现的检索评测基线。评测必须以独立黄金集为输入，量化证据是否在最终上下文中被保留，并能定位问题发生在召回、排序还是最终上下文截断；产物默认不得泄露用户问题正文。

**范围边界：** 本单元只实现离线检索评测与报告，不改在线检索排序、不新建/迁移数据库、不修改 CI 配置。当前 `mindgraph_golden.jsonl` 只有 12 条样本，禁止伪造样本或设置会产生虚假信心的硬阈值；扩充到路线图要求的 50--200 条独立样本后，才能把回归阈值接入 CI。

**约束：**

- 黄金标签必须来自 `evaluation/datasets/mindgraph_golden.jsonl`，不能从运行时 SQLite/检索结果反推。
- 仅 `expected_behavior == "answer"` 的用例参与检索证据指标；`abstain` 用例必须在报告中单独计数，回答/拒答正确性继续由既有 `answer_eval` 负责，不能把“没有证据”误报为“正确拒答”。
- 评测报告默认只输出 `case_id` 等非敏感标识；只有显式 `include_questions=True` 才能包含问题正文。
- 结果使用可注入的检索回调和内存 `RetrievalTrace`，单元测试不得依赖 API、向量模型、运行时数据库或网络。

## 任务 1：实现黄金集校验与检索证据评测器

**文件：**

- 新建：`evaluation/mindgraph_retrieval_eval.py`
- 新建：`tests/test_mindgraph_retrieval_eval.py`

**接口与行为：**

1. 提供 `load_golden_dataset(path=...)` 与 `validate_golden_cases(cases)`：校验 JSONL 对象、必填字段、`case_id` 唯一、`expected_behavior` 值、`answer` 用例至少一个 `gold_vault_paths`、`abstain` 用例不得含黄金路径。错误要携带能定位的 `case_id` 或行信息。
2. 提供 `evaluate_retrieval_cases(cases, retrieve, *, top_k=5, include_questions=False)`，其中 `retrieve(case)` 返回 `src.retrieval.types.RetrievalTrace`。从 `dense_results`、`sparse_results`、`fused_results`、`reranked_results`、`final_selected_chunks` 的 `chunk.metadata["vault_path"]` 读取证据路径。
3. 对每个回答用例计算并汇总：最终证据路径的 recall@k、precision@k、任一黄金路径首次出现位置的 MRR、以及证据最远到达阶段（`not_retrieved`、`retrieved_not_ranked`、`ranked_not_final`、`final`）。阶段归因必须依据各阶段真实结果，不能根据分数猜测。
4. 对拒答用例报告 `scored: false` 和明确原因，但不把它们混入上述指标的分母；报告同时给出回答/拒答的样本计数。
5. 输出 JSON 可序列化的稳定报告，含数据集版本、汇总指标、逐例明细与失败用例。默认逐例明细不可含 `question`，显式开启才可出现。

**测试（先写红测）：**

- 合法数据集加载与不合法/重复用例的定位错误。
- 各阶段的召回、排序丢失、最终截断三种归因，以及 top-k / MRR / precision / recall 聚合值。
- `abstain` 不计入检索指标且报告可见。
- 默认报告中不含任何问题正文，显式开启时才保留。

**验收：**

```powershell
.venv\Scripts\python.exe -m pytest tests/test_mindgraph_retrieval_eval.py -q
```

## 任务 2：校正评测数据集说明与运行指引

**文件：**

- 修改：`evaluation/datasets/README.md`
- 可选修改：`evaluation/README.md`（仅在现有目录确实存在且内容需要衔接时）
- 修改：`tests/test_mindgraph_retrieval_eval.py`（为真实 V2 数据集增加结构性回归测试）

**行为：**

1. 明确 V2 黄金集目前有 12 条、独立于运行时数据库，且当前阶段的目标是评测机制有效性而非统计显著性。
2. 说明新增样本的标签规则、`answer`/`abstain` 边界、版本化规则和扩样后再启用阈值门禁的条件。
3. 给出不依赖模型/API 的测试命令；不得把尚不存在的 `run_ablation.py` 写成当前入口。
4. 测试应直接加载仓库内 V2 数据集并保证其能通过校验，但不得对当前样本数伪设 50 条下限。

**验收：**

```powershell
.venv\Scripts\python.exe -m pytest tests/test_mindgraph_retrieval_eval.py tests/test_answer_evaluation.py -q
```

## 任务 3：集成审查与验证

**文件：**

- 仅在前两项发现实际缺口时修改对应文件；禁止为通过检查添加绕过逻辑。

**验证：**

```powershell
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m ruff check src evaluation tests
```

**提交：**

```powershell
git add evaluation/mindgraph_retrieval_eval.py tests/test_mindgraph_retrieval_eval.py evaluation/datasets/README.md
git commit -m "feat: add retrieval quality evaluation baseline"
```

推送、创建 PR 与合并均不在本计划授权范围内，完成本地验证后报告结果并等待用户决定。
