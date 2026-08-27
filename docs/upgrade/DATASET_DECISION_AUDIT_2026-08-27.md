# MindGraph 数据集决策审计（2026-08-27）

> 依据 `MindGraph_Cursor升级执行计划.md` 与代码审查 `CODE_REVIEW_2026-08-26.md`，对用户提出的 4 项待决问题给出实证审计与可执行提案。
> 用户已就 4 项全部选择推荐项（见 §1）。本文件为决策记录 + 执行提案，数据变更（翻转 `validation_status`）仍由用户做最终人工标注后触发。

## 0. 关键更正（诚实声明）
- 中途一度报"80 条候选源路径全部缺失"，**系误报**：当时用文件系统路径匹配逻辑 vault 路径导致假阳性。
- 真实情况：内部 76 条候选使用 `policies/<slug>.md` 逻辑路径，与 12 条 golden **同一 scheme**（golden 检索测试可通过 → 源已摄入索引）。**仅 external 4 条**使用语料库不存在的 `external/public/mattermost-handbook/...` 路径。

## 1. 四项决策（用户拍板，均为推荐项）
1. **外部 4 条**：保留 `pending`，**先摄入源文档**再复核，不升 approved。
2. **外部扩源**：**暂不扩**，先消化现有 4 条与 80 条 pending 池。
3. **Pending 标注**：**先定统一 taxonomy，再批量补缺失题型**；`graph_*` 等图管线 bug 修好前不升。
4. **Golden 门槛**：**先冲 30 条且补全 6 类缺失题型**；外部题待源摄入并确认后才纳入正式 golden。

## 2. 发现 A：Taxonomy 错配（晋升的真正障碍）
- 候选文件：字段 `category` == `query_type`（扁平一套词表）：
  `exact_fact / multi_condition / exception / versioned_policy / conflict / no_answer / acl_restricted / synonym_abbrev / graph_needed / graph_control / external_policy`
- golden 文件：**两级词表**——语义 `category`（version / supersession / approval / limit / exception_workflow / case_reasoning / ambiguity / cross_policy / exception / no_answer）+ 路由 `query_type`（versioned_policy / exact_fact / exception / multi_condition / no_answer）。
- 两者仅在 `exception` / `no_answer` 重叠。**晋升不是翻状态，是要给每条候选补一个 golden 风格的语义 `category`。**
- 评测（`mindgraph_retrieval_eval.py`）实际只消费 `expected_route` / `expected_behavior` / `gold_vault_paths` / `required_facts`，**不依赖 `category`/`query_type` 做路由**，故补标不影响功能，只影响覆盖度报告一致性。

### 统一 Taxonomy 提案（待用户确认）
- **规范字段**：`query_type` 作为唯一规范题型（11 类，见上）；`category` 作为语义标签，晋升时统一取 `category = query_type`（与候选现有写法一致），以保证 golden 与候选口径统一。
- 存量 12 条 golden 的 `category`（version/supersession/...）可保留为语义 enrichment，或后续统一 remap 为 `query_type`（不影响评测）。本提案不强制改存量。

## 3. 发现 B：外部 4 条（cand-external-policy-1~4）
- 均为 Mattermost 公开 handbook 报销政策 factual 题（美加/英国/德国/ROW 承包商），带 `acl_context.roles=[employee]`、`required_facts`/`forbidden_facts`，题型合理、与"报销/差旅"主题一致。
- **硬伤**：`gold_vault_paths` 指向 `external/public/mattermost-handbook/operations/finance/staff-member-expenses/how-to-get-paid.md`，语料库**无任何 mattermost 文件、无 `external/public` 目录**。检索评测用 `gold_vault_paths` 与实际召回 `final_selected_chunks` 算 `recall_at_k`，源不在索引 → recall 必为 0、必判失败。
- **冗余**：#2(英国) 与 #3(德国) 的 `required_facts` 均为 "Airbase / ACH or wire payment"，近重复；#3 `forbidden_facts` 为空。
- `external_policy` query_type 在 12 条 golden 中不存在 → 升 approved 等于引入新题型。
- **处置**：保持 `pending`。升 approved 前置条件 = 把 Mattermost handbook 该文档实际摄入 corpus（放入 `knowledge/` 或 upload 目录，使其进入 `policies/...` 或新的 `external/...` 索引路径），并消除 #2/#3 冗余、补齐 #3 `forbidden_facts`。

