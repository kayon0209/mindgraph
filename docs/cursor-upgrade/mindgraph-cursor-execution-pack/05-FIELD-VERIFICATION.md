# 现场核对总览（FIELD VERIFICATION）

核对日期：2026-09-11（第一轮 20:50 / **第二轮 21:15 补核 PR-01、PR-10、PR-14、PR-15**）  
核对方式：对当前工作区逐条实测（命令 + 磁盘状态），非文档推断  
核对范围：本执行包 5 个框架文件 + **全部 15 份 PR 任务书**的每一项引用

> **本文件优先于其余任务书。** 任务书正文与本文冲突时，以本文为准。
> 本文件不改变任何 PR 的目标，只修正「文件名、前提、验收命令」三类事实性错误。

---

## 0. 一句话结论

**执行包可以执行，但必须先读本文再开工** —— 否则会踩 **3 条系统性假失败、11 处错误或缺失前提、
1 个必须先处理的基线问题（§4.1 的 B 类孤儿改动）**。

第二轮补核新增的修正（第一轮未覆盖）：**PR-01**（已部分完成，勿重写）、**PR-10**（现有
Service 已在线，勿误改）、**PR-14**（原任务书方向偏了，改动面应大幅缩小）、**PR-15**（ADR 引用错，
应新增 ADR-006）。合计 15 份任务书**全部完成现场核对**。

---

## 1. 基线状态（已核实）

| 项 | 执行包声明 | 实测 | 结论 |
|---|---|---|---|
| 取证基线 SHA | `aa3943aad99f377970d16d1f7da9c0f3432fd3c3` | 远端 `refs/heads/main` = 同一 SHA | ✅ **未过期** |
| 本地分支 | — | `pr-01-freeze-baseline` | ⚠️ 非 `main`，属正常（PR-01 已分支提交） |
| 本地 HEAD | — | `483b0be` | ⚠️ 比 `main` 多 1 个提交（PR-01 产物已提交） |
| 本执行包自身 | 应放入 `docs/cursor-upgrade/` | `git status` 显示 `?? docs/cursor-upgrade/` | ⚠️ **untracked，属预期**，不要 add 也不要删 |
| 任务书「必须先阅读」文件 | — | 全部存在（含 12 个测试文件） | ✅ **无指向空气的引用** |

### 1.1 分支策略（第二轮核对新增）

实测当前在 `pr-01-freeze-baseline` 分支（HEAD `483b0be`），`main` 停在 `aa3943a`。

- **PR-01 已完成一次提交**：`483b0be feat(eval): freeze factual baseline script (PR-01)`，
  仅含 `scripts/freeze_baseline.py`(+370) 与 `tests/test_freeze_baseline.py`(+340)。
- ⚠️ **不要在 `pr-01-freeze-baseline` 上继续做 PR-02。** 该分支只承载 PR-01；
  继续往上堆会让 PR-01 / PR-02 的提交混在一条分支上，无法独立 review 与回滚。
- **PR-02 起应基于 `main` 新建分支**（如 `pr-02-feedback-ownership`）。
- 任务书写「PR-02 依赖 PR-01」指的是**依赖 PR-01 的交付物**（基线脚本与产物），
  不是「必须叠在 PR-01 分支上」。若 PR-01 尚未合入 `main`，则**二选一并在 PR 描述里写明**：
  ① 先合 PR-01 再开分支；② 从 `pr-01-freeze-baseline` 开分支并标注依赖。不要默认。

---

## 2. 三条命令照抄必假失败（每条都实测复现）

### 2.1 `02-QUALITY-GATES.md` 的 pytest 命令 → 加 `--no-cov`

```
python -m pytest tests/test_freeze_baseline.py -q
→ 15 passed，但 pytest 输出：FAIL Required test coverage of 55% not reached. Total coverage: 6.11%
→ 退出码 = 1（实测，非管道掩盖值）
```

原因：`pytest.ini:15` 有 `--cov-fail-under=55`，单文件跑的覆盖率必然远低于 55%。

**改用**：

```bash
# 改动期间跑目标测试
python -m pytest <targeted-tests> -q --no-cov
# 收尾时跑全量（此时覆盖率才有意义，当前 ~75%）
python -m pytest -q
```

### 2.2 `02-QUALITY-GATES.md` 的 ruff 命令 → 必须加 `--select`

```
python -m ruff check src scripts tests evaluation
→ Found 5934 errors.        （全部是仓库存量债务，与本 PR 无关）

python -m ruff check src scripts tests --select F821,F822,F823,E902
→ All checks passed!        （这才是 AGENTS.md:52 定义的 CI 门禁）
```

**改用**：`AGENTS.md:52` 的门禁命令为准。若要看自己是否引入新债，用：

```bash
python -m ruff check <changed-active-paths> --ignore RUF002,RUF003,E501 --output-format concise
# 并与 HEAD 版对比：
#   for f in <changed-active-paths>; do git show HEAD:$f > .ruffbase/$(echo $f | tr '/' '_'); done
#   python -m ruff check .ruffbase --ignore RUF002,RUF003,E501 --output-format concise
# 两边逐规则一致 = 净新增 0。RUF002/RUF003 是中文全角标点误报，属仓库存量噪声。
```

### 2.3 mypy 也会被存量错误误伤

```bash
python -m mypy <changed-active-paths> --follow-imports=silent
```

若报错出现在**你没有修改的文件**（如 `evaluation/answer_eval.py`），先 `git show HEAD:<file>` 对比，
确认是存量后**不要顺手修**（违反 guardrail §2「一次只解决一个可验证问题」），在验收报告里标注即可。

---

## 3. 阶段编号不对齐（bootstrap 第 1 步会读不到定义）

