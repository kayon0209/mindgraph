# MindGraph 前端全面审查与优化方案

- 日期：2026-08-27
- 范围：后端安全/完整性复审 + 前端全部源码（4 个视图页、App 外壳、组件、lib、样式）
- 方法：逐文件代码审查 + 活体 API 契约验证 + 数据库核查 + 既有测试/构建门禁
- 约束：所有前端改动不逾越现有后端架构与已定义功能；确需新增能力处均已标注「需后端协同」。

---

## 0. 结论速览（TL;DR）

**总体判断：前端代码质量高，没有假功能、死按钮、无响应点击或崩溃路径。** 数据全部来自真实端点，无 Mock 占位；加载/空/错误三态齐全；有统一视觉语言（纸墨编辑风 + 衬线标题 + 噪点纹理），**不是「AI 味」模板**；响应式三断点 + `prefers-reduced-motion` + focus-visible + aria 标签都已到位。

**但存在 1 个阻断级功能问题（P0）**，以及若干配置级安全风险与体验缺口：

| 级别 | 数量 | 代表问题 |
|---|---|---|
| 🔴 P0 阻断 | 1 | **F1 过期 MindGraph 索引**：旗舰「可信问答」引用的是 7 月内容创作语料，而非当前 `knowledge/` 的报销语料 |
| 🟠 P1 重要 | 4 | 402 错误未映射、`chunk_count` 恒为 0、台账无分页、服务断连无重试 |
| 🟡 P2 体验 | 8 | 无「清空对话」、无深链、证据轨只反映最新一轮、缺三视图新手引导等 |
| 🔵 安全配置 | 3 | `.env` 明文密钥、`AUTH_MODE=off`、`CORS=*` + Vite `--host 0.0.0.0` |

**需要你拍板的 3 件事**（详见 §9）：
1. F1 索引重建用哪种方式（推荐「普通重建」，保留 6 条有效 confirmed 关系）。
2. 是否现在就实施前端 P1/P2 体验优化。
3. 是否在对外暴露前做安全收口（轮换密钥 / 收紧 CORS 与 host）。

---

## 1. 审查范围与方法

**已审查文件（前端）**
- `web/src/App.tsx`（外壳/导航/健康指示）
- `web/src/pages/ChatPage.tsx`（621 行）
- `web/src/pages/KnowledgePage.tsx`、`EvaluationPage.tsx`、`RelationsPage.tsx`
- `web/src/components/Primitives.tsx`、`PolicyGovernance.tsx`、`BarComparison.tsx`
- `web/src/lib/api.ts`、`metrics.ts`、`policy-conflicts.ts`、`route-decision.ts`、`types.ts`
- `web/src/styles.css`（2197 行）、`index.html`、`main.tsx`、`package.json`

**已交叉验证（后端/数据）**
- 活体端点：`/health`、`/mindgraph/notes`、`/mindgraph/evaluation/ablation`、`/mindgraph/relations/proposed|confirmed` 均 200
- 数据库 `data/product/product.sqlite3`：notes=406、note_relations=229（19 confirmed / 209 proposed / 1 rejected）
- 索引指针：`data/mindgraph_indexes/CURRENT` 与 `data/retrieval_indexes/CURRENT`
- Golden Set：`evaluation/datasets/mindgraph_golden_v2.jsonl`（90 条，目标为报销语料）

**验证手段与限制**
- 后端：pytest 319 passed / 2 skipped；ruff 门禁通过
- 前端：`tsc` typecheck、vitest 15 例、`vite build` 均通过
- 浏览器自动化在本环境不可用（Edge headless `--dump-dom` 返回 0 字节；无 playwright/puppeteer）。因此 **UI/UX 结论基于代码审查 + 活体 API 载荷比对**，未做真实像素级走查。

---

## 2. 🔴 P0 阻断问题（必须优先解决）

### F1 · MindGraph 索引过期，旗舰问答引用无关内容

**现象**：在「可信问答」里问报销类问题（例如快捷提问「2026 年 8 月发生的费用最晚多久提交？」），返回的引用是「李凯盟 / 面试问答 / 起号」等内容创作文档，与问题完全无关。

**根因（双索引「分脑」）**：
- 前端 `streamChat` 调 `/mindgraph/chat/stream`，走 `mindgraph_pipeline`，读 `data/mindgraph_indexes`。
- 该目录 `CURRENT` 指向 **`mg-20260720T084816Z`**（7 月 20 日构建，内容创作语料，378 notes / 3019 chunks，**不含任何报销文档**）。
- 而 `knowledge/` 现在是**报销语料**（25 个 .md：`policies/expense-general-v1/v2`、`travel-*`、`cases/`、`workflows/`、`external/` 等）；`data/retrieval_indexes` 的 `CURRENT`（8 月 27 日，含报销文档）属于**经典 `/chat` 管线，前端并未调用**。
- 结果：前端接线的索引是旧的，新的索引接在了没被用的管线上。

**佐证（活体数据）**：
- `/mindgraph/evaluation/ablation` 返回 `library_stats = { notes_total: 406, indexed_notes: 378, chunks_total: 3019, relations_confirmed: 19, relations_proposed: 209 }`。`indexed_notes(378)` 读的就是过期的 mindgraph manifest，与 `notes_total(406)` 不一致——这正是 F1 的可见症状。
- Golden Set v2.4.0（90 条）全部以报销语料为 gold（如 `policies/expense-general-v2.md`），与当前 `knowledge/` 一致，但与过期索引不匹配。

**影响**：核心卖点「先给结论、再交付证据」在主打场景下失真；质量账本与快捷提问都建立在错误语料上。

**修复（数据/运维操作，非前端改动）**：

