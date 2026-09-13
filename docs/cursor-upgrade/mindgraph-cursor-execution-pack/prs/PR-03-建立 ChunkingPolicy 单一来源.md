# PR-03｜建立 ChunkingPolicy 单一来源

阶段：M0  
依赖：PR-01

## ⚠️ 现场核对修正（2026-09-11 实测，优先于下文）

**修正 1：切分参数有三个读取点，不是两处——第三个在线上索引构建路径上（内联字面量）。**

| 站点 | 位置 | 参数 | 用途 |
|---|---|---|---|
| `StructuredChunker` | `src/application/structured_chunker.py:9` | `child_size=500, parent_size=1200, overlap=50` | 上传文档路径（`document_lifecycle_service.py:44`） |
| 旧 Markdown 加载器 | **`src/document_loader.py:16-18`** | `DEFAULT_CHUNK_SIZE=500` / `DEFAULT_CHUNK_OVERLAP=50` | Markdown 扁平切分 |
| **索引服务内联调用** | **`src/application/mindgraph_index_service.py:129`** | `_chunk_text(sec_body, 500, 50)`（:28 从 `document_loader` 导入私有函数） | ⚠️ **线上索引（`mg-`，25 篇 / 581 chunks）的构建点** |

**漏掉第三处 = 线上索引不受 policy 管辖，本 PR「单一来源」的目标就没有达成。**
注意它是**内联字面量**，并不读取 `DEFAULT_CHUNK_SIZE`；把常量包成 policy 对象后，
这里必须同步改成从 policy 取值，否则会留下一个更隐蔽的漂移点。

**修正 2：`StructuredChunker` 现有参数是 500/1200/50，不只是 500/50。**
`ChunkingPolicy` 的字段设计要能容纳 parent/child 双尺寸 + overlap，否则 PR-09 还得再改一次。

**修正 3：路径勘误。** `document_loader.py` 在 **`src/` 根目录下**，不在 `src/application/`。

## 目标

所有 Markdown、上传文档、全量和增量路径从同一策略对象读取切分参数，但第一步保持旧输出不变。

## 真实业务失败

当前 `structured_chunker.py` 与 `document_loader.py` 各自维护 500/50 等参数，真实运行出现增量 98 与全量 69 chunks。

## 必须先阅读

- `src/application/structured_chunker.py`
- `src/document_loader.py`
- `src/application/mindgraph_index_service.py`
- `src/retrieval/indexing.py`
- `src/application/document_lifecycle_service.py`
- `tests/test_document_intelligence.py`

## 范围内

- 新增不可变 ChunkingPolicy 与配置加载。
- 为旧 Markdown 和结构化文档提供适配器。
- 索引 manifest 记录 policy name/version/parameters。
- 默认 `legacy_v1` 保持现有输出。

## 范围外

- 不实现新语义切分。
- 不改 Chunk ID 算法。
- 不激活新索引。

## 实施顺序

1. 获取最新 main SHA、git status 和现有基线。
2. 复现业务失败或证明当前缺口。
3. 先补失败测试与契约测试。
4. 实现最小兼容改动。
5. 运行目标测试、相关回归与必要评测。
6. 输出新旧差异、已知风险和回滚步骤。

## 测试矩阵

- 默认策略与修改前 fixture 字节级一致。
- 非法 overlap/size 失败。
- manifest 包含策略版本。

## 验收标准

- 生产代码不再散落切分常量。
- 旧语料输出不变。
- 可通过配置选择策略但默认仍 legacy。

## 回滚

回退适配器调用；旧函数和旧索引格式仍可读。

## Cursor 可复制指令

```text
你只执行 PR-03「建立 ChunkingPolicy 单一来源」，不得顺手做后续任务。

先读取 01-GLOBAL-GUARDRAILS.md、本任务书和“必须先阅读”中的仓库文件。先输出现场核对报告和最小改动计划，不要立即写代码。确认仓库现状与任务书一致后，先补失败测试，再做最小实现。完成后运行目标测试、相关回归和必要评测，报告真实输出、行为/指标差异、风险与回滚步骤。若发现任务前提不成立或需要破坏现有契约，停止实现并提交差异报告。
```

## ✅ 验收结果（2026-09-11T23:50，接管者实测）

**通过**，提交 `c8f69ae`。定向 14 passed；全量 654 passed / 2 skipped / 0 failed（覆盖 74.60%）；
三处切分站点（加载器 69 / 线上路径 590 / 结构化 3）在两棵树上的 `seq_sha256` **逐字节相同**；
突变验证：内联字面量回归 → 1 failed、快照失真 → 4 failed、常量脱钩 → 2 failed（测试非空转）；
`ruff --select F821,F822,F823,E902` 全过（排除中文注释噪声后净减 32 条）；mypy 0 错。

## ⚠️ 本 PR 遗留缺口（已知、当前不可达、移交 PR-09）

「单一来源」只完成了**选择端**，未接通**生效端**：

- `ChunkingPolicy.from_settings()` 能正确按 `CHUNKING_POLICY` 选中预设；
- 但 `retrieval/indexing.py:27` 的 `load_corpus` → `load_all_kb_chunks(doc_dirs)` **不传参数**，
  落回 `document_loader.DEFAULT_CHUNK_SIZE`（硬绑 `LEGACY_V1`）→ **实际切分不跟随 policy**；
- 结果：manifest 同时写 `chunk_size`(=500) 与 `chunking_policy.child_size`(=预设值)，**自相矛盾**。

实测证据（注入 `probe_v2` = child 800/overlap 100）：选中 probe_v2 ✅，但切分仍 69 chunks / 最大 434 字 ❌。

**不阻塞本 PR 的理由**：`_PRESETS` 仅有 `legacy_v1`，缺口不可达；任务书本就限定「默认保持现有输出」。
**必须在 PR-09 引入第二个预设之前修复**，否则 PR-09 的 parent-child 双跑会得到"配了但不生效 + manifest 撒谎"的静默故障。