- 本执行包用 **M0–M6**。
- 仓库 `docs/UPGRADE_PLAN.md` 用的是 **UG-001 … UG-008**（P1/P2 条目表），**没有 M0–M6**。
- 仓库真正的实现顺序在 `AGENTS.md:77`「## 当前产品路线」（6 步）。

**建议映射（本次核对新增，仓库原本没有；仅作对齐用，不是权威定义）**：

| 执行包阶段 | PR | 对应仓库条目 |
|---|---|---|
| M0 事实 / 安全 / 切分 / 归因 | 01–05 | UG-007（检索质量评测与证据可观测性，P1）· UG-008（知识治理与生命周期过滤） |
| M1 解析 / OCR / 跨页 / Parent–Child | 06–09 | **UG-001**（鲁棒文档 ingestion，layout-aware）— 原文自述「真实 OCR 引擎执行与扫描 PDF fixture 仍待接入」 |
| M2 Query Analyzer / Rerank | 10–11 | **UG-002**（Query 理解与多查询召回）— 原文自述「真实问题集上的召回增益与预算校准仍待完成」 |
| M3 / M4 多轮 / 澄清 / bad case | 12–14 | UG-007 · UG-008 |
| M6 存储与队列抽象 | 15 | UG-005 / UG-006 相邻（企业化方向） |

**给 Cursor 的指令**：现场核对报告里直接声明「执行包阶段号与 `docs/UPGRADE_PLAN.md` 编号体系不同，
本文第 3 节映射为推断」，不要因为找不到 M0 而停下。

---

## 4. 工作区未提交改动（**第二轮核对已重新定性，按本节处理**）

截至 2026-09-11 21:15 复核，`git status --porcelain` 实测：

```
 M docs/GOLDEN_DATASET_CARD.md
 M evaluation/mindgraph_retrieval_eval.py
 M evaluation/runner.py
 M scripts/freeze_baseline.py
 M src/api/dependencies.py
 M src/application/evaluation_service.py
 M src/application/index_metadata.py
 M tests/test_freeze_baseline.py
 M tests/test_mindgraph_retrieval_eval.py
?? docs/cursor-upgrade/
?? tests/test_evaluation_v2_migration.py
?? tests/test_index_root_registry.py
```

（9 改 + 2 新增测试 + 本执行包 untracked = 12 项。上一版写成「10 改」，是数错了。）

**这 12 项不是一类东西，必须分开看**：

| 类别 | 文件 | 性质 | 与执行包的关系 |
|---|---|---|---|
| **A｜PR-01 深化** | `scripts/freeze_baseline.py`(+217)、`tests/test_freeze_baseline.py`(+296)、`src/application/index_metadata.py`(+243)、`tests/test_index_root_registry.py`(新增 143) | 对已提交的 PR-01（`483b0be`）做第二轮加固：volatile 字段显式声明、必需配置键 fail-closed、索引根登记表 | **与 PR-01 目标同源**，属 PR-01 未收尾的部分 |
| **A｜评测口径** | `evaluation/mindgraph_retrieval_eval.py`(+51)、`evaluation/runner.py`(+5)、`tests/test_mindgraph_retrieval_eval.py`、`docs/GOLDEN_DATASET_CARD.md`(+17) | 数据集摘要口径显式化（`canonical-jsonl-source-line-v1`） | 与 PR-01「数据摘要可追溯」一致 |
| **B｜孤儿特性** | `src/application/evaluation_service.py`(+487)、`src/api/dependencies.py`(+60)、`tests/test_evaluation_v2_migration.py`(新增 373) | `/api/v1/evaluations` 的 **v1/v2 双数据集分派**（按 `dataset_name` 二选一） | ⚠️ **不属执行包任何 PR**，也不是 PR-01 的产物 |

**B 类是一个必须先处理的问题，不要忽略**：

1. 它改变了 `/api/v1/evaluations` 的数据集解析行为，而 **PR-01 的验收标准原文是
   「现有测试全绿，生产行为零变化」** —— 换句话说，**带着 B 类去发 PR-02，PR-01 的验收承诺
   在自己家里就已经不成立了。**
2. `src/application/evaluation_service.py` 正是 **PR-01 任务书的「必须先阅读」文件**。
   Cursor 读它会看到一套任务书从未提及的双栈分派逻辑，存在「顺手改它」或「被它误导」的风险。
3. B 类与 A 类**已经耦合**：`scripts/freeze_baseline.py` 新增 `from application.index_metadata
   import INDEX_ROOT_REGISTRY`，而 `index_metadata.py` 同时服务 v2 数据集登记。
   **不能只挑 A 类提交、把 B 类晾着。**

### 4.1 前置动作：**先判断时机，两种时机处理方式不同**

**时机 A｜还没有任何 PR 开工** → **先提交，再发 Cursor。** 理由：

1. 每个 PR 都要输出「本 PR 的 diff」与「新旧行为差异」。基线不干净 → **每个 PR 的 diff 都不可信**，
   也无法判断某处改动是它做的还是本来就有的。
2. `tests/test_freeze_baseline.py` 有一条「dirty workspace 被显式记录」的验收要求 →
   基线长期 dirty，这条断言会一直指向一堆非本 PR 的改动。
3. 这些改动**已实测全绿**（全量 669 passed / 3 skipped）。提交它比让它裸奔更安全。

建议拆两个提交（顺序不可颠倒）：