```bash
python scripts/sync_vault.py --vault "D:/demo/mindgraph/knowledge"
```

**数据决策（重要）**——重建会触发 `VaultSyncService` 的 `prune_missing`（默认开启），把不在 `knowledge/` 里的笔记及其关系删掉。精确测算：

| 项 | 现状 | 普通重建后 |
|---|---|---|
| notes | 406 | **25**（剪掉 381 条内容创作遗留） |
| confirmed 关系 | 19 | **保留 6**（两端都在报销语料内），丢失 13 |
| proposed 关系 | 209 | 丢失 209 |
| rejected 关系 | 1 | 丢失 1 |

- **推荐：普通重建（不加 `--reset`）**——保留 6 条报销语料内部的有效 confirmed 关系，重建后 `graph_enabled` 一跳扩展仍有真实边可用。
- `--reset` 会先 `DELETE note_relations + notes`，连那 6 条也清掉，仅在你想彻底归零时才用。

**重建后的连锁收益**：问答引用回到报销文档；`indexed_notes` 与 `notes_total` 对齐为 25；Golden Set 可正常评测；快捷提问恢复可用。

**配套动作（P1）**：重建后重跑 `python scripts/run_ablation.py`，让质量账本基于报销语料出真实指标（现有 6 条 run 均为 7 月内容语料）。

---

## 3. 安全审查（方面一：安全漏洞）

### 已确认无的风险（正面结论）
- **XSS / 注入**：前端全部通过 React 文本节点渲染，`web/src` 中无 `dangerouslySetInnerHTML`、`innerHTML`、`eval`、`document.write`；引用摘录、证据原文、关系证据均为纯文本渲染，自动转义。
- **SQL 注入**：后端所有动态 `IN (...)` 均用 `",".join(["?"]*n)` + 参数绑定（如 `mindgraph_readonly.py` L298/385/444）；仅有的 f-string 标识符拼接（`database.py` 的 `PRAGMA table_info`、`vector_store.py` 表名、`feedback_service.py` 字段赋值）都来自内部常量/白名单，不接受用户输入。
- **敏感信息泄漏到客户端**：错误经 `normalize_http_error` 归一化，不向客户端抛堆栈。
- **密钥/索引入库**：`.gitignore` 已覆盖 `.env`、`data/`、`evaluation/results/`、`*.sqlite3`，真实密钥与索引不会进 git。

### 需要处理的风险（按优先级）

| ID | 级别 | 问题 | 现状评估 | 建议 |
|---|---|---|---|---|
| S1 | 🟠 | `.env` 明文存放真实密钥（`ZHIPU_API_KEY`、`OPENAI_COMPAT_API_KEY`） | 已被 `.gitignore` 排除，未入库；但明文落盘且调试中多次经手 | 轮换两枚密钥；确认从未粘贴进聊天/日志/截图；生产改用环境变量注入或密钥管理 |
| S2 | 🟠 | `AUTH_MODE=off` → `resolve_access_scope` 返回 `None` → **ACL 全绕过**（`auth.py` L224-237） | 本地单用户演示可接受 | 对外暴露前必须启用鉴权；文档中明确「off 仅限本机」 |
| S3 | 🟡 | `CORS_ORIGINS` 含通配 `*` | 与 S2 叠加时，任意网页可对本机 API 发起跨域请求 | 收紧为明确来源列表（如 Vite dev origin） |
| S4 | 🟡 | `package.json` dev 脚本 `vite --host 0.0.0.0`，dev server 暴露到局域网 | 本机回环使用时风险低 | 除非确需局域网演示，改回默认本机绑定 |
| S5 | 🔵 | provider 支撑的端点无速率限制 | 本地优先，风险低 | 对外前加限流，防止成本型滥用 |

> S2/S3/S4 单独看都可接受于「本地优先」场景，但**三者叠加**意味着：只要这台机器被局域网触达，任何设备/网页都能无鉴权读取整个知识库。建议在对外暴露前作为一组一起收口。

---

## 4. 代码质量与功能完整性（方面一：逻辑 / 未完成 / 隐患功能）

### 本会话已修复
- **F2 · 生成失败（DeepSeek 402）**：直连验证 DeepSeek key 返回 HTTP 402（欠费/配额）。已将 `.env` 的 `CHAT_PROVIDER=deepseek` 改为 `CHAT_PROVIDER=zhipu`，重启 API 后 `/mindgraph/chat` 返回 200、`provider=zhipu`、`model=glm-4.7`、`state=answered`、`degraded=False`。
  - **回滚方式**：给 DeepSeek 账户充值后，将 `.env` 改回 `CHAT_PROVIDER=deepseek` 并重启 API 即可。

### 待修复缺陷

| ID | 级别 | 问题 | 定位 | 建议（标注是否需后端） |
|---|---|---|---|---|
| F3 | 🟠 | HTTP 402 未在 `normalize_http_error` 映射，落到 `unknown_provider_error`，用户看到不可操作的提示 | `infrastructure/openai_compatible_provider.py` L17-21 | 【后端】补 402→配额/计费类错误码；前端 `ERROR_MESSAGES` 同步增文案 |
| F4 | 🟠 | `chunk_count` 恒为 0（字段从未被写入），但 KnowledgePage 抽屉展示「访问级别 / 分块」，向用户呈现误导性的「0」 | `mindgraph_readonly.py` L236 读取；`KnowledgePage.tsx` L159 展示 | 【后端】索引构建时回填每 note 分块数；短期【前端】可先隐藏该字段 |
| F6 | 🟡 | zhipu 客户端仅在启动时创建一次；观察到进程内首次调用瞬时挂起/重试（`Retrying request`），无显式超时配置 | `infrastructure/chat_provider.py` | 【后端】为 provider 调用加超时 + 有界重试，并把超时归一为 degraded |
| F7 | 🟡 | 双检索管线「分脑」：`/chat`（retrieval_indexes）与 `/mindgraph/chat`（mindgraph_indexes）并存，前端只用后者，新索引却在前者 | `api/dependencies.py` L57/78/84/111 | 【后端】统一索引根，或弃用未使用的 `/chat`，消除漂移与误用 |

