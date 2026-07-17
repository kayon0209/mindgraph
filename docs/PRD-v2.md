# 产品需求文档（PRD v2）｜企业报销知识问答系统 Expense RAG QA

> 文档版本：v2.0 ｜ 更新：2026-07-17 ｜ 状态：重构后最新版
> 替代：PRD-v1.md（2026-07-13，仅覆盖 v1 架构，已废弃）
> 知识来源：公司内部《差旅费报销管理办法》《费用报销管理制度》等制度文件

---

## 0. 修订说明（v1 → v2）

| 维度 | v1（已废弃） | v2（当前重构态） |
|------|------------|----------------|
| 向量库 | ChromaDB 本地 | FAISS 密集向量 + BM25 稀疏 + RRF 融合 + 可选 Cross-Encoder 重排 |
| 检索策略 | Top-K=3 单一 | 4 种策略：dense / sparse / hybrid / **hybrid_rerank** |
| 答案生成 | 智谱 GLM-4 单模型 | 多 Provider 解耦（DeepSeek / 智谱 / Anthropic），运行时热切换 + 主备降级 |
| 输出方式 | 同步整段返回 | **SSE 流式输出**（首字 < 1s） |
| 知识库管理 | 上传即覆盖 | 版本化索引：文档状态机 + Embedding 复用 + 原子切换 `CURRENT` 指针 + 回滚 |
| 检索过滤 | 无 | 新增**查询日期过滤**、**知识分类过滤**、**权限等级（authority_level）** |
| 安全护栏 | 基础拒答 | 关键词短路 + Prompt 注入防护 + PII 检测脱敏 + SQL/XSS 防护 + 合规日志 |
| 评测体系 | 无 | 34 题 Gold 数据集 + Recall@K / MRR / chunk_hit_rate + Ablation 对比 |
| 工程架构 | 单体脚本 | DDD 分层（Domain/Application/Infrastructure/API）+ CI/CD + Docker + Nginx |
| 版本号 | 1.0 | 3.1.0 |

> v1 文档中的"北极星指标"为产品目标，非实测结论。实测需来自版本化数据集 + 可复现基线，详见 §11。

---

## 1. 产品目标

让员工在提交报销前，用自然语言快速查询公司报销规则，拿到**可执行的答案 + 制度原文引用 + 可追溯检索证据**。对制度未覆盖或超范围问题，系统稳定拒答/降级，并给出可选路径（转人工、补充信息再问等）。

---

## 2. 重构后核心能力

### 2.1 混合检索与重排

- **四策略可切换**：
  - `dense`：FAISS 向量语义检索
  - `sparse`：BM25 关键词检索
  - `hybrid`：RRF（Reciprocal Rank Fusion）融合排序
  - `hybrid_rerank`：融合后接 Cross-Encoder 重排（精度最高，延迟略高，可降级）
- **Embedding**：`BAAI/bge-small-zh-v1.5`（可配置），支持本地缓存离线推理
- **检索增强过滤**：
  - `query_date`：按费用发生日期过滤时效性相关的制度条款
  - `knowledge_categories`：按知识分类（如"差旅/日常/特殊")筛选
  - `authority_level`：制度权威等级，影响引用优先级
  - `include_historical`：是否纳入历史版本条款

### 2.2 多 LLM Provider 与降级

- 通过 OpenAI-compatible 适配器统一接入：DeepSeek / 智谱 GLM / Anthropic Claude
- 运行时按 `chat_provider` + `chat_model` 动态选择，支持主备 Provider 自动降级
- Provider 未配置时，返回检索到的制度证据原文，不强行生成（优雅降级）

### 2.3 流式问答（SSE）

- 事件流：`request_started → scope_check_completed → retrieval_*` → `generation_started → answer_delta* → citations → usage → completed`
- 首字响应目标 < 1s，完整响应 < 8s
- 降级事件明确标注原因（retrieval_unavailable / provider_error / provider_not_configured）

### 2.4 版本化知识库

- 文档状态机：`uploaded → parsing → building → active`，非法状态跃迁被拒绝
- 索引版本化：`build-index` 生成新版本，校验通过后**原子切换** `CURRENT` 指针，失败构建不影响线上索引
- Embedding 复用：文档内容未变则复用既有向量，加速重建
- 回滚：支持指针回退到历史版本

### 2.5 安全护栏体系

| 层级 | 机制 | 说明 |
|------|------|------|
| 输入 | 关键词短路拒答 | 薪资/请假/辞职等与报销无关话题直接拒答（< 10ms） |
| 输入 | Prompt 注入防护 | 识别 `ignore previous` / `system prompt` 等越狱指令 |
| 输入 | SQL 注入 / XSS 检测 | 清洗特殊字符与危险语句 |
| 处理 | 特殊情况固定话术 | 跨年度/无发票/电子发票/合住等 PRD §10 话术短路返回 |
| 输出 | PII 检测与脱敏 | 身份证/手机号/邮箱自动隐藏 |
| 输出 | 引用强制 | 答案必须标注 `[citation-N]` 来源，降低幻觉 |

---

## 3. 用户画像与使用场景

| 用户类型 | 占比 | 核心诉求 |
|---------|------|---------|
| 普通员工（报销发起人） | ~70% | 报销前查规则/材料/标准，降低被退回概率 |
| 财务 / HR | ~30% | 核查政策、定位制度依据、统一对外口径 |

典型场景：报销前查询、提交后被退回定位原因、审核时核对原文、特殊情况确认（跨年度/无发票/电子发票）。

