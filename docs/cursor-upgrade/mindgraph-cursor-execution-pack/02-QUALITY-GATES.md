# 质量与发布门禁

## 硬门禁

- ACL leakage：0。
- 跨主体会话、反馈、bad case、artifact 读取/写入：0。
- 意外索引缩水：阻断激活。
- 冲突证据未解决时：禁止生成裁决性答案。
- OCR 失败、空文本、乱码和不可定位内容：禁止进入 active index。
- 新增 API/SSE 状态必须先更新契约测试。

## 候选质量目标

这些是候选发布目标，不允许通过修改评测口径硬凑：

| 指标 | 目标 |
|---|---:|
| Recall@5 | ≥0.90 |
| required fact coverage | ≥0.80 |
| citation precision | ≥0.95 |
| citation recall | ≥0.90 |
| conflict accuracy | ≥0.90 |
| ACL leakage | 0 |

若当前基线与目标差距过大，PR 只要求相对改进和无回归，并在报告中说明达到绝对目标仍需哪些数据或能力。

## 性能与成本

- 每个阶段记录 P50/P95：query analysis、embedding、dense、sparse、fusion、rerank、context expansion、generation。
- 条件式 Rerank 相对全量 Rerank：质量下降不超过 1pp，同时 P95 或计算成本下降至少 20%。
- 多轮上下文必须记录额外输入 token 和延迟。
- 大规模 ingestion 记录 pages/s、chunks/s、embedding cache hit、失败率、积压和恢复时间。

## 测试层级

1. 单元测试：纯规则、ID、状态机、边界。
2. 契约测试：API、SSE、MCP、schema、trace。
3. 集成测试：SQLite/FAISS/BM25/文件系统真实交互。
4. 安全测试：ACL、跨主体、路径、敏感日志。
5. 离线评测：冻结语料与 Golden。
6. 真实模型评测：单独标记、保留 provider/model/usage。
7. 浏览器 E2E：上传→索引→问答→引用→反馈。

## 每次 PR 至少执行

> ⚠️ **命令已在现场核对后修正**（见 `05-FIELD-VERIFICATION.md` 第 2 节）。照下列原样执行，不要照抄旧写法。

```bash
# 1. 目标测试（改动期间）
#    必须带 --no-cov：pytest.ini:15 有 --cov-fail-under=55，单文件跑必然判失败
python -m pytest <targeted-tests> -q --no-cov

# 2. 收尾全量测试（此时覆盖率才有意义）
python -m pytest -q

# 3. ruff：以 AGENTS.md:52 定义的 CI 门禁为准
python -m ruff check src scripts tests --select F821,F822,F823,E902

# 4. ruff：确认自己没引入新债（与 HEAD 版逐规则比对，净新增必须为 0）
python -m ruff check <changed-active-paths> --ignore RUF002,RUF003,E501 --output-format concise

# 5. mypy：只看改动文件；若报错落在未修改文件上，先 git show HEAD:<file> 确认是存量
python -m mypy <changed-active-paths> --follow-imports=silent
```

**三条已知陷阱**（实测复现，不是推测）：

| 现象 | 原因 | 处理 |
|---|---|---|
| `15 passed` 但退出码 1 | `--cov-fail-under=55` | 加 `--no-cov` |
| ruff 报 5934 errors | 仓库存量债务，与本 PR 无关 | 加 `--select F821,F822,F823,E902`（实测 All checks passed!） |
| mypy 报错在没改的文件里 | 存量类型债 | 不在本 PR 修，验收报告标注即可 |

涉及 Web 时追加：

```bash
corepack pnpm --dir web lint
corepack pnpm --dir web test
corepack pnpm --dir web build
```

涉及索引、检索、Prompt、Rerank 时必须追加对应离线评测和结果对比；具体命令以仓库最新脚本 `--help` 为准，不猜参数。