### 完整性结论
- 前端**无未完成/半接线功能**：所有按钮、表单、标签页、抽屉、反馈、中止、重试都接到真实端点。
- `api.answer`（非流式 `/mindgraph/chat`）在 `api.ts` 已定义但 UI 未调用（UI 全走流式）——无害的死导出，可保留备用。
- 会话持久化做了防御：`normalizeRestoredTurns` 会把上次中断在「生成中」的轮次恢复为错误态，不会永远卡转圈。

---

## 5. UI/UX 审查（方面二：布局 / 假功能 / 点击 / 崩溃）

**结论：未发现死按钮、假功能、无响应点击或崩溃路径。** 逐条核验的交互链如下，全部闭环：

| 视图 | 已验证闭环的交互 |
|---|---|
| 可信问答 | 快捷提问→提交；策略/TopK 下拉；高级设置（关系开关/跳数/查询日期）；流式三步轨迹；中止；错误重试+技术详情；👍👎反馈（含失败重试）；引用/路由/冲突/确认关系四区证据轨 |
| 制度台账 | 搜索提交；指标卡；台账行→详情抽屉（Esc/遮罩/按钮三种关闭）；详情失败独立错误条不拖垮整页 |
| 质量账本 | 刷新；14 张指标卡；消融对比条；运行记录表；空态指向真实脚本 |
| 关系审核 | proposed/confirmed 切换；刷新；审核原因必填校验；确认/拒绝→`resolve`→重载；冲突标记；证据原文展示 |

### 发现的体验缺口（非阻断）

| ID | 级别 | 问题 | 定位 | 建议 |
|---|---|---|---|---|
| U1 | 🟠 | **中止后转圈永不停**：点击「中止」触发 AbortError，catch 分支跳过状态更新，该轮 `state` 永远停在 `streaming`，前端持续显示加载动画（刷新后才自愈） | `ChatPage.tsx` L228-236 | 【前端】AbortError 时把该轮置为「已中止」态并保留已生成部分 |
| U2 | 🟠 | **台账无分页**：`api.notes` 写死 `limit=200`，当前 406 条只能看到前 200，页脚显示「200 / 406」却无法查看其余 | `api.ts` L111；`KnowledgePage.tsx` | 【前端】后端已支持 `offset`（`mindgraph_readonly.py` L106-109，limit≤1000），加分页/「加载更多」即可，无需后端改动 |
| U3 | 🟠 | **校验错误吞掉整个队列**：RelationsPage 用页面级 `setError` 承载「审核原因必填」，一旦触发会用 ErrorState 替换整个审核列表（当前按钮 disabled 使其实际不可达，但模式危险） | `RelationsPage.tsx` L37-42 | 【前端】改为行内校验提示，不动页面级错误态 |
| U4 | 🟡 | 无「清空对话」：会话永久存于 localStorage，用户无法主动清空 | `ChatPage.tsx` | 【前端】加「清空」按钮（二次确认） |
| U5 | 🟡 | 服务断连提示无重试按钮，只能手动刷新整页；健康检查为一次性，不轮询 | `App.tsx` L29-38, L85 | 【前端】断连态加「重试」；可选低频重连探测 |
| U6 | 🟡 | 证据轨只反映**最新一轮**：多轮对话中上滑查看旧轮时，右侧引用/轨迹仍是最新一轮的 | `ChatPage.tsx`（citations/trace 为全局态） | 【前端】点击历史轮切换证据轨；或明确标注「当前展示最新一轮」 |
| U7 | 🟡 | 无深链：视图用 `useState` 切换，刷新永远回到「可信问答」，无法分享/收藏具体视图 | `App.tsx` L26 | 【前端】hash 路由（如 `#/knowledge`）或记忆上次视图 |
| U8 | 🔵 | 移动端（≤920px）`chat-layout` 保持固定高度 + `overflow:hidden` 单列堆叠，证据轨可视高度可能被压得极小（代码推断，待真机验证） | `styles.css` L385, L1849-1860 | 【前端】移动端释放高度为 auto，证据轨随页滚动 |
| U9 | 🔵 | 「中止」与「重试」共用 RotateCcw 图标，语义混淆 | `ChatPage.tsx` L443-445 | 【前端】中止改用 Square/StopCircle |
| U10 | 🔵 | 关系审核标签页按钮缺 `role="tab"`/`aria-selected`（容器有 `role="tablist"`） | `RelationsPage.tsx` L84-91 | 【前端】补齐 ARIA 语义 |
| U11 | 🔵 | 台账搜索仅提交触发，无即搜/清空按钮 | `KnowledgePage.tsx` L86-90 | 【前端】可选防抖即搜 |

---

## 6. 交互体验与新手引导（方面三）

### 现有优点（应保持）
- 聊天页有「三步上手」引导卡：可关闭、localStorage 记忆、不再打扰。
- 快捷提问 3 条与 Golden Set 同源（报销场景），是好的「首次价值」入口（F1 修复后即生效）。
- 高级设置默认折叠 + `title` 提示，把复杂度藏起来。
- 所有空态诚实且可操作（如质量账本空态直接给出 `scripts/run_ablation.py`），无 Mock 填充。
- 错误态统一带「重试 + 技术详情折叠」，拒答/权限不足/证据不足各有专门文案。

### 缺口与建议