---

## 4. 功能范围（MoSCoW，重构后）

### Must Have
- 自然语言问答（单轮 + 流式）
- 答案来源引用（文档名 + 段落原文 + 检索分数）
- 多格式文档上传与管理（MD/TXT/PDF/DOCX/XLSX）
- 超范围/低相似度降级（不编造，给可选路径）
- 特殊情况固定话术
- 多检索策略 + 重排
- 版本化知识库（上传/构建/切换/回滚）
- 多 Provider 接入与降级

### Should Have
- 问题历史记录
- 有用/没用 反馈收集 Bad Case
- 查询日期 / 知识分类 / 权限等级过滤
- 可复现评测体系

### Could Have（v2+）
- 多轮追问引导（主动收集缺失信息）
- FAQ 预整理入库

### Won't Have（本期不做）
- 登录/权限体系（沿用 demo / api_key 模式）
- 生产级高可用部署（脚手架已备，策略见 §12）

---

## 5. 系统架构（重构后 RAG 数据流）

```
离线：文档输入 → 结构化分块(标题优先 + 500字/overlap50)
     → metadata(doc_name/section_path/chunk_index/authority_level/knowledge_category)
     → BGE Embedding → FAISS + BM25 双索引 → 版本化向量库

在线：用户问题
     → 范围检查(关键词短路 / 越狱检测)
     ├── 超范围 → 拒答 + 可选路径
     └── 范围内 ↓
     → 向量化 → 融合检索(RRF) → [重排] → Top-K
     ├── 无命中(<阈值) → 降级 + 可选路径
     └── 通过 ↓
     → 拼装 Prompt(系统指令 + 引用证据) → LLM 流式生成
     → 返回：结论 + 依据 + [citation-N] + 可选路径
```

---

## 6. 评测与质量闭环

### 6.1 评测指标
- `recall@1/3/5`：答案相关 chunk 是否进入 Top-K
- `MRR`：首个相关结果排名倒数均值
- `document_hit_rate`：文档级命中率
- `chunk_hit_rate`：chunk 级命中率（暴露"检索到文档但错 chunk"问题）

### 6.2 数据集
- 34 题 Gold 数据集，每题标注 Gold 文档 + Gold Chunk 标签
- 题型：直接规则题 / 边界特殊情况 / 知识库外 / 模糊口语化
- 注意：小样本来自开发过程，**不代表生产效果**，需独立 holdout

### 6.3 坏案例闭环
- 没用反馈 → 中文标签改进队列 → 根因分类 → 记录分析与解决 → 标记进度 → 导出候选 → 人工审批后进入官方数据集

### 6.4 历史基线（修复前，仅供趋势参考）
- hybrid 策略：recall@1/3/5 = 0.19/0.38/0.46，MRR=0.31，document_hit_rate=1.0
- 失败集中在：direct_rule 出差类、special_case 电子发票类（chunk_hit=0）
- 修复后需重新跑评测以获得可信基线

---

## 7. 安全与合规

- 数据脱敏：上传前人工检查，确认不含 PII
- 对抗测试：角色扮演攻击 / 规则绕过 / 信息套取 / 超范围诱导
- 隐私日志开关：`PRIVACY_LOG_QUESTIONS` 生产环境建议关闭
- 统一拒答话术：「抱歉，我只能回答公司报销相关问题。」

---

## 8. 技术栈

| 组件 | 技术 |
|------|------|
| 前端 | Streamlit（内部工具定位） |
| 检索 | FAISS + BM25 + RRF + 可选 Cross-Encoder |
| Embedding | BAAI/bge-small-zh-v1.5 |
| 生成 | DeepSeek / 智谱 GLM / Anthropic Claude（OpenAI-compatible 适配器） |
| API | FastAPI + SSE |
| 存储 | SQLite（WAL 模式）+ 版本化向量索引 |
| 部署 | Docker Compose + Nginx（限流/安全头/SSE） |
| 工程 | ruff + mypy + pytest（覆盖率门禁）+ Trivy + bandit + GitHub Actions |

---

## 9. 验收标准 Checklist（重构后）

- [ ] 四检索策略可切换且结果合理
- [ ] SSE 流式输出首字 < 1s
- [ ] 多 Provider 切换与降级正常
- [ ] 版本化索引：构建/切换/回滚不影响线上
- [ ] 查询日期/知识分类/权限等级过滤生效
- [ ] 超范围拒答 + 可选路径
- [ ] 特殊情况固定话术命中
- [ ] PII 检测脱敏生效
- [ ] 评测可复现跑通并产出指标报告
- [ ] LLM 超时优雅降级

---

## 10. 已知限制（KNOWN_LIMITATIONS）

- SQLite 并发写有上限，高并发需迁 Postgres
- Streamlit 前端定位内部工具，外部用户需换 Web 前端
- BGE 模型需预置（生产 `BGE_LOCAL_FILES_ONLY=true`，不运行时下载）
- 评测需模型可用，当前沙箱环境无法跑通完整评测

---

## 11. 下一步（迭代方向）

1. 在有模型的机器上跑完整评测，修复 7 个失败用例
2. Provider 鉴权固化（api_key 模式 + 密钥管理）
3. 可选 Postgres 迁移 / Web 前端替换
4. 多轮追问与 FAQ 入库（Could Have）

---

*本文档基于重构后代码（chat_service.py / special_cases.py / rag_engine.py / config / docs/ 系列）整理，反映 v3.1.0 实际能力。*