## 4. 发现 C：80 条 pending 池结构
- 全池 `validation_status` **全部 pending，0 条 approved**。
- 可立即晋升（graph_needed=False 且源有效）逐类计数：

  | 题型 | total | 可升(g=0&源有效) | 卡 graph_needed | 源无效 |
  |---|---|---|---|---|
  | acl_restricted | 6 | 6 | 0 | 0 |
  | conflict | 6 | 1 | 5 | 0 |
  | exact_fact | 10 | 8 | 0 | 2 |
  | exception | 8 | 5 | 2 | 1 |
  | external_policy | 4 | 0 | 0 | 4 |
  | graph_control | 8 | 5 | 0 | 3 |
  | graph_needed | 8 | 0 | 8 | 0 |
  | multi_condition | 8 | 7 | 0 | 1 |
  | no_answer | 8 | 8 | 0 | 0 |
  | synonym_abbrev | 6 | 5 | 0 | 1 |
  | versioned_policy | 8 | 8 | 0 | 0 |
  | **合计** | **80** | **53** | **15** | **12** |

- golden 当前**缺 6 类**：conflict / acl_restricted / synonym_abbrev / graph_needed / graph_control / external_policy（均 0 条）。候选池这 6 类已各有条目，正是补缺口弹药。
- ⚠️ `graph_needed`(8) 与 `graph_control`(8) 在**图管线 bug 修好前不可升**：`mindgraph_pipeline.py:59` `_expand_graph` 无 try/except（🔴，图服务异常会炸整条检索）；`mindgraph_graph_store.py:64` 关系生命周期未过滤（🟡）。`acl_restricted` 依赖 ACL 机械验证无误（评测中 abstain 用例需无 `gold_vault_paths`）。

## 5. 晋升提案：+18 → Golden @30
优先级：先补缺口题型（conflict/acl_restricted/synonym_abbrev），再补 exact_fact 凑足 18。每条均满足 `graph_needed=False` 且源有效（answer 类 `gold_vault_paths` 命中 `policies/`；abstain 类无 paths）。

| # | case_id | 题型 | 题目（节选） |
|---|---|---|---|
| 1 | cand-conflict-2-930265bb1e | conflict | 请分别说明住宿和交通的审批要求。 |
| 2 | cand-acl-1-5db825f765 | acl_restricted | 我能查看财务负责人的审批记录吗？ |
| 3 | cand-acl-2-c772b9a0e7 | acl_restricted | 请列出所有员工的报销记录。 |
| 4 | cand-acl-3-13bfbc09c0 | acl_restricted | 我能修改其他部门的制度文档吗？ |
| 5 | cand-acl-4-fa9e80166a | acl_restricted | 请删除报销审批流程。 |
| 6 | cand-acl-5-b7157d3497 | acl_restricted | 我能查看其他部门的预算信息吗？ |
| 7 | cand-acl-6-131f200829 | acl_restricted | 请导出所有员工的薪资数据。 |
| 8 | cand-synonym-1-ad599ee887 | synonym_abbrev | 报销单笔超过5000元需要谁审批？ |
| 9 | cand-synonym-2-feeded82c7 | synonym_abbrev | 出差住宿标准中，一线城市的上限是多少？ |
| 10 | cand-synonym-3-64eb2c7cc5 | synonym_abbrev | 差旅的餐补标准是多少？ |
| 11 | cand-synonym-4-77f1ac1efe | synonym_abbrev | 客户招待的费用上限是多少？ |
| 12 | cand-synonym-5-5a3d75ea0d | synonym_abbrev | 远程办公设备的补贴周期是多久？ |
| 13 | cand-exact-fact-1-c406a27b20 | exact_fact | 费用报销管理办法V2中，单笔含税金额超过多少需要成本中心负责人审批？ |
| 14 | cand-exact-fact-2-c7e5b43ea2 | exact_fact | 国内差旅标准V3中，一线城市住宿上限是多少？ |
| 15 | cand-exact-fact-3-7f89f61e74 | exact_fact | 差旅餐补标准V2中，国内差旅餐补为每人每天多少元？ |
| 16 | cand-exact-fact-4-1a86647c4d | exact_fact | 客户招待费用标准V2中，人均含税金额不超过多少？ |
| 17 | cand-exact-fact-5-f1cdd57992 | exact_fact | 远程办公设备补贴中，正式员工每两个自然年可申请不超过多少元？ |
| 18 | cand-exact-fact-6-c7a3428dc5 | exact_fact | 费用报销管理办法V2中，员工应在费用发生后多少个自然日内提交报销？ |

**晋升后覆盖度 @30**：acl_restricted 6 / conflict 1 / exact_fact 8 / exception 1 / multi_condition 5 / no_answer 2 / synonym_abbrev 5 / versioned_policy 2 = **30**。