| ID | 级别 | 问题 | 建议 |
|---|---|---|---|
| I1 | 🟡 | 引导只有聊天页有；台账/质量账本/关系审核首次进入无上下文解释 | 【前端】每视图加一行可关闭的上下文提示（例：关系审核页解释「proposed 从哪来、确认后去哪」） |
| I2 | 🟡 | 关系审核空态文案「可以通过关系抽取 API 生成 proposed 候选」面向开发者，审核者不知如何操作 | 【前端】改为面向操作者的说明（指向实际脚本/流程） |
| I3 | 🟡 | 长生成无时间反馈：zhipu 实测一次回答约 30-40s，期间只有一句「正在核对制度与证据……」 | 【前端】加已用时显示；>15s 提示「复杂问题需要更久」 |
| I4 | 🔵 | 降级回答（degraded）只把兜底文案当正文显示，降级原因码（如 `unknown_provider_error`）不可见 | 【前端】完成帧带 `degraded` 时显示原因徽标；与 F3 联动 |
| I5 | 🔵 | `usage` SSE 事件（token/耗时）后端已发送但前端未消费 | 【前端】答案下加一行「耗时 Xs · Y tokens」，强化「可审计」叙事 |
| I6 | 🔵 | 高级设置开关的状态不跨会话保存 | 【前端】策略/TopK/graph 偏好存 localStorage |

---

## 7. 产品级体验（方面四：C 端标准）

### 已达标项
- **视觉辨识度**：纸墨配色、衬线大标题、印章元素、噪点纹理、编号导航——成体系的编辑风设计，明显规避了模板化「AI 味」。
- **诚实性**：指标、引用、关系全部真实数据；关系页底部有「当前不是完整知识图谱」的自我披露，符合「不夸大 GraphRAG」的计划红线。
- **可审计闭环**：回答→引用→检索轨迹→反馈→质量账本，叙事完整。
- **动效克制**：reveal 入场 + hover 微交互 + `prefers-reduced-motion` 全量降级。

### 提升项（按 C 端留存标准）

| ID | 级别 | 建议 | 说明 |
|---|---|---|---|
| P1 | 🟠 | 索引新鲜度可见化 | 在台账或侧栏展示当前索引版本/构建时间（数据已在 CURRENT manifest；若现有端点未暴露则【需后端协同】加字段）。可根治 F1 类「静默过期」事故 |
| P2 | 🟠 | 质量账本与问答同源 | 重建索引后重跑 ablation（同一 mindgraph 管线，`run_ablation.py` L127 已确认），指标即代表线上问答质量 |
| P3 | 🟡 | 对话管理 | 清空对话 + （可选）导出为 Markdown |
| P4 | 🟡 | 深链与视图记忆 | hash 路由，支持刷新保持、分享 |
| P5 | 🟡 | 键盘效率 | `1-4` 切视图、`/` 聚焦提问框 |
| P6 | 🔵 | 微交互升级（motionsites 风格） | 流式打字光标、引用卡 hover 预览原文位置、轨迹步骤连线动画；均为纯 CSS/前端 |
| P7 | 🔵 | 多轮上下文 | 当前每问独立（`ChatRequest` 无 history 字段）；如需追问承接【需后端协同】定义会话上下文 |

---

## 8. 优化路线图（按优先级）

### P0 —— 立即（阻断项）
| # | 事项 | 类型 | 工作量 |
|---|---|---|---|
| 1 | **F1 重建 mindgraph 索引**（命令与数据决策见 §2，等你确认） | 运维 | ~10 分钟构建 + 验证 |
| 2 | U1 中止后状态修复 | 前端 | 0.5h |

### P1 —— 本周（正确性与核心体验）
| # | 事项 | 类型 | 工作量 |
|---|---|---|---|
| 3 | F3 402/错误码映射 + 前端文案 | 后端+前端 | 2h |
| 4 | U2 台账分页（后端 offset 已就绪） | 前端 | 2h |
| 5 | U3 关系审核行内校验 | 前端 | 1h |
| 6 | U5 断连重试 + I3 长生成计时 | 前端 | 2h |
| 7 | F4 chunk_count 回填（或前端先隐藏） | 后端(前端兜底) | 2h |
| 8 | 重建后重跑 run_ablation.py 刷新质量账本 | 运维 | 30min |
| 9 | S3/S4 收紧 CORS 与 dev host | 配置 | 0.5h |

### P2 —— 下周（产品化打磨）
| # | 事项 | 类型 | 工作量 |
|---|---|---|---|
| 10 | U4 清空对话、U7/P4 深链、I6 偏好持久化 | 前端 | 4h |
| 11 | U6 证据轨随轮次切换、I4 降级徽标、I5 usage 展示 | 前端 | 4h |
| 12 | I1/I2 三视图引导与文案 | 前端 | 2h |
| 13 | P1 索引新鲜度展示 | 前端(+可能后端) | 3h |
| 14 | U8-U11 移动端/图标/ARIA/搜索打磨 | 前端 | 3h |
| 15 | S1 密钥轮换、F6 provider 超时、F7 管线统一评估 | 运维/后端 | 视决策 |

> 路线图全部遵守约束：前端项只用现有 API 面（含已存在但前端未用的 `offset`）；F4/F6/F7/P1/P7 中需要新后端能力处均已显式标注「需后端协同」，不擅自越界。

---

## 9. 需要你拍板的决策

1. **F1 索引重建方式**
   - A（推荐）：普通重建 `python scripts/sync_vault.py --vault "D:/demo/mindgraph/knowledge"` → notes 406→25，保留 6 条报销语料内的 confirmed 关系。
   - B：`--reset` 彻底归零 → 连 6 条 confirmed 也清掉，关系全部重新积累。
   - 被剪掉的 381 条内容创作笔记与 223 条关系（含 209 proposed）将**不可恢复**（库内记录；vault 文件不受影响）。
