# MindGraph 前端界面设计优化提案（2026-08-28）

> 审查范围：`web/src/styles.css`（2498 行）、`App.tsx`、`components/Primitives.tsx`、`pages/{ChatPage,KnowledgePage,EvaluationPage,RelationsPage}.tsx`
> 硬约束：纯前端视觉/交互层；不新增后端 API；不破坏已有 ARIA 改造成果；保持 4 视图信息架构。

---

## 0. 现状视觉基线

| 维度 | 现状 |
|------|------|
| 主题 | 暖纸色浅底（`--paper-deep:#e9e5dc`）+ 深炭侧栏（`--sidebar:#171916`）+ 朱砂红品牌色（`--accent:#db4b2f`） |
| 字体 | 标题 Noto Serif SC（衬线）/ 正文 IBM Plex Sans + Noto Sans SC / 数据 IBM Plex Mono |
| 图形母题 | 知识图谱节点连线（favicon）、证据印章（confirmed-stamp 旋转图章） |
| Token 化程度 | `:root` 仅 18 个 token；全文另有 ~20 处一次性硬编码色值 |

整体评价：纸感 + 衬线 + 印章的「档案/凭证」气质与「依据工作台」定位高度契合，是应当保留并强化的资产。问题集中在 **token 不完整、语义色未分层、数据密集区可读性、少量样式缺失**。

---

## 1. P0 —— 立即修复（缺陷级，本轮已顺手修掉前两项）

### P0-1 ✅已修 `--line-strong` 未定义
- **现状**：`styles.css` L1724 `.relation-review-controls textarea` 引用 `var(--line-strong)`，该变量从未定义 → `border-color` 声明失效回退 `currentColor`（墨黑），属意外渲染。
- **方案**：在 `:root` 补定义 `--line-strong:#b3ada0`（介于 `--line` 与 `--line-dark` 之间）。
- **影响面**：`styles.css` `:root` 一行。

### P0-2 ✅已修 `.answer-actions` / `.feedback-row` 无样式
- **现状**：`ChatPage.tsx` L613/L621 使用 `answer-actions`、`feedback-row` 类名，`styles.css` 无对应规则 → 错误重试行与回答反馈行（有帮助/没帮助按钮）无间距、无布局，按钮紧贴正文。
- **方案**：补 `.answer-actions{display:flex;flex-wrap:wrap;align-items:center;gap:10px;margin-top:12px}`，`.feedback-row` 追加 `padding-top:10px;border-top:1px dashed var(--line)` 形成「回答 / 反馈」视觉分段。
- **影响面**：`styles.css` 追加两条规则。

### P0-3 关系审核 tabs 缺键盘导航
- **现状**：`RelationsPage` tabs 已有 `role="tab"`/`aria-selected`/`aria-controls`（U10 成果），但无 ←/→ 方向键切换，键盘用户只能 Tab 逐个进入。
- **方案**：tabs 容器加 `onKeyDown`：ArrowRight/ArrowLeft 循环切换焦点与选中项，Home/End 跳首尾；非激活 tab 设 `tabIndex={-1}`（roving tabindex，WAI-ARIA Tabs 标准模式）。
- **影响面**：`RelationsPage.tsx` tabs 渲染处约 20 行；纯前端。

---

## 2. P1 —— 近期优化

### P1-1 语义色分层（成功/警告/错误/降级）
- **现状**：`--moss/--amber/--danger` 同时承担装饰与语义两种职责；降级徽标（amber）、错误态（danger）、确认态（moss）各自为政，`accent-dark` 还混入链接/强调场景。
- **方案**：新增语义别名层（不改现有色值，零视觉风险）：
  ```css
  --success: var(--moss); --warning: var(--amber); --error: var(--danger);
  --success-soft:#e7ebe2; --warning-soft:#f2e6d2; --error-soft:#f0ded9;
  ```
  状态类（`.status-pill`、`.degraded-badge`、`.error-state`、`.feedback-failed`）统一改用语义 token；三个 `-soft` 底色用于徽标/横幅的浅底深字，替代目前直接压在纸面上的细边框。
- **影响面**：`styles.css`（token + ~10 个状态类）。

