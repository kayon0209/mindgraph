# RAG 报销政策助手 · Web UI 生图设计 Prompt

> 用途：输入到图像生成模型（如 ChatGPT / DALL·E / 类似生图工具），生成一张可用于指导前端实现的 UI 设计参考图。
> 设计目标：在现有 FastAPI 后端能力基础上，构建一个**视觉丰富、信息密度高、企业级专业感**的 Web 控制台，解决原 Streamlit 页面"内容空白、视觉薄弱"的问题。

---

## 中文描述性 Prompt（直接用于生图）

请生成一张**企业级 AI 报销政策问答系统的 Web 控制台界面设计图（UI mockup）**，整体为深色主题（dark mode）的现代化 SaaS 仪表盘风格，采用玻璃拟态（glassmorphism）质感。

### 整体布局（三栏式工作台）
- **顶部导航栏（Top Bar）**：左侧是品牌区——一个圆形渐变 logo 配文字"小财 · 报销政策助手"；中间是全局状态/搜索胶囊；右侧是主题切换按钮（月亮/太阳图标）、API 状态指示灯（绿色圆点"系统正常"）、用户头像。
- **左侧边栏（Sidebar，约 240px）**：竖向导航菜单，包含 4 个功能入口，带图标和选中态高亮：
  1. 政策问答（对话气泡图标，当前选中，靛蓝高亮）
  2. 政策中心（书库图标）
  3. 质量看板（柱状图/监控图标）
  4. 问题改进（任务清单图标）
  侧栏底部有"检索策略预设"快捷卡片。
- **中央主区（Center，对话工作区）**：
  - 顶部是一条**控制工具条**：检索策略下拉（默认"混合检索 hybrid"）、知识分类多选标签（政策/指引/FAQ/参考）、查询日期选择器、历史开关 toggle、Top-K 数值。
  - 中间是**对话消息流**：用户问题气泡（右对齐，靛蓝填充）；AI 回答卡片（左对齐，玻璃卡片），内含结构化答案文本、关键数字高亮、以及一个"引用来源"折叠区列出 2-3 条引用条目（文档名 + 段落 + 置信度条）。
  - 底部是**输入区**：圆角多行输入框 + 发送按钮（金色渐变）+ 附件/语音小图标，输入区上方有流式打字指示（三点跳动动画）。
- **右侧面板（Right Panel，约 320px，检索溯源与性能）**：
  - 顶部标题"检索过程可视化"。
  - 一个**检索流水线时间轴**：检索开始 → 稠密向量 → BM25 稀疏 → RRF 融合 → 重排 → 生成，每步带耗时毫秒小标签和状态点。
  - "候选与重排"区：显示候选数量（如 dense 12 / bm25 10 / 融合 15 / 重排后 5）的迷你条形图。
  - "性能"卡片：TTFT、总耗时、Token 用量（prompt/completion）、实际命中策略徽章。
- **底部浮层数据条（可选）**：一行 4 个质量指标卡（Recall@5、MRR、文档命中率、Chunk 命中率），带微型 sparkline。

### 视觉风格与质感
- 玻璃拟态：卡片使用半透明白色叠加 + 30px 背景模糊 + 1px 半透明白边 + 柔和投影。
- 圆角统一 16-20px，间距宽松（8/12/16/24 栅格）。
- 微交互暗示：按钮悬浮微抬升、卡片入场上浮淡入、流式打字光标闪烁。
- 字体：无衬线现代字体（Inter / 系统字体），标题字重 600-700，正文 400。

### 配色方案（具体色值）
- 背景：深邃蓝黑渐变 `#0B1120` → `#0F172A`（顶部略亮）。
- 主品牌色（靛蓝）：`#6366F1` / `#4F46E5`（按钮、选中态、链接）。
- 财务强调色（琥珀金）：`#F59E0B` / `#FBBF24`（发送按钮、关键数字、KPI 高亮）。
- 成功/合规绿：`#10B981`；警示橙：`#FB923C`；危险红：`#F87171`。
- 主文本：`#E2E8F0`；次级文本：`#94A3B8`；分隔线：`rgba(255,255,255,0.08)`。
- 玻璃卡片：`rgba(255,255,255,0.05)`，边框 `rgba(255,255,255,0.10)`。
- 引用来源卡片：左侧 3px 靛蓝边条 + 淡蓝底 `rgba(99,102,241,0.08)`。

### 需要体现的产品定位
专业、可信赖的企业财务 AI 助手，而非玩具聊天机器人；强调"答案可追溯、检索可解释、质量可度量"。

---

## English Prompt (for image-generation models)

Generate a UI mockup of an **enterprise-grade AI expense-policy Q&A console** in a modern dark-mode SaaS dashboard style with glassmorphism.

**Layout — three-column workbench:**
- **Top bar**: left brand zone (gradient circular logo + "小财 · Expense Policy Assistant"); center global status/search pill; right side theme toggle (moon/sun), green "system healthy" status dot, user avatar.
- **Left sidebar (~240px)**: vertical nav with 4 icon entries — Policy Chat (selected, indigo highlight), Policy Center (library), Quality Dashboard (monitor), Issue Improvement (task list). Bottom: a "retrieval strategy preset" quick card.
- **Center column (chat workspace)**:
  - Top control bar: retrieval-strategy dropdown (default "Hybrid"), multi-select knowledge-category chips (Policy/Guideline/FAQ/Reference), query-date picker, history toggle, Top-K stepper.
  - Middle message stream: right-aligned user bubbles (indigo fill); left-aligned AI answer glass cards with structured answer, highlighted key numbers, and a collapsible "Citations" section listing 2-3 source entries (doc name + snippet + confidence bar).
  - Bottom input zone: rounded multiline input + gold-gradient send button + attachment/voice icons; typing indicator (3 bouncing dots) above input.
- **Right panel (~320px, retrieval trace & performance)**:
  - Title "Retrieval Process".
  - A retrieval pipeline timeline: retrieve → dense → BM25 → RRF fusion → rerank → generate, each step with ms latency tag and status dot.
  - "Candidates & Rerank" mini bar chart: dense 12 / bm25 10 / fused 15 / reranked 5.
  - "Performance" card: TTFT, total ms, token usage (prompt/completion), actual strategy badge.
- **Bottom floating data strip**: 4 KPI cards (Recall@5, MRR, doc hit-rate, chunk hit-rate) with tiny sparklines.

**Visual style**: glassmorphism (translucent white overlay, 30px blur, 1px translucent border, soft shadow); 16-20px radii; generous spacing; hover lift, fade-in entrance, blinking caret. Modern sans-serif (Inter).

**Color scheme**: background gradient `#0B1120`→`#0F172A`; primary indigo `#6366F1`/`#4F46E5`; finance accent amber `#F59E0B`/`#FBBF24`; success green `#10B981`; text `#E2E8F0`/`#94A3B8`; glass card `rgba(255,255,255,0.05)` with `rgba(255,255,255,0.10)` border; citation card left 3px indigo bar + `rgba(99,102,241,0.08)` tint.

**Positioning**: professional, trustworthy enterprise finance AI assistant — answers are traceable, retrieval explainable, quality measurable.