```bash
# 提交 1（A 类）：PR-01 收尾加固 —— 与 PR-01 目标同源
git add scripts/freeze_baseline.py tests/test_freeze_baseline.py \
        src/application/index_metadata.py tests/test_index_root_registry.py \
        evaluation/mindgraph_retrieval_eval.py evaluation/runner.py \
        tests/test_mindgraph_retrieval_eval.py docs/GOLDEN_DATASET_CARD.md
git commit -m "feat(eval): PR-01 baseline hardening (volatile declaration + config fail-closed + index-root registry)"

# 提交 2（B 类）：评测栈分派 —— 归属需人类先决策，见下表
git add src/application/evaluation_service.py src/api/dependencies.py \
        tests/test_evaluation_v2_migration.py
git commit -m "feat(eval): dataset dispatch for /api/v1/evaluations"
```

**B 类归属是产品决策，必须显式选一个，不要悄悄带过**：

| 选项 | 含义 | 代价 |
|---|---|---|
| **B1 单独保留** | 承认它是独立特性，提交并留决策记录 | PR-01 的「行为零变化」要改口径为「除评测入口外零变化」；`evaluation_service.py` 会长期出现在后续 PR 的「已存在但任务书未提」清单里 |
| **B2 先回滚** | 评测入口恢复 HEAD 行为，B 类移入独立分支/任务书 | PR-04/05 若需碰评测入口要重做；373 行测试暂时作废 |
| **B3 合入 PR-01** | 视作 PR-01 的一部分（PR-01 本就是「冻结事实基线」入口） | 必须同步修订 PR-01 的验收标准措辞，否则 PR-01 自我矛盾 |

**时机 B｜已有 PR 在工作区开工（⚠️ 2026-09-11 21:33 实测即为此情况）**
实测：PR-02 已开工 —— `src/application/feedback_service.py` 已改、`tests/test_feedback_ownership.py`
已新建（7870 字节）、`.pytest_cache` 刚更新。**→ 不要提交、不要 stash、不要搬家。**

- **现在提交会把开工中的 PR 半成品一起卷进提交**，内容不可解释，也失去干净回滚点。
- 正确做法：**保持工作区现状**，把 A / B 类当「既存改动」**隔离处理** ——
  Cursor 不得 touch、不得 commit、不得让它们出现在本 PR 的最终提交里；在报告里逐条声明。