### P1-2 对比度与小字号（WCAG AA）
- **现状**：`.relation-review-controls label` 为 **9px + #77736a**，在 `--paper` 上对比度约 3.4:1，低于小文本 4.5:1 要求；全篇另有若干 9-10px 文本（`confirmed-stamp` 9px、部分 mono 标注 9px）。
- **方案**：正文类文本最小 11px；label 类提到 10.5-11px 并将色值收到 `--ink-soft`（#4e524b，对比度 ≈6.6:1）；印章/装饰性 mono 可保留 9px（属装饰，AA 对 incidental text 豁免）但需 `letter-spacing` 保持。
- **影响面**：`styles.css` ~6 处 font-size/color。

### P1-3 硬编码色值收编
- **现状**：`#efebe2`、`#eadfce`、`#d4c2aa`、`#5f5548`、`#393c35`、`#30332d`、`#b9bcb3`、`#eee9dd`、`#a9ada3`、`#77736a` 等一次性色值散落 ~20 处，侧栏内尤其多。
- **方案**：补 token：`--paper-soft:#efebe2`、`--warning-soft`（见 P1-1）、`--sidebar-line:#30332d`、`--sidebar-line-strong:#393c35`、`--sidebar-ink:#eee9dd`、`--sidebar-ink-soft:#a9ada3`、`--sidebar-ink-dim:#b9bcb3`，逐处替换。
- **影响面**：`styles.css` 纯替换，无行为变化。

### P1-4 数据密集视图可读性（台账/质量账本）
- **现状**：表格无行悬停、无斑马纹，数字列（R@5、tokens、耗时）用衬线 `metric-value` 展示单卡尚可，列内数字不对齐。
- **方案**：① 数字列统一 `font-variant-numeric: tabular-nums`（等宽数字，列自然对齐）；② 行 `:hover` 上 `--paper-soft` 底；③ >8 行的表格加 `nth-child(even)` 极浅斑马（`rgb(28 26 20 / 2.5%)`）；④ 指标卡数字保留衬线但加 `tabular-nums`。
- **影响面**：`styles.css` 表格与 `.metric-value` 相关 ~8 处。

### P1-5 证据轨信息架构（聊天视图右栏）
- **现状**：引用 / 检索轨迹 / 路由决策 / 用量多块纵向堆叠，长回答时关键引用常被推出首屏；正文 `[citation-N]` 与右栏引用卡无联动。
- **方案**：① 证据轨整体 `position: sticky; top: 16px`（桌面断点），随滚动常驻；② 引用卡加 `:target`/hover 高亮：正文引用标记 hover 时对应卡描边 `--accent`（纯 CSS 可用 `:hover` + 兄弟选择器有限实现，若不够则 ~15 行 TSX state）；③ 每个证据块标题行统一为可折叠 `<details>`（默认引用展开、轨迹折叠）。
- **影响面**：`styles.css` + `ChatPage.tsx` 证据轨区块。

### P1-6 加载与空态升级
- **现状**：`LoadingState` 单一 spinner + 文案；台账/评估页首屏加载时布局跳动。
- **方案**：为列表与指标卡提供 skeleton 占位（`background: linear-gradient(90deg, --paper, --paper-soft, --paper)` + `background-size:200%` 流光动画），`LoadingState` 增加 `variant="list"|"metrics"` 属性。
- **影响面**：`Primitives.tsx` + `styles.css`；各页面按需接入（可分批）。

### P1-7 微交互与状态反馈
- **现状**：流式打字光标已有（P6 成果）；按钮无按压反馈；导航切换无过渡。
- **方案**：① 按钮 `:active{transform:translateY(1px)}`；② `.nav-button.active` 的左侧指示条加 `transition: transform 180ms`；③ 视图切换保留现有 `reveal` 淡入，但给 `prefers-reduced-motion` 用户关闭（见 P2-3）；④ 复制引用/请求号成功时按钮短暂变 `✓ 已复制`（已有则核对一致性）。
- **影响面**：`styles.css` 为主。

