# MindGraph 后续升级计划（Roadmap / Upgrade Plan）

> **状态说明**：本文件记录**已规划但尚未实施**的能力增强。所有条目状态为 `Planned`，
> 在实施完成前，MindGraph 当前版本**不包含**这些能力。请勿在简历 / 对外文档中提前声称已具备。

## 规划条目一览

| ID | 升级项 | 优先级 | 预计版本 | 状态 | 说明 |
|----|--------|--------|----------|------|------|
| UG-001 | 鲁棒文档 ingestion（layout-aware） | **P2** | **v2.2** | Planned | PDF / 扫描件接入 OCR + 表格识别 + 结构恢复，条件触发，不影响 `.md` 主路径 |
| UG-002 | Query 理解层 | P2 | v2.2（候选） | Candidate | 查询改写 / 分解 / HyDE，提升长问、歧义问的检索命中（见下） |
| UG-003 | 多租户 / 权限过滤 | P3 | v3.0（候选） | Candidate | 行级隔离（workspace / tenant）；当前为单用户个人知识库，无此需求 |

---

## UG-001 详细说明：鲁棒文档 ingestion（layout-aware）

**背景**：当前 ingestion 链路为
`解析(pdf.py/pypdf | .md | .docx) → document_loader 按标题切节 + 500 字切块 → BGE 本地嵌入`。

- 主数据路径为 Obsidian `.md` 笔记，结构已知，**无需** OCR / 表格 / 结构恢复；
- 但 PDF 分支使用 `pypdf` 仅抽文本层，存在三类真实损失：
  1. 扫描件（无文本层）→ 抽出为空，需 **OCR**；
  2. 表格 → 被线性化、丢失行列关系，需 **表格识别**；
  3. 多栏 / 页眉页脚 / 阅读顺序 → 需 **结构恢复**。

**方案（规划）**：
- 在「解析」与「切块」之间插入**文档理解层**，仅对 PDF / 扫描件条件触发，`.md` 路径零成本；
- 选型贴合本地离线基调：**Docling / MinerU / Marker**（离线，PDF + 表格 + 结构一体）；
- 表格输出为 markdown table 或「逐行语义单元」，保证切块器与嵌入器友好；
- 不改动现有 `document_loader` 与嵌入 / 检索内核。

**验收（规划）**：扫描件 PDF 与含表 PDF 经 ingestion 后，其表格 / 结构可被检索命中；`.md` 主路径的延迟与召回不变。

**优先级依据（P2）**：主路径为 Markdown，PDF 为增量场景；若 PDF 成为主要数据源则升 **P1**。

---

## 其他候选（待评估，未排入确定版本）

- **UG-002 Query 理解**：当前 `chat_service._retrieve` 直接把原始问题送入 `embedding + BM25`，
  无改写 / 分解 / HyDE。长问、歧义问、多跳问的检索召回有提升空间。
- **UG-003 权限过滤**：当前检索无租户 / 行级隔离（单用户个人库）；
  仅有 scope guardrail + `document_status` / `knowledge_category` / 有效期 过滤。
  若走向多用户 SaaS，需补 workspace / tenant 级隔离（参考 hrbp-ai-workbench 的 RLS 方案）。