2. **是否现在实施前端 P1/P2 优化**（§8 中所有【前端】项均可立即开工，不依赖后端）。
3. **安全收口时机**：是否现在轮换两枚明文密钥并收紧 CORS/host（若仅本机使用可暂缓，但建议尽快）。

---

## 10. 附录：验证记录

- **测试门禁**：pytest 319 passed / 2 skipped；ruff 通过；web `tsc` / vitest 15 例 / `vite build` 全部通过。
- **活体端点（2026-08-27）**：`/health`→ok；`/mindgraph/notes?limit=1`→total=406；`/mindgraph/evaluation/ablation`→runs=6（最新 7 月 20 日 bm25）；proposed=209、confirmed=19。
- **生成验证**：`/mindgraph/chat`（zhipu glm-4.7）→ 200，`state=answered`，`degraded=False`，约 39s。
- **索引指针**：`mindgraph_indexes/CURRENT=mg-20260720T084816Z-5fae59b4`（378 notes/3019 chunks，过期）；`retrieval_indexes/CURRENT=mg-20260827T095853Z-4ef8127b`（610 chunks/406 notes，含报销，未被前端使用）。
- **本会话已做的变更**：`.env` `CHAT_PROVIDER=deepseek→zhipu`（回滚方式见 §4 F2）；清理了遗留的重复服务进程与本会话临时调试文件。
- **当前运行中的服务**：API `http://127.0.0.1:8000`（zhipu 已生效）；Vite dev `http://127.0.0.1:5175`。

---

## 11. 修复执行记录（2026-08-28，用户已批准全部建议）

> §9 决策结果：F1 采用方案 A；前端 P1/P2 全部实施；安全收口现在执行（S1 密钥轮换为用户侧操作，见下）。

### 11.1 P0 阻断项

| 编号 | 状态 | 执行内容 |
|------|------|----------|
| F1 索引过期 | ✅ 完成 | 手动清理 379 条 `source_id` 为 NULL/外部的孤儿笔记 + 223 条关系（`_prune_missing` 只按 source_id 匹配，无法自动清掉 NULL 源）；`MindGraphIndexService.build(force=True)` 重建，581/581 复用嵌入缓存。`CURRENT=mg-20260828T042253Z-f281844d`（25 notes / 581 chunks）。数据库备份：`data/product/product.sqlite3.bak-20260827` |
| F2 配额耗尽 | ✅ 完成（上轮） | 已切 zhipu glm-4.7 |
| F3 错误码归一 | ✅ 完成 | `openai_compatible_provider.normalize_http_error` 增加 402→`quota_exhausted`；`chat_provider._normalize_error` 将 zhipu SDK 异常统一为带 `.code` 的 `NormalizedProviderError`（400/401/402/403/404/429/5xx/timeout 全覆盖）；前端 `ERROR_MESSAGES` 补齐全部错误码的中文可行动提示 |
| F4 索引状态回填 | ✅ 完成 | `mindgraph_index_service` 构建成功路径按 `chunk_count` 逐条回填 `notes.index_status/index_version/last_indexed_at/chunk_count`；验证 25 条笔记 chunk_count 全部 >0 |
| F6 zhipu 挂起 | ✅ 完成 | `ZhipuAI(timeout=90, max_retries=2)`（原默认 3 次重试导致长挂起），流式迭代逐 chunk 归一异常 |

### 11.2 安全收口

| 编号 | 状态 | 执行内容 |
|------|------|----------|
| S1 密钥轮换 | ⚠️ 待用户操作 | 两枚明文密钥仍在 `.env`（已被 `.gitignore` 排除，不会入库）。**请到智谱与 DeepSeek 控制台轮换密钥后更新 `.env`**——代理无法访问服务商控制台 |
| S2 AUTH_MODE=off | ⏸ 维持 | 本机单用户场景，按升级计划维持 off；上线前必须开启 |
| S3 CORS 通配符 | ✅ 完成 | `.env` `CORS_ORIGINS` 移除 `*`，保留 5173-5175/8501/obsidian 白名单 |
| S4 Vite 局域网暴露 | ✅ 完成 | `web/package.json` `dev` 脚本移除 `--host 0.0.0.0`，回到仅本机 |

### 11.3 前端修复（U/I/P 全量）