### P1-8 移动端（≤920px）收尾
- **现状**：U8 已释放聊天区固定高度；侧栏折叠为顶栏后导航横向排列。
- **方案**：① 反馈行按钮在窄屏 `flex-direction: column; align-items: stretch`（拇指可达）；② 证据轨折叠块标题行触控目标 ≥44px；③ 台账表格窄屏转「卡片式行」（`display:block` + data-label 伪元素方案，避免横向滚动）。
- **影响面**：`styles.css` 媒体查询区块。

---

## 3. P2 —— 可选增强

| 编号 | 建议 | 说明 |
|------|------|------|
| P2-1 | 深色主题（`prefers-color-scheme: dark`） | token 层已具备，翻转为暗色只需一套变量覆盖；侧栏天然是深色样板 |
| P2-2 | 打印样式 | 引用清单 `@media print` 优化（去侧栏、引用编号转脚注格式），配合「导出 Markdown」计划项 |
| P2-3 | `prefers-reduced-motion` 全局开关 | 关闭流光/淡入/光标动画 |
| P2-4 | 空态插画 | 台账/关系审核空态加单色线稿插画（图谱母题），提升第一印象 |
| P2-5 | 焦点可见性增强 | 现有 `outline: 3px rgb(accent/28%)` 偏淡，深色侧栏上建议提到 40% |

---

## 4. 建议新版设计 Token 表

```css
:root {
  /* 基底（保留现有） */
  --ink:#1b1d19; --ink-soft:#4e524b;
  --paper:#f6f2e9; --paper-deep:#e9e5dc; --paper-bright:#fffdf8;
  --line:#cbc5b8; --line-strong:#b3ada0; --line-dark:#9d988e;   /* 补 line-strong */
  --accent:#db4b2f; --accent-dark:#a52f1d;
  --sidebar:#171916; --sidebar-soft:#252823;

  /* 新增：纸面衍生 */
  --paper-soft:#efebe2;

  /* 新增：语义层（映射现有色，零视觉风险） */
  --success:#4d674f; --warning:#b87322; --error:#9e3327;
  --success-soft:#e7ebe2; --warning-soft:#f2e6d2; --error-soft:#f0ded9;

  /* 新增：侧栏文字/线 */
  --sidebar-line:#30332d; --sidebar-line-strong:#393c35;
  --sidebar-ink:#eee9dd; --sidebar-ink-soft:#a9ada3; --sidebar-ink-dim:#b9bcb3;

  /* 字号阶梯（现状梳理 + 收敛） */
  --text-xs:10.5px; --text-sm:12px; --text-base:13.5px; --text-lg:16px;
  --text-xl:20px; --text-display:26px;
  /* 正文类文本下限 11px（AA 可读性）；9px 仅限装饰性印章/mono 标注 */

  /* 间距阶梯（现状以 4px 为基数，显式化） */
  --space-1:4px; --space-2:8px; --space-3:12px; --space-4:16px;
  --space-5:24px; --space-6:32px;

  /* 圆角/阴影（保留） */
  --radius-sm:8px; --radius-md:14px; --radius-lg:22px;
  --shadow:0 20px 60px rgb(28 26 20 / 10%);
  --shadow-pop:0 8px 24px rgb(28 26 20 / 14%);   /* 浮层/悬浮卡用，比 --shadow 轻 */

  /* 数字对齐 */
  --numeric: tabular-nums;
}
```

---

## 5. Logo 更新说明（v2 印章路线，2026-08-28 下午，按 UX 研究评审规格重做）

> 演进记录：v1（当日上午）为「众证归一」节点图（三节点汇入带对勾中心节点）。经 UX 研究员评审
> 《MindGraph_Logo优化方案_2026-08-28.md》后改采**印章路线**——节点网络属品类陈词滥调
> （Logseq/Obsidian 同款），印章（核实盖章）与合规域同构，直接编码「审核通过、有据可查」的产品承诺；
> 且与聊天首屏既有的「可审计」印章元素（`.intro-seal`）形成品牌呼应。v1 图形弃用。

### 设计理念
**「核实盖章」**——一方印章盖在制度文书上，印内对勾表示核验通过。印章是编辑/出版/法务的视觉语言，
脱离科技几何模板感；方章 + 对勾落在「已验证」语义区，以文档折角作为差异化元素（仅大尺寸层级保留）。

