# PR-09｜Parent–Child 语义切分双跑

阶段：M1  
依赖：PR-03, PR-08

## 🔴 开工前置条件（来自 PR-03 验收 2026-09-11T23:50，未完成则本 PR 必然踩坑）

PR-09 要**引入第二个 ChunkingPolicy 预设**，而 PR-03 遗留了一个在当前（仅 1 个预设）**不可达**、
但一旦有第二个预设就**必然触发**的静默故障：

- `ChunkingPolicy.from_settings()` 能正确选中新预设 ✅
- 但**实际切分不跟随**：`src/retrieval/indexing.py:27` `load_corpus` → `load_all_kb_chunks(doc_dirs)`
  不传参数，落回 `document_loader.DEFAULT_CHUNK_SIZE`（硬绑 `LEGACY_V1`）❌
- 结果：manifest 同时写 `chunk_size`(=500) 与 `chunking_policy.child_size`(=新预设值)，**自相矛盾**，
  双跑对比时两个索引的差异报告会**不可解释**。

实测（注入 `probe_v2` = child 800）：选中 probe_v2 ✅，切分仍 69 chunks / 最大 434 字 ❌。

**本 PR 动工前必须先修复**：让切分参数真正贯通到 `load_corpus` / `load_all_kb_chunks` 的生效路径，
并让 `index_metadata()` 的 `chunk_size` 与 `chunking_policy.child_size` 同源。
（未纳入 PR-03 是因为它要改默认参数求值时机与 m3 manifest 契约，超出 PR-03 范围。）

## ⚠️ 现场核对修正（2026-09-11 实测，前提部分不成立）

**修正 1：Parent–Child 已经实现了，本 PR 不是「实现切分」。**
`src/application/structured_chunker.py` 实测已有任务书「范围内」要求的能力：

| 任务书要求 | 现状 |
|---|---|
| 稳定 child/parent ID 与 lineage | ✅ `:26` `parent_id = sha256(f"{checksum}:parent:{idx}:{parent_text}")[:24]`；`:30` `child_id = sha256(f"{parent_id}:child:{idx}:{text}")[:24]` |
| 父块文本随子块携带 | ✅ `:25` 拼 `parent_text`；`:32` 写入 `StructuredChunk(..., parent_text=parent_text)` |
| 按标题/段落边界优先切分 | ✅ `:18` 按 `heading_path` 分组；`:28-29` 在父块内按 `child_size` 滑窗切子块 |

**修正 2：真实缺口是「零消费」——检索层完全没用 parent。**
实测 `grep parent_chunk_id\|parent_text src/retrieval/` 命中数 = **0**。

即：parent 产出了、写进了 `StructuredChunk`，但**召回链路从不消费它**，
`src/retrieval/pipeline.py` 也没有 context expansion 环节。

→ **本 PR 的真实工作量是「接消费端」，不是「实现切分」**，改法完全不同：
1. 先定位 `StructuredChunk` → 索引存储 → 检索结果这条链路上 `parent_text` **在哪一层被丢弃**；
2. 再实现「命中 child 后按预算补 parent / 邻接上下文」；
3. 分块参数（350–800 / 600–1200）属调参，不是主要矛盾。

**修正 3：`StructuredChunker` 目前只有上传一条入口，线上索引不走它。**
唯一调用点 `src/application/document_lifecycle_service.py:44`；
线上索引（`mg-`，581 chunks / 25 篇）由别处构建（见 PR-03 修正 1）。
→ 「覆盖 Markdown 路径」是本 PR 的真实前置条件，否则双跑比较的是两个不同构建器，结论无效。

**修正 4：参数不要在本 PR 直接改。** `:9` 现有默认是 `child_size=500, parent_size=1200, overlap=50`，
与任务书「制度合同 350–800、研报 600–1200」不同。参数变更必须经 PR-03 的 `ChunkingPolicy` 统一收口。

**修正 5：本 PR 建议重写后再发。** 上述四点使原任务书的「实施顺序」与「范围内」都需调整，
直接照原文执行会让 Cursor 重写一份已存在的实现。

## 目标

小块用于召回、父块用于补充上下文，并通过 shadow index 证明收益后再激活。

## 真实业务失败

固定 500 字符硬切会截断条件与结论；单纯增加 overlap 会放大重复和 Prompt 噪声。

## 必须先阅读

- `src/application/structured_chunker.py`
- `src/document_loader.py`
- `src/retrieval/types.py`
- `src/application/mindgraph_index_service.py`
- `src/retrieval/pipeline.py`

## 范围内

- 按标题/条款/段落/句子边界优先切分。
- 制度合同 350–800，研报 600–1200，超长时长度兜底。
- 稳定 child/parent ID 与 lineage。
- 检索命中 child 后按预算补 parent/邻接上下文。
- 新旧索引双跑。

## 范围外

- 不根据每个 query 动态重切知识库。
- 不直接覆盖 CURRENT。

## 实施顺序

1. 获取最新 main SHA、git status 和现有基线。
2. 复现业务失败或证明当前缺口。
3. 先补失败测试与契约测试。
4. 实现最小兼容改动。
5. 运行目标测试、相关回归与必要评测。
6. 输出新旧差异、已知风险和回滚步骤。

## 测试矩阵

- 边界、稳定 ID、重复率、parent 回溯、ACL 一致性、版本一致性。

## 验收标准

- 总体 Recall@5 不下降。
- 跨页/表格/条款事实覆盖提升。
- Prompt 重复率和长度受控。
- 人工确认后才切换。

## 回滚

CURRENT 回切 legacy index；新格式读取由 flag 控制。

## Cursor 可复制指令

```text
你只执行 PR-09「Parent–Child 语义切分双跑」，不得顺手做后续任务。

先读取 01-GLOBAL-GUARDRAILS.md、本任务书和“必须先阅读”中的仓库文件。先输出现场核对报告和最小改动计划，不要立即写代码。确认仓库现状与任务书一致后，先补失败测试，再做最小实现。完成后运行目标测试、相关回归和必要评测，报告真实输出、行为/指标差异、风险与回滚步骤。若发现任务前提不成立或需要破坏现有契约，停止实现并提交差异报告。
```