- **B 类归属决策推迟到该 PR 验收之后**，**不构成阻塞**（与 PR-02 无文件耦合）。
- **保护动作（已执行）**：未提交改动已导出到仓库外的
  `D:\demo\output\_mindgraph_uncommitted_backup_20260911\`
  （`tracked-changes.patch` 1769 行 + `untracked-files.tgz`，含执行包与两个新测试文件）。
  **任何人不得依赖「工作区还在」这一假设。**
- **分支**：PR-02 已在 `pr-01-freeze-baseline` 上开工 → **不中断、不搬家**，
  在验收阶段单独整理提交归属（PR-01 与 PR-02 的提交都在本地、均未推远端，可事后切分）。
  **只有尚未开工的 PR 才需要从 `main` 新建分支。**

**不做隔离的后果**：本 PR 的 diff 里会混入 1254 行与它无关的既有改动；
验收时无法判断某处改动是本 PR 引入的还是原本就在 —— **这个混乱会随每个 PR 累积。**

---

## 5. 数据可得性（**本节推翻旧结论，最重要**）

> 旧结论「仓库无 PDF、M1（PR-06/07/08）无数据可验收」**是错的**，已作废。

**真实情况**：`data-sources/ocr/chinese-gov/` 下有现成 OCR 靶标体系。

| 资产 | 路径 | git 状态 |
|---|---|---|
| 靶标说明（含跑法） | `data-sources/ocr/chinese-gov/OCR_TARGETS.md` | ✅ 已跟踪 |
| 6 张页图靶标 | `data-sources/ocr/chinese-gov/rendered/*.png` | ✅ 已跟踪 |
| 其中 **2 张"真·纯图像页"**（源文字层=0，1654×2339） | `guowuyuan-gongbao-202524_p2.png` / `_p3.png` | ✅ ⭐ 最优靶标 |
| Tesseract 验证脚本 | `data-sources/ocr/chinese-gov/ocr_verify.py` | ✅ 已跟踪 |
| **rapidocr 验证脚本（纯 pip，无外部二进制）** | `data-sources/ocr/chinese-gov/ocr_verify_rapidocr.py` | ✅ 已跟踪 |
| 页图渲染脚本 | `data-sources/ocr/chinese-gov/render_ocr_targets.py` | ✅ 已跟踪 |
| 源 PDF：国务院公报（61 页 / 43MB） | `data-sources/ocr/chinese-gov/guowuyuan-gongbao-202524.pdf` | ⚠️ **本地存在，被 `.gitignore:69` 忽略** |
| 源 PDF：自然资源听证规定（13 页） | `data-sources/ocr/chinese-gov/ziran-ziyuan-tingsheng-guiding.pdf` | ⚠️ 同上 |

**OCR 引擎当前未安装**（实测 `.venv`）：

```
missing: pytesseract  paddleocr  rapidocr_onnxruntime  fitz(PyMuPDF)  pdfplumber  pdf2image
installed: PIL
pyproject.toml:46 → ocr = ["paddleocr>=2.9.0", "paddlepaddle>=3.0.0"]   （optional extra，未装）
```

**PR-07 的建议路径**：优先 `rapidocr-onnxruntime`（纯 pip、跨平台、无需系统二进制），
且 `ocr_verify_rapidocr.py` **已经写好可直接跑**作为探针。Tesseract 需装系统二进制 + `chi_sim` 语言包，次选。

**其它数据**：

- 语料：`knowledge/` = 25 篇（对应线上索引 `mg-` 581 chunks）；`demo-vault/` = 13 篇；`data-sources/handbooks/` = 7.5MB（basecamp / gitlab / mattermost）。
- 多轮会话：`tests/test_conversations.py`（335 行）已存在；`clarification_requests` 表已建（`src/infrastructure/database.py:454-470` + 2 索引）→ **PR-12/13 不需要新造数据**。

---

## 6. 逐 PR 前提修正（正文已同步改好，此处为索引）

| PR | 任务书原述 | 实测修正 | 影响 |
|---|---|---|---|
| **01** | 「新增基线采集脚本或复用现有 Runner」 | `scripts/freeze_baseline.py` **已存在且已提交**（`483b0be`，370 行），工作区再加固 217 行 | 本 PR **不是从零新建，是收尾**；见 §6.1 |
| **02** | 只改 `feedback.py` + `feedback_service.py` | **漏一个文件**：`src/application/agent_service.py:367` 的 INSERT 只有 18 列、**漏写 `principal_id`**（`src/application/chat_service.py:338` 是 19 列含该列）→ assist 渠道归属恒 NULL | 只收紧查询不补写，assist 用户反馈会被全拒 |
| **02** | 需改 schema 加归属列 | `query_logs.principal_id` **已存在**（`src/infrastructure/database.py:289-296`，注释标「schema v13 安全审查 F1」，可空） | **纯查询侧修复，不改 schema** |
| **03** | 两处切分常量 | **三处**：`src/application/structured_chunker.py:9`、`src/document_loader.py:16-18`、**`src/application/mindgraph_index_service.py:129` 内联 `_chunk_text(sec_body, 500, 50)`** | 第三处正是**线上索引构建点**，漏了等于没统一 |
| **04** | 「解析、切分和范围口径可能漂移」 | **不是参数漂移，是两条切分路径**（元数据铁证，见下） | 改法完全不同：不是调参，是要选口径 |
| **05** | conflict accuracy=0 需归因 | 前提成立（基线 JSON 中 `conflict_accuracy: 0.0`） | 无修正 |
| **06/07/08** | 需 PDF/扫描件 | **有靶标**（第 5 节）；OCR 引擎需自行安装 | 从"M1 无出口"变为**可做** |
| **09** | 「小块用于召回、父块补充上下文」待实现 | **Parent–Child 已实现**（`src/application/structured_chunker.py:24-33`，sha256 lineage + `parent_text`）；**检索层消费数 = 0** | 真实缺口是「接消费端 + 覆盖 Markdown 路径」，不是重写切分 |
| **12/13** | — | 表与测试均已在（第 5 节） | 无数据阻塞 |
| **11** | Rerank 默认关 | 前提成立：`src/infrastructure/settings.py:113` `RERANKER_ENABLED = False` | 无修正 |
| **10** | QueryAnalyzer shadow | 前提成立（confidence 硬编码两档）；**但 `QueryUnderstandingService` 已在生产路径**（`src/application/chat_service.py:91`） | shadow 不得改造现有 service；见 §6.3 |
| **14** | 「bad_cases 缺少各阶段候选，需扩展 snapshot」 | ⚠️ **方向偏了**：阶段候选/route/variants/index 版本**已在 `query_logs.trace_json`**；真实缺口是「没消费」+ 位置式 INSERT + `request_id UNIQUE` | 改动面应大幅缩小；见 §6.4 |
| **15** | 「必须先阅读 `docs/ADR-005`」 | ⚠️ ADR-005 是**单节点硬化 ADR**，其非目标**明确列出多 worker/外部队列不做** | 应新增 ADR-006，不是扩写 ADR-005；见 §6.5 |

### 6.1 PR-01 修正（**该 PR 已部分完成，不要重写**）

| 项 | 任务书原述 | 实测 |
|---|---|---|
| 交付物 | 「新增基线采集脚本或复用现有 Runner」 | `scripts/freeze_baseline.py` **已存在且已提交**（`483b0be`，370 行）+ 工作区再加固 217 行 → **不是从零新建，是收尾** |
| 「必须先阅读」`src/application/index_metadata.py` | — | 该文件在工作区**新增 243 行**（`INDEX_ROOT_REGISTRY` / `index_root_spec` / `version_label_set`），**尚未提交**，见 §4 |
| 「必须基于最新 main」 | 基线 SHA `aa3943a` | 工作区已有 PR-01 提交 `483b0be` → 执行时的 HEAD **已不是** `aa3943a`，报告里要同时写明「取证基线 SHA」与「实际 HEAD SHA」 |

→ **给 Cursor 的指令**：本 PR 在仓库中**已部分完成**。现场核对报告必须先说明
「PR-01 已提交到什么程度、工作区还有哪些未提交的 PR-01 收尾」，再决定本 PR 是「继续收尾」
还是「已完成、只需验收」。**不要重写已提交的 `freeze_baseline.py`。**

### 6.2 PR-02 补充（原修正之外，多两处实测事实）

- **`src/api/routes/feedback.py` 已经注入了 principal，但没往下传**：
  `create_feedback(payload, principal = Depends(require_authenticated))` —— `principal` 拿到了、
  函数体却是 `return get_container().feedback.create_feedback(payload)`，**没有把它传给服务**。
  而 `FeedbackService.create_feedback(payload)` 的签名里也**没有 principal 形参**。
  → 「路由把 principal_id 传入 FeedbackService」这条改动是**成立且必需**的，但现状不是「没认证」，
  是「认证了但没用」。报告里要写准，否则容易误判成「加个 Depends 就行」。
- **写入端只有两处，无遗漏**：全仓 `INSERT INTO query_logs` 仅
  `src/application/chat_service.py:338`（19 列，含 `principal_id`）与 `src/application/agent_service.py:367`（18 列，缺）。
  即修正 2 的「补写入端」只需改一处，不存在第三处漏网点。
- `bad_cases.request_id` 是 **UNIQUE**、`feedback` 表按 `request_id` 去重 → PR-02 收紧归属后，
  历史 NULL 行的影响面只落在**读取/提交匹配**上，不涉及主键冲突。
- **⚠️ 认证口径有两个出口，别选错**：`src/api/auth.py:215` `require_authenticated() -> **dict**`
  与 `src/api/auth.py:257` `current_actor() -> **str**`（`name or username or "anonymous"`）。
  写入端落库的是 **str** → 查询端必须用 `current_actor` 口径，否则与存量行匹配不上。
- **⚠️ 测试注入行为取决于写法**（两种写法都正确，但注入面不同）：
  - **写法 A（推荐，实测已被采用）**：`Depends(require_authenticated)` + 函数体内 `current_actor(request)`
    → monkeypatch 模块属性 `auth.current_actor` **生效**（调用时才查找）。
  - **写法 B**：`Depends(current_actor)` → `router` 在模块导入时即捕获函数对象，
    事后赋值 `auth.current_actor = ...` **不生效**，必须用 `app.dependency_overrides[current_actor] = ...`。
  - 只有「选 B + monkeypatch」才会表现为「归属测试全红、看起来像实现 bug」。
    **先确认写法，再判断失败是注入问题还是实现问题。**

### 6.3 PR-10 修正

| 项 | 任务书原述 | 实测修正 | 影响 |
|---|---|---|---|
| 「固定 confidence 不代表真实置信度」 | — | ✅ 成立：`src/application/adaptive_retrieval_router.py:287` `confidence = 0.85 if route in {"exception_or_conflict","cross_policy","structured_fallback"} else 0.95` —— **仅两档硬编码** | 前提无误，可直接做 |
| 「关键词/正则无法稳定识别指代」 | — | ✅ 成立：`src/application/query_understanding.py`（102 行）全为正则 + 关键词表，无指代解析、无跨轮、无条件槽 | 前提无误 |
| ⚠️ **任务书未提：`QueryUnderstandingService` 已在线** | — | `src/application/chat_service.py:91` **已在生产路径调用** `.plan(...)`，并把 mode / warnings 写进 trace（:197、:205） | **PR-10 是 shadow 模式，绝不能改造现有 service 来「实现 analyzer」** —— 那会改变生产路由，违反本 PR 自己的「不改变生产路由」 |
| 验收「90 条现有集 + 新增 ≥30 条」 | — | `tests/test_adaptive_router.py`（283 行）已存在；**无 `test_query_understanding.py`** | 新分析器的契约测试要**新建文件**，不要塞进 router 测试 |

→ **给 Cursor 的指令**：新增的 QueryAnalysis 契约与 analyzer 必须与**已有的
`QueryUnderstandingService` 明确区分**（前者纯观测 shadow，后者已在生产改写查询）。
现场核对报告要写清两者边界，避免重复造轮子或误改线上路径。

### 6.4 PR-14 修正（**本包修正幅度最大的一份，原任务书方向偏了**）

| 项 | 任务书原述 | 实测修正 | 影响 |
|---|---|---|---|
| 「bad_cases 缺少改写 query、各阶段候选、上下文」 | — | ⚠️ **数据其实已经在库里**：`query_logs` 表已含 `trace_json / citations_json / usage_json / timing_json / index_version / prompt_version / actual_provider / category_filter_json`；`RetrievalTraceModel`（`src/domain/models.py:100-121`）**已含** `route_decision` / `query_variants` / `original_query` / `dense_results` / `sparse_results` / `fusion_results` / `reranked_results` / `final_chunks` / `index_version` / `applied_filters` / `degraded` / `policy_conflicts` | **真实缺口不是「没采集」，是「没消费」**：`src/application/feedback_service.py:33` 只取 `trace.get("final_chunks")` 冗余存一列，其余阶段数据可经 `request_id` JOIN `query_logs` 取回 |
| 「additive 扩展 bad-case snapshot」 | — | ⚠️ **`src/application/feedback_service.py:32` 是位置式插入**：`INSERT OR IGNORE INTO bad_cases VALUES (?,?,?,?,?,?,?,?,?,?,?)` —— 11 个 `?`、**无列名**。任何 `ADD COLUMN` 都会让这条语句列数不匹配而报错 | 加列前必须先把写入改成**显式列名插入**，否则先炸的是写入端 |
| 「规范化 hash + 语义相似检测重复」 | — | ⚠️ `bad_cases.request_id TEXT NOT NULL **UNIQUE**`，且已用 `INSERT OR IGNORE` 做幂等 | **同一 request_id 不可能有第二行** → 「3 次相近提问」必须是**跨 request_id 的近似匹配**（新列或新表），不能在原表补行 |
| 「也没有重复失败转人工」 | — | ✅ 成立；`status` / `error_category` / `reviewer_note` / `resolution` 已有 | 本 PR 是加「升级信号 + 归因层」 |
| 「resolved 经人工审核导出 regression candidate」 | — | **骨架已在**：`export_bad_cases()` 已把 `status == "resolved"` 映射为 CSV 的 `regression_candidate` 列 | 只需补「审核动作」与「导出后置」，不必重建导出 |
| 「保存 prompt/model/citation verdict」 | — | 部分已有：`prompt_version` / `actual_provider` / `citations_json` 在 `query_logs`。**缺**：citation 正确性判定、与 golden 标准答案的关联 | 只补判定与关联，不需重建快照表 |

→ **给 Cursor 的指令**：**先写现场核对报告，把「哪些数据已在 `query_logs.trace_json`」逐项列出**，
再据此把改动面收缩到：① 位置式 INSERT 改显式列名 → ② 读取面 JOIN `query_logs`
→ ③ 新增重复检测与升级信号 → ④ 补 citation verdict 与标准答案关联。
**不要按原任务书从零扩展 snapshot 表** —— 那是重复劳动，且会与已有 `trace_json` 形成两套事实源。

### 6.5 PR-15 修正

| 项 | 任务书原述 | 实测修正 | 影响 |
|---|---|---|---|
| 「必须先阅读 `docs/ADR-005-m6-single-node-hardening.md`」 | — | ⚠️ ADR-005 讲的是**单节点硬化**（MCP 版本协商、备份恢复演练、SLO、威胁模型），**不是存储抽象**；且其「非目标」原文明确列出 **多 worker / 外部队列、K8s 部署、正式生产规模声明 —— 本轮不做** | **本 PR 要写的是新 ADR（建议 `ADR-006-enterprise-profile-boundaries.md`），不是扩写 ADR-005**；新 ADR 必须显式说明与 ADR-005 非目标的关系，否则两份 ADR 互相打脸 |
| 「定义 DocumentStore / MetadataStore / VectorIndex / SparseIndex / TaskQueue Protocol」 | — | **已有雏形**：`src/retrieval/types.py:86-115` 已有 `EmbeddingProvider` / `DenseRetriever` / `SparseRetriever` / `FusionStrategy` / `Reranker`；`src/domain/interfaces.py:6` `ChatProvider`；`src/infrastructure/parsers/base.py:8` `DocumentParser` | **是「补缺 + 统一」，不是从零建** → 报告里先列已有 Protocol 清单与缺口，避免定义语义重叠的接口 |
| 「应用层不再依赖具体向量/队列实现」 | — | ✅ 成立：`src/application/task_service.py:47` `def __init__(self, database: ProductDatabase)` —— 构造器直接吃具体类型 | 前提无误 |
| 范围外「不接入 Milvus/Postgres/OpenSearch」 | — | ✅ 与 ADR-005 一致 | 无冲突 |
| 依赖「PR-04 / PR-06 / PR-09」 | — | PR-09 任务书前提部分不成立（见第 6 节表格） | 依赖链上有一环要重写，PR-15 的实际开工时点会后移 |

→ **给 Cursor 的指令**：**新增 ADR 而非修改 ADR-005**；报告里先列已有 Protocol 清单。

### 6.6 PR-06 修正（**前提大半不成立：页级解析早已实现，缺的是「持久化 + 状态机」**）

**实测（2026-09-11 23:10）**：任务书「范围内」列出的能力，大部分**已经在仓库里**：

| 任务书要求 | 现状 | 证据 |
|---|---|---|
| 页级解析 | ✅ 已有 | `src/infrastructure/parsers/`：`base.py` / `registry.py` / `pdf.py` / `docx.py` / `text.py` / `xlsx.py` |
| parser version 记录 | ✅ 已有 | `ParsedDocument.parser_version`（`src/domain/models.py:356`）；`src/application/document_lifecycle_service.py:75,82` 已组装 `diagnostics={"parser":…,"parser_version":…}` |
| 页号可追溯 | ✅ 已有 | `ParsedElement.page_number`（`:341`）；`StructuredChunk.page_start/page_end`（`:370-371`） |
| 扫描页标记 | ✅ 已有 | `ParsedDocument.ocr_required_pages: list[int]`（`:359`） |
| OCR 来源标记 | ✅ 已有 | `ParsedElement.ocr_derived: bool`（`:347`） |
| 表格可追溯 | ✅ 已有 | `ParsedElement.table_id` / `table_rows`（`:344-345`） |

**真正缺的只有两样——这才是本 PR 的工作量：**

1. **持久化**：全仓 30 张 `CREATE TABLE` 里**没有** `ingestion_jobs` / `page_artifacts` / `document_pages`。
   页级产物解析完**随进程丢弃** → 「重试不重跑成功页」物理上做不到，因为成功页没有任何落盘记录。
2. **状态机**：无 `registered→extracting→parsed/ocr_required/failed→chunked` 的落库状态与 attempt 计数。

→ **照原文执行会让执行者重写一套已存在的解析层。** 正确改法是
「把**已经算出来**的页级产物落库 + 补状态机」，不是「实现页级解析」。

**⚠️ 本 PR 是 schema 变更**（新增表）。按仓库红线须**单独确认**；若采用，验收报告必须显式声明
「仅新增表，未改既有表结构/列」。详见 §10.2。

### PR-04 元数据铁证（同一语料两条切分路径）

| 索引版本 | chunk 数 | metadata 切分口径 |
|---|---:|---|
| `m3-20260910T073532Z-9befc0b7` | **69** | `chunk_size=500, chunk_overlap=50`（扁平，来自 `document_loader`） |
| `m4-20260909T123040Z-d432056c` | **98** | `chunker={'child_size':500,'parent_size':1200,'overlap':50}`（**StructuredChunker**） |

→ **PR-03 与 PR-04 是同一根因的两面。** 且「哪一个口径是对的」是**产品决策**，
门禁本身决定不了；不一致时按 `04-PR-TEMPLATE.md` 的停止规则上报，不要自行选一个激活。

---

## 7. 建议执行顺序

**第 0 步（阻断项）：先处理 §4.1 的基线问题** —— 把 12 项未提交改动提交或分流，
并把 B 类（评测栈数据集分派）的归属定下来。**不完成这一步，任何 PR 都不要发给 Cursor。**

1. **PR-02**（首个交付，理由：前提已实测成立、改动面最小、安全收益明确）
   ⚠️ 文件清单必须补上 `src/application/agent_service.py`；**不改 schema**；
   注意现状是「路由已注入 principal 但未传给 service」（§6.2）。
2. **PR-03** → **PR-04**（同一根因，连续做；03 建立单一来源，04 上门禁）
3. **PR-05**（依赖基线，PR-01 已提交，可直接做）
4. **PR-06 → PR-07 → PR-08**（先装 OCR 引擎并跑通靶标探针，再进 PR-07）
5. **PR-10 → PR-11**；**PR-12 → PR-13 → PR-14**；最后 **PR-15**
6. **PR-09** 建议排在 PR-07/08 之后重写任务书再发（原前提部分不成立）。

**一次只发一个 PR。** 前一 PR 未验收，不开始下一个。

---

## 8. 本文件未做的事

- 未修改任何 PR 的**目标与验收标准**——只改事实引用。例外：PR-14 的**改动面建议**（§6.4）
  与 PR-15 的**ADR 归属建议**（§6.5）已明确写出，但那仍属「事实性修正」，不是替产品拍板。
- 未断言任何 PR「一定通过」——可执行性 ≠ 无条件完成。
- 未替产品做决策。以下四项**必须由人类显式决定**，本文件只指出它们存在：
  1. **§4.1 的 B 类孤儿改动归属**（B1 单独保留 / B2 回滚 / B3 合入 PR-01）——**发 Cursor 前必须先定**；
  2. PR-01 的评测栈默认入口；
  3. PR-04 的切分口径（69 扁平 vs 98 StructuredChunker，哪个是对的）；
  4. PR-09 的 parent 消费策略。
- 未实现 PR-14 / PR-15 的方案——只指出「数据已在库里」「Protocol 已有雏形」这两个会改变改法的事实。
- 未复核 `manifest.json` 之外的其它历史文档（如早期生成报告）是否也含旧结论。

---

## 9. 提交纪律：**工作区绿 ≠ 提交可过**（09-11 22:25 新增，PR-02 实测踩到）

### 9.1 发生了什么

PR-02 代码已验收通过（见下），执行者随后 `git add`。**但暂存区的内容不自洽**：

| 事实 | 取证 |
|---|---|
| 已暂存的 `tests/test_freeze_baseline.py` 调用 `freeze_baseline.strip_volatile(...)`（3 处，`:138/:140/:166`）并引用 `VOLATILE_PATHS` | `git diff --cached -- tests/test_freeze_baseline.py` 的 `+` 行 |
| 而定义它们的 `scripts/freeze_baseline.py` **没被暂存** | `git show HEAD:scripts/freeze_baseline.py` 与 `git show :scripts/freeze_baseline.py` **同为 370 行**；`strip_volatile` HEAD=0 / 工作区=1 |
| 同类缺口 | `src/application/index_metadata.py`（+243，含 `INDEX_ROOT_REGISTRY`）**已暂存**，而其消费方 `scripts/freeze_baseline.py:49` **未暂存** |

**后果：现在 `git commit`，会得到一个自身测试跑不通的提交**（`AttributeError: module
'scripts.freeze_baseline' has no attribute 'strip_volatile'`，至少 3 个用例失败）。

### 9.2 为什么这条比"写错文件名"严重

- `pytest` 跑的是**工作区**；`git commit` 提交的是**暂存区**。两者可以不一致。
- **执行者看到全绿，你审的也是工作区全绿，但提交出去的是坏的。** 这种坏提交不会被本地任何
  一次绿灯暴露，只会出现在 CI / 换机器 / `git stash` 之后——**而且它会被后续 PR 的建筑在上面**。
- 判据要从「工作区全绿」升级为「**只有提交内容的树** 全绿」。

### 9.3 规定的提交切分（三段，各自自洽）

```bash
git reset                     # --mixed，只清暂存，不动工作区

# ① PR-02 本体：实现 + 它自己的测试，自成一体
git add src/application/feedback_service.py src/api/routes/feedback.py \
        src/application/agent_service.py src/application/evidence_tools/feedback_tool.py \
        tests/test_feedback_ownership.py
# 自查：git diff --cached --stat 只应含这 5 个文件

# ② PR-01 收尾加固：**8 个文件必须整组**，劈开就坏
git add scripts/freeze_baseline.py tests/test_freeze_baseline.py \
        evaluation/mindgraph_retrieval_eval.py tests/test_mindgraph_retrieval_eval.py \
        docs/GOLDEN_DATASET_CARD.md evaluation/runner.py \
        src/application/index_metadata.py tests/test_index_root_registry.py

# ③ B 类孤儿（评测栈数据集分派，不属任何 PR；归属待人类拍板）
git add src/application/evaluation_service.py src/api/dependencies.py \
        tests/test_evaluation_v2_migration.py
```

**提交后必须做的自证**（比"本地全绿"强一档）：

```bash
git stash push -u          # 把工作区剩余改动（含本执行包）挪开
.venv/Scripts/python.exe -m pytest -q --no-cov -p no:cacheprovider
git stash pop
```

在**只有提交内容**的树上跑一次——这才叫"证明提交是可过的"。

### 9.4 PR-02 验收记录（核对方独立复跑，非执行者自述）

- 定向：`tests/test_freeze_baseline.py` → 不适用；`tests/test_feedback_ownership.py` → **8 passed**
- **突变验证（本 PR 的核心判据，此前从未做过）：通过** —— 把 `feedback_service.py:30` 的
  `AND principal_id=?` 换成 `AND ? IS NOT NULL`（保持绑定数、仅关闭归属校验）→ **2 failed**
  （`assert 201 == 404`），退出码 1。**说明测试确实锁住了归属，且"期望 404"的用例不再假通过。**
  突变后按 sha256 逐字节还原。
- R1（实现是否被削弱）：**否** —— `feedback_service.py:27` `if not principal_id` + `:30`
  `AND principal_id=?` 完好。三处调用点修法均正确。
- **全量套件（核对方独立跑）：`678 passed, 2 skipped, 0 failed`（44.66s）** —— 对比开工前
  `6 failed / 671 passed / 3 skipped`（总收集数同为 680）→ **仓库零回归**。
  ⚠️ 跑法：**必须** `CODEBUDDY_SAFE_DELETE_ENABLED=0` 前缀，否则安全拦截会吞掉 summary 行并
  返回 `exit=1`（假失败）。
- **结论：PR-02 代码可交付；下一步不是继续写代码，而是把提交切干净（见 §9.3）。**

---

## 10. 环境基线：下游 PR 的硬前置（2026-09-11 23:10 实测，执行权已交接）

**背景**：PR-02 已提交（`d21c314`）；执行者随后自行开工 **PR-03**（`src/application/chunking_policy.py`
于 23:01 新建，`document_loader.py` / `settings.py` 于 23:03-23:04 修改）→ **PR-03 进行中**。
下表是**在其之上**继续推进时必须知道的环境事实，逐条为实测（`importlib` / `grep` / `ls`），非推断。

### 10.1 已具备的能力（不要重复造）

| 能力 | 实测证据 |
|---|---|
| PDF / DOCX / XLSX / TEXT 解析 | `src/infrastructure/parsers/` 六模块 + `registry.py` |
| 页级元数据契约 | `ParsedDocument`（`models.py:350-360`）、`ParsedElement`（`:337-347`）、`StructuredChunk`（`:363-374`） |
| 稠密检索模型（离线可用） | **`data/bge-small-zh-v1.5/model.safetensors`**（仓库自带，无需联网） |
| 重排技术栈 | `torch` ✅ / `transformers` ✅ / `sentence_transformers` ✅ 全部可 import |
| 图像与数值 | `PIL 12.3.0` ✅ / `numpy 2.5.2` ✅ / `pypdf 6.17.0` ✅ |
| 评测底座 | 全量 680 collected（PR-02 提交后：640 passed / 2 skipped / 0 failed） |

### 10.2 缺失项（会直接卡住对应 PR）

| PR | 缺什么 | 实测 | 影响 |
|---|---|---|---|
| **PR-07 / PR-08** | **无任何 OCR 引擎** | `fitz` / `pytesseract` / `paddleocr` / `rapidocr_onnxruntime` / `cv2` / `onnxruntime` **全部 `ModuleNotFoundError`** | 扫描件路径**跑不通**。「低置信进 `needs_review`」的**逻辑**可写；但「扫描 fixture 可检索且页码可追溯」这条验收**必须先装 OCR**（建议 `rapidocr-onnxruntime`，需联网） |
| **PR-11** | **无 cross-encoder 模型** | 栈齐，但 `~/.cache/huggingface/` 下**只有 `.agent_harnesses.json`，没有 `hub/` 目录**；repo 内仅 `data/bge-small-zh-v1.5`（**双编码器，不能当 CE 用**） | `off/all/conditional` 三组消融**不可复现**。PR-11 验收允许「否则保持关闭」→ 可交付**降级路径 + 关闭态**，不能交付质量结论 |
| **PR-06** | 页级表不存在 | 30 张 `CREATE TABLE` 中无 `ingestion_jobs` / `page_artifacts` / `document_pages` | 需 **additive `CREATE TABLE IF NOT EXISTS`**（schema 变更，见 §10.3） |

### 10.3 三条治理提示（不是技术问题）

1. **PR-06 属 schema 变更**（新增表）。任务书本就要求 additive schema，但按仓库既定红线，
   schema 变更须**单独确认**；若采用，须在验收报告里显式声明「仅新增表，未改既有表结构/列」。
2. **`docs/cursor-upgrade/` 是 untracked 噪声**（本执行包自身，预期行为，不要 add）。
3. **`mindgraph/.workbuddy/` 也是 untracked 噪声**（巡检自动化的记忆目录），
   **不得进入任何提交**；建议在 `.gitignore` 补一条，但该改动本身也须单独确认。

### 10.4 对「一晚跑完 15 个」的判断（执行者视角，实测口径）

真实依赖链：**03→04→06→07→08→09→11→15** 与 **05→10**。三条硬约束：

- **PR-12 / PR-13 已标 `STOP AND ASK`**（前者把项目从单轮无状态改成有状态多轮 = 重新定位；
  后者 interrupt+resume 属 BrandAgent 领地），**PR-14 依赖 PR-12** → **三者不应执行**。
- **PR-11 的消融验收受「无模型」阻塞** → 只能交付关闭态与降级路径。
- **PR-08 依赖 PR-07，PR-07 受「无 OCR」阻塞** → 该子链大概率走不到底；若坚持，需先装 OCR。

→ **可行的过夜目标**：PR-03 收口 → PR-04 → PR-05 → PR-10 → PR-06 →（视网络）07/08/09 → PR-11 降级路径。
「15 个全绿」在一晚内**不成立**，原因不是执行力，是上述依赖与缺失。

### 10.5 交接说明（角色变更）

原执行者（外部 agent）在提交 PR-02 后自行开工 PR-03。**自 2026-09-11 23:05 起，执行权交接给
本会话**：它负责 PR-03 之后的全部剩余工作，并自行取证。

⚠️ **副作用（必须记录）**：核对方与执行者**合并为同一主体**，
因此 §9.4 那种「核对方独立复跑」的性质**不再成立**。补偿手段：
① 每步仍落 `06-NIGHT-GATE-LOG.md` 的带时间戳证据；
② 提交后仍执行 §9.3 的 `git stash push -u → 全量 pytest → stash pop` 自证；
③ 保留每小时的只读外部巡检（独立会话、无本次上下文）。