### 图形构成
- **印章框**：圆角方形描边（24 网格：`x 6.2..19.4`，圆角 2.4，右上角切折角 3.8）
- **文档折角**：右上角折角线（`M15.6 5.4 V9.2 H19.4`），代表被盖章核验的制度文书
- **核验对勾**：印内圆头折线（`M9.2 12.8 l2.3 2.3 4.3-4.8`）

### 尺寸层级（评审要求：16px 专用简化资产）
| 层级 | 资产 | 内容 | 关键参数 |
|------|------|------|----------|
| ≥24px 完整版 | 侧栏 `.brand-mark` 内联 SVG | 印章框 + 折角 + 对勾 | 24 网格，stroke 1.7（22px 渲染 ≈1.56px） |
| 16-20px 简化版 | `web/public/favicon.svg` | 加粗印章框 + 对勾，**折角删除** | 32 网格，stroke 3（16px 渲染 = 1.5px，满足 ≥1.5px 要求） |

### 色彩与底色适配（评审要求：恢复主题适配，不硬编码）
| 场景 | 用色 | 对比度 |
|------|------|--------|
| 深底（favicon：深炭容器 `#171916`） | 亮朱印框 `#e05a41` + 奶油对勾 `#f6f2e9` | 印框 4.9:1、对勾 ≈13:1（均过非文本 3:1） |
| 品牌 blob（侧栏朱砂底 `--accent`） | `currentColor` 奶油单色线稿 | 随 `color:#fff4e8` 继承，恢复主题适配能力 |
| 浅底/单色（审计报告 PDF、水印） | 单色 `#1b1d19` 或 `#a52f1d`（见下方单色版） | accent-dark 对纸底 ≈6.2:1 |

原审计方案硬编码 `#B04736`（对深炭仅 3.2:1 且丢失 currentColor）、折角 0.85 透明度两处问题均已修正。

### 单色版（合规刚需：导出审计报告/证据文件 PDF 触点）
```svg
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="#1b1d19"
     stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">
  <path d="M8.6 5.4 H15.6 L19.4 9.2 V16.2 Q19.4 18.6 17 18.6 H8.6 Q6.2 18.6 6.2 16.2 V7.8 Q6.2 5.4 8.6 5.4 Z"/>
  <path d="M15.6 5.4 V9.2 H19.4"/>
  <path d="M9.2 12.8 l2.3 2.3 4.3-4.8"/>
</svg>
```

### 使用规范
| 项 | 规范 |
|------|------|
| 最小尺寸 | 16px（用简化版）；完整版最小 24px；低于 16px 禁用 |
| 留白 | 独立使用时四周留白 ≥ 印框宽度 25% |
| 验收环境 | 必须在 Chrome 标签页 16px、Windows 任务栏、书签栏、32px Retina 实测，不许只看设计稿 |
| 禁用 | 不改印框色相；不在复杂背景裸放（需容器底）；不给对勾加描边/阴影；折角不使用半透明填充 |

### 连带可访问性修复（评审发现）
- `.intro-seal`「可审计」13px 文本原用 `--accent`（对纸底 ≈3.7:1，不过 AA 正文 4.5:1）→ 改 `--accent-dark`（≈6.2:1）
- 其余 `color: var(--accent)` 仅存于 `.trace-step.running` 图标（非文本 3:1 达标），不改

---

## 6. 落地顺序建议

1. **本轮已完成**：P0-1、P0-2（CSS 缺失修复）+ Logo 更新（v2 印章路线，含 16px 简化版/单色版/对比度修复，见 §5）
2. **下一批（约 0.5 天）**：P0-3 tabs 键盘导航、P1-1 语义色、P1-2 对比度、P1-3 色值收编 —— 全部只动 `styles.css`/`RelationsPage.tsx`，风险最低
3. **再下一批（约 1 天）**：P1-4 数据可读性、P1-5 证据轨 sticky/联动、P1-6 skeleton
4. **按需**：P1-7/P1-8 与全部 P2 项