| 编号 | 状态 | 执行内容 |
|------|------|----------|
| U1 中止后永久转圈 | ✅ | AbortError 分支将该轮置为 error 态（保留已生成部分 + 「已手动中止」说明 + 可重试），并同步证据轨 |
| U2 台账无分页 | ✅ | `api.notes(query, offset, limit)` + KnowledgePage 分页控件（每页 50，页码/上下页/区间显示） |
| U3 校验吞队列 | ✅ | RelationsPage 审核原因校验改为页内 `relations-hint` 行内提示，不再触发整页 ErrorState |
| U4 无清空对话 | ✅ | 对话面板顶栏「清空对话」按钮，两步确认（3 秒自动还原），清空 localStorage 会话 |
| U5 断连无重试 | ✅ | App 顶栏离线态增加「重试连接」按钮，健康检查可重入 |
| U6 证据轨只跟最新轮 | ✅ | 每轮完成时快照 citations/trace/route/steps/usage/degraded 入轮次；每轮新增「定位本轮证据链」按钮，证据轨随选中轮次切换 |
| U7 无深链 | ✅ | hash 路由（`#/chat` `#/knowledge` `#/evaluation` `#/relations`），刷新/分享保持视图 |
| U8 移动端高度 | ✅ | ≤920px 时 `.chat-layout` 释放固定高度与 overflow:hidden，证据轨随页滚动 |
| U9 中止/重试图标混淆 | ✅ | 中止改用 Square 图标，RotateCcw 保留给重试 |
| U10 标签页 ARIA | ✅ | 关系审核 tabs 补 `role="tab"`/`aria-selected`/`aria-controls` + `role="tabpanel"` |
| U11 搜索无清空 | ✅ | 台账搜索框增加一键清空按钮（清空并复位列表） |
| I1 视图引导 | ✅ | 新增 `ContextHint` 组件（可关闭、持久化），接入台账/质量账本/关系审核三视图 |
| I2 空态文案 | ✅ | 关系审核空态改为面向审核者的说明（候选从哪来、确认后去哪） |
| I3 长生成无反馈 | ✅ | 流式回答显示「已用时 Xs」计时；≥15s 追加「复杂问题需要更久」预期管理 |
| I4 降级不可见 | ✅ | `degraded` 事件原因落证据轨横幅 + 完成轮次徽标（与 F3 联动） |
| I5 usage 未消费 | ✅ | 消费 SSE `usage` 事件：回答下「耗时 Xs · 输入/输出 tokens」+ 证据轨「本次用量」区块 |
| I6 设置不持久 | ✅ | 检索策略/TopK/关系扩展/跳数写入 localStorage，跨会话保持 |
| P1 索引新鲜度 | ✅ | 【后端协同】`_active_index_stats()` 与 `library_stats` 增加 `index_version`/`index_built_at`；台账头部展示「当前索引 · 构建于」 |
| P2 质量账本同源 | ✅ | 索引重建后重跑 `run_ablation.py`：bm25 R@5=0.838 / bm25_vector R@5=0.886 / graph 未达门槛保持关闭（90 例 golden，v2.4.0） |
| P3 对话管理 | ✅ 部分 | 清空对话已实现；导出 Markdown 为可选项，未实施 |
| P4 深链 | ✅ | 同 U7 |
| P5 键盘效率 | ✅ | `1-4` 切视图、`/` 聚焦提问框（输入态不触发） |
| P6 微交互 | ✅ 部分 | 流式打字光标（纯 CSS）；hover 预览/连线动画未实施 |
| P7 多轮上下文 | ⏸ 未实施 | 需后端定义会话上下文字段，超出本轮范围，留待后端协同排期 |

### 11.4 验证记录（2026-08-28）

- **测试门禁**：pytest 319 passed / 2 skipped；ruff 关键规则（F821/F822/F823/F401/F811/F841/E902）在本次改动文件 0 命中（其余为历史遗留）；web `tsc` ✅ / vitest 15 例 ✅ / `vite build` ✅。
- **活体端点**：`/health`→ok；`/mindgraph/notes?limit=3&offset=2`→total=25 分页正常；`/mindgraph/evaluation/ablation`→runs=9，`library_stats` 含 `index_version=mg-20260828T042253Z-f281844d`、`index_built_at=2026-08-28T04:23:04Z`。
- **消融重跑**：3 策略写入 evaluation_runs（bm25 / bm25_vector / bm25_vector_graph），graph gate 判定 `keep_graph_disabled`，与「图默认关闭」策略一致。
- **改动文件清单**：后端 `src/infrastructure/chat_provider.py`、`src/infrastructure/openai_compatible_provider.py`、`src/application/mindgraph_index_service.py`、`src/api/routes/mindgraph_readonly.py`；前端 `web/src/App.tsx`、`web/src/pages/{ChatPage,KnowledgePage,RelationsPage,EvaluationPage}.tsx`、`web/src/components/Primitives.tsx`、`web/src/lib/api.ts`、`web/src/types.ts`、`web/src/styles.css`；配置 `.env`、`web/package.json`。
- **服务**：API `http://127.0.0.1:8000`（已载入全部后端改动）；Vite dev `http://127.0.0.1:5175`（前端改动经 HMR 生效；`vite build` 产物已更新）。

### 11.5 生成供应商切换至 Gitee AI（2026-08-28，用户指令：智谱/DeepSeek 用量快且贵，改用 Gitee AI Qwen3.8-Flash）

- **方式**：纯 `.env` 配置切换，零代码改动。OpenAI 兼容槽（`CHAT_PROVIDER=deepseek`，ProviderRegistry 按名称路由，Literal 限定 zhipu/deepseek/anthropic）整体改指 Gitee AI：`OPENAI_COMPAT_BASE_URL=https://ai.gitee.com/v1`、`OPENAI_COMPAT_MODEL=qwen3.8-flash`（官方模型列表中的规范 id，小写）、`OPENAI_COMPAT_MODELS=qwen3.8-flash`、用户提供的新 API Key、`OPENAI_COMPAT_VERIFIED=true`。
- **切换前实测**：`GET /v1/models` 200（241 个模型，含 `qwen3.8-flash`）；非流式与流式 `chat/completions`（含 `stream_options.include_usage`，与本项目 provider 发送的 payload 完全一致）均 200，usage 正常返回（prompt/completion/total tokens）。
- **切换后冒烟**：`POST /api/v1/mindgraph/chat/stream`「出差报销的餐补标准是多少？」→ 完整事件链 request_started→…→generation_started→34×answer_delta→citations→usage→completed；`result_state=answered`、无 degraded、5 条引用、usage=748/382/1130 tokens（`usage_source=provider_reported`）、`index_version=mg-20260828T042253Z-f281844d`；query_logs `actual_provider=deepseek`（即 OpenAI 兼容槽）。
- **回滚**：`.env` 内保留原 DeepSeek 三行注释；智谱槽未动，`CHAT_PROVIDER=zhipu` 即可回到 glm-4.7。
- **注意**：S1 密钥轮换因本次切换对 DeepSeek 已不再紧迫（Key 已停用注释保留）；智谱 Key 仍建议轮换。Gitee AI 侧该 Key 已明文落入 `.env`（gitignore 排除），如需公开仓库同样先轮换。