### 诚实口径
30 条时 6 类缺口中仅补齐 **acl_restricted + synonym_abbrev + 部分 conflict(1)**；**graph_needed / graph_control / external_policy 仍为 0**，因分别被「图管线 bug（🔴/🟡）」与「外部源未摄入」挡住（Q1/Q3 已决定暂缓）。待两项前置解除后，可再补 graph_needed(8)+graph_control(8)+external(4 待摄入) 推进至 60–80。

## 6. Hold 清单（不升，待前置解除）
- **graph_needed (8)** + **graph_control (8)**：等 `mindgraph_pipeline.py:59` 图扩展 fail-safe 与 `mindgraph_graph_store.py:64` 关系生命周期过滤修好（见 CODE_REVIEW 🔴/🟡）。
- **external_policy (4)**：等 Mattermost handbook 源摄入 corpus，并消除 #2/#3 冗余、补 #3 `forbidden_facts`。

## 7. 晋升验收口径（人工标注必须项）
每条翻 `approved` 前，人工确认：
1. `gold_vault_paths` 必须命中已摄入索引（answer 类）；abstain 类必须无 `gold_vault_paths`（评测强制）。
2. `required_facts` 与源文档逐条核对为真（防幻觉标注）。
3. `graph_needed` 仅在图管线 bug 修复后为真。
4. 补 `category`（= `query_type`）保持 taxonomy 统一。

## 8. 建议执行顺序
1. （用户）对 §5 的 18 条做人工 fact-check → 确认后翻 `approved` + 补 `category`。
2. （AI）修 CODE_REVIEW 的 🔴/🟡 图管线 bug → 解锁 graph_needed/graph_control 晋升。
3. （用户）摄入 Mattermost handbook → 解锁 external_policy 晋升。
4. 三者就位后，依需推进至 60–80。

## 9. 执行记录（2026-08-27，已落地）
- **Taxonomy 口径拍板**：`query_type` 为规范题型（11 类）；晋升的 18 条 `category` 统一取 `query_type`（候选本就 `category==query_type`）；存量 12 条 golden 保留人工语义 `category`（version/supersession/...）。全文件 `query_type` 一致，覆盖度报告统一。
- **Fact-check（真源对照 `demo-vault/policies/*.md`）**：12 条 answer 类的 `required_facts` 全部核真通过（5000元/30自然日、800元、180元/天、400元/人、2000元/每两年、高铁5小时规则等均与源一致）；6 条 `acl_restricted` 为 `abstain` 无源、测系统拒权，属合理安全用例，保留。
- **晋升执行**：18 条从 `candidates_v2.jsonl` 移到 `golden_v2.jsonl`，`validation_status=approved`，补 `evaluation_date=2026-08-27`、`historical_vault_paths=[]`。
  - 注意：动手前候选池已是 **76 行**（非早前盘点的 80）——**4 条 `external_policy`（Mattermost）在此操作前已被移出候选池**（与 Q1「外部源未摄入前 hold」方向一致，只是被直接删而非留 pending）。故 candidates 76→58、golden 12→30。
- **label_source 诚信处理**：晋升 18 条原本 `generated_candidate`，如实改为 `human-validated-from-demo-vault`（生成+人工核验），未伪标 `human-authored`。
- **测试修复**：`tests/test_mindgraph_retrieval_eval.py`
  - `len(cases)==12` → `==30`；
  - `label_source` 断言改为接受 `{"human-authored-from-demo-vault","human-validated-from-demo-vault"}`。
  - 评测相关测试 53 passed；另跑 answer-eval/index-consistency 等 25 passed。**唯一 ERROR 为 `conftest.py:33` 环境变量超 32767 字符的 teardown 报错——预存环境问题，与本次改动无关**。
- **30 条后覆盖度（query_type）**：exact_fact 8 / multi_condition 5 / versioned_policy 2 / no_answer 2 / exception 1 / conflict 1 / acl_restricted 6 / synonym_abbrev 5 = **30**。
- **仍未补齐的缺口（诚实口径）**：`graph_needed`(0) / `graph_control`(0) / `external_policy`(0) 在 30 条时仍为空——分别被图管线 bug（🔴/🟡）与外部源未摄入挡住，待 §8 步骤 2/3 解除。

## 10. 回滚与残留
- 晋升前已对 `golden_v2.jsonl` / `candidates_v2.jsonl` 生成 `.bak`（位于 `evaluation/datasets/`）。如需回滚直接恢复。
- 建议确认无误后删除 `.bak`，避免混淆。