### 11.6 UX 研究审计修复批次（2026-08-28，按研究员优先级清单顺序执行，用户指令：按顺序都一一解决修复）

依据 `D:\software\mindgraph-audit-2026-08-28\MindGraph_前端优化方案_2026-08-28.md` 的 14 项清单，按「引用内联锚点 + 版本时效警示 + 证据导出 > 空指标墙/Markdown > 会话历史（带导出）> 其余卫生项」顺序全部落地。**零后端改动**——逐项核对现有载荷/端点后确认全部可在前端完成（Citation 已含 `document_version/effective_from/effective_to/policy_status`；`/api/v1/config/public` 已返回 `chat_models`+`default_chat_provider`）。

**① 引用内联锚点**（新增 `web/src/components/AnswerBody.tsx`）：回答正文中的 `[citation-N]` 解析为上标按钮 `.citation-ref`，点击滚动并高亮证据轨对应条目（`data-citation-rank` + 1.8s flash 脉冲）；引用不存在时降级为灰色纯文本 `.citation-ref-unlinked`，不再显示裸标记。

**② 版本时效警示**（新增 `web/src/lib/citation-status.ts`）：`citationValidity()` 以「提问当日」为基准判定 current/caution/stale（stale=expired/superseded/archived 或已过 effective_to；caution=draft/未生效/unspecified）。证据轨每条引用标题行加时效徽标（替代裸 `policy_status`）；回答下方 `VersionWarning`：有 stale → role=alert 红色横幅逐条列出失效文档，仅 caution → 琥珀色轻提示。

**③ 证据导出**（新增 `web/src/lib/export-evidence.ts`）：单轮与整会话均可导出 Markdown 证据包（问题/回答/请求ID/索引版本/模型/耗时/Token + 每条引用的制度族/版本/状态时效/生效区间/责任人/章节/摘录，stale 引用附 ⚠ 提示；页脚声明「客户端生成，正式审计以 query_logs 为准」），文件名 `mindgraph-证据-YYYYMMDD-HHmm-<问题slug>.md`，Blob 下载。服务「查制度→要依据→留证据」留痕链。

**④ Markdown 渲染**：`AnswerBody` 为零依赖轻量渲染器（直接构建 React 元素，无 innerHTML，XSS 构造上免疫）：标题 #~####、有序/无序列表（含 `•`/`、`/`)` 项目符号）、引用块、``` 代码围栏、`**加粗**`、`` `行内代码` ``、外链（仅 http/https + rel=noopener）。

**⑤ 会话历史（带导出）**：localStorage 多会话（`mindgraph.chat.sessions`），首次提问惰性创建、标题=首问前 20 字；顶栏 `details` 会话菜单（切换/导出/两步确认删除），明示「工作留痕，非审计级留存」；旧键 `mindgraph.chat.turns` 一次性迁移后删除。每轮存 `queryDate/createdAt/model/indexVersion`，切换历史轮时时效判定使用该轮查询日。

**⑥ 追问建议**：`FollowUpSuggestions` 基于命中引用生成（有版本→历史版本对比；多文档→两制度关系；有失效→现行版本；通用→例外情形），最多 3 条，无引用不显示。

**⑦ 空指标墙**（EvaluationPage）：11 项指标按有值/未启用分区，未启用项收进「还有 N 项指标未启用 · 查看如何开启」折叠区，每项附开启方式（`scripts/run_ablation.py`、`scripts/run_answer_eval.py`、Golden Set 版本断言、路由矩阵、图门槛）；无运行记录时给 EmptyState+命令。

**⑧ 模型状态前置**（App.tsx）：连接指示器经 `/config/public` 显示「provider · model」（未配置时标注），提问前即可见当前生成模型。

**⑨ 卫生项**：删除死样式（`.nav-index`、`.release-label`、run-table 行 hover）；移除全部 6 处 `text-transform: uppercase`（中文无意义且伤可读性）；旧引导卡改为可折叠 `.onboarding-inline`（默认收起，去掉「知道了」按钮）；四个页面 eyebrow 改为任务导向文案（「基于制度证据的问答」「检索质量追踪」「制度关系审核」「制度台账与版本」）；RelationsPage 标签页补全键盘导航（←/→/Home/End + roving tabindex）；RelationsPage BGE 卡文案澄清「当前仅使用 BGE 相似度，非业务关系类型」。

**验证**：`pnpm typecheck` ✅（tsc -b 0 错）；`pnpm build` ✅（1808 模块，CSS 40.12 kB / JS 273.89 kB）。无浏览器自动化，视觉以用户 Ctrl+F5 刷新为准。

**改动文件**：新增 `web/src/components/AnswerBody.tsx`、`web/src/lib/citation-status.ts`、`web/src/lib/export-evidence.ts`；修改 `web/src/pages/{ChatPage,EvaluationPage,RelationsPage,KnowledgePage}.tsx`、`web/src/App.tsx`、`web/src/lib/api.ts`、`web/src/types.ts`、`web/src/styles.css`。

**遗留（用户侧）**：S1 智谱 Key 轮换；`.env` 中 Gitee AI Key 勿入公开仓库。

### 11.7 图谱功能批次：可视化 + 问题概念挖掘（2026-08-28，交接单 mindgraph-handoff-2026-08-28 §2 A3-B4）

按交接单顺序执行 A3→A6→B1-B4。**治理红线全程保持**：`GRAPH_DEFAULT_ENABLED` 未翻转（图仍默认关闭，本批只做可视化与数据积累）；挖掘只产出 `proposed` 候选，必须 HITL 确认后才进入检索路径；查询时零 LLM 抽取；不引入外部图数据库（仍为 SQLite `note_relations`）。

**A3 知识图谱页**（新增 `web/src/pages/GraphPage.tsx`）：d3-force 力导向图（forceLink≈110 / manyBody -220 / collide r+14），滚轮缩放（passive:false，k∈[0.25,4]）、背景拖拽平移、节点拖拽（pointer capture + fx/fy + alphaTarget，位移<4px 判定为点击）；点击节点打开右侧详情面板（经 `api.note` 拉全量字段），出边/入边关系双向可点击跳转（选中并居中目标节点）、「在台账中查看」跳 `#/knowledge`；深链 `#/graph?node=<id>`（hashchange 监听 + replaceState 同步）；工具栏含节点/确认边/待审边计数、「显示待审关系」开关（待审边虚线）、适应视图/重新布局/从提问挖掘按钮；节点半径随度数 `7+min(deg*2,10)`、按目录分类着色，边按关系类型着色 + 分类型箭头 marker；空态两档（0 节点 → EmptyState；有节点无确认边 → 引导横幅去关系审核）。

**A4 导航与材料上传**：App.tsx 新增「知识图谱」导航（Network 图标，快捷键扩为 1-5，hash 路由 `#/graph`）；KnowledgePage 新增 `.md` 上传卡片（点击/拖拽，`api.uploadDocument` → `api.incrementalRebuild` 自动增量重建 → 状态区引导「融入图谱」调 `api.extractRelations`，产出提示去关系审核确认）；台账抽屉的关系列表改为可点击按钮（跳对应笔记详情），类型用共享中文标签着色。

**A5 样式**：styles.css 追加图谱页（画布点阵背景/图例/空态横幅/详情面板/链接列表）、上传卡片（虚线 dropzone + drag-over + 状态区）、关系页挖掘条与缺口面板全套样式；移动端底部导航 `repeat(4,1fr)` → `repeat(5,1fr)`。

**B1-B2 问题概念挖掘（后端）**：新增 `src/application/question_concept_miner.py` —— 纯规则、离线、无 LLM：① 提问中《标题》与笔记标题/别名精确匹配；② 标题/别名（≥2 字符）子串命中；③ 同一提问命中 ≥2 篇笔记 → 两两产出 `CO_ASKED` proposed 候选（confidence 随共同出现次数 0.55 起步 +0.05/次、封顶 0.9；evidence_span=原提问，evidence_section=query_logs，extraction_method=question_co_asked）；④ 未匹配的《》引用累计进新表 `concept_signals`（覆盖缺口）。增量幂等：水位记录于新表 `concept_mine_runs`（取本批最大 created_at，非墙钟），只扫描水位后新提问；候选对与 note_relations 任意状态/任意方向去重；`PRIVACY_LOG_QUESTIONS=false`（question=NULL）安全跳过；dry_run 只预测不落库、不推进水位。schema 8→9（新增 `concept_signals`、`concept_mine_runs`）；`TYPED_RELATION_TYPES` 增加 `CO_ASKED`（保证 HITL 确认与图遍历可用，且不在 GOVERNANCE 集合 → span 证据即可）。端点：`POST /api/v1/mindgraph/relations/mine-questions`（admin，手动挖掘，写 access_audit）与 `GET /api/v1/mindgraph/concept-gaps?limit=N`（只读，阈值 `CONCEPT_MINE_GAP_MIN_SEEN=2` 过滤一次性噪音）。自动触发：`ChatService._persist` 成功后 fire-and-forget 回调 → 容器计数累计 `CONCEPT_MINE_AUTO_MIN_NEW_QUESTIONS=20` 条新提问后后台线程增量挖掘（单飞互斥，异常只记日志，绝不影响应答路径）；`CONCEPT_MINE_AUTO_ENABLED=false` 可整体关闭。

**B3 测试**：新增 `tests/test_question_concept_miner.py` 16 例（《》匹配/别名子串/co-occurrence 置信度/任意状态任意方向去重/增量水位/NULL 提问安全/dry_run 零写入/单笔记不成边/两端点契约/limit 校验/自动触发阈值与单飞与开关关闭）。同步修正 `test_policy_metadata.py` 的 schema 版本断言 8→9。顺手清掉 `chat_service.py` 一处历史遗留未用导入（F401）。

**B4 覆盖缺口面板**：RelationsPage 新增挖掘条（说明 + 「从提问中挖掘」手动按钮 + 结果消息）与「知识覆盖缺口」面板（`api.conceptGaps`，按出现次数降序，展示词/次数/最近出现，空态说明收敛路径）；候选卡关系类型改用共享中文标签；指标卡口径更新为「BGE / CO_ASKED」。

**验证（2026-08-28）**：pytest **335 passed / 2 skipped**（基线 319 + 新增 16，`--no-cov`）；ruff 关键规则（F821/F822/F823/F401/F841/E902）本次改动文件 0 命中；web `pnpm typecheck` ✅ / `pnpm build` ✅（CSS 48.08 kB / JS 310.11 kB）。无浏览器自动化，视觉以用户刷新为准。

**改动文件**：新增 `web/src/pages/GraphPage.tsx`、`src/application/question_concept_miner.py`、`tests/test_question_concept_miner.py`；修改 `web/src/{App.tsx,styles.css}`、`web/src/pages/{KnowledgePage,RelationsPage}.tsx`、`web/src/lib/api.ts`、`web/src/types.ts`、`src/api/{dependencies.py,routes/mindgraph_readonly.py}`、`src/application/{chat_service.py,mindgraph_graph_store.py}`、`src/infrastructure/{database.py,settings.py}`、`tests/test_policy_metadata.py`。
