# MindGraph 路线图外部依赖 · 公开数据来源整理

> 用途：把路线图里 6 个"外部验收依赖"逐一映射到**真实可访问、可复现、免费/开源**的公开数据或自托管服务，
> 让你在没有公司数据的前提下也能把项目推到"5 个 Done + 1 个诚实 Partial"。
> 所有来源均在 2026-08-27 通过 WebSearch / 实际下载核验过真实性。
> 已实际下载的样本放在仓库 `data-sources/`（git-ignored 或按需提交，见末尾说明）。

---

## 0. 诚信红线（先读）

| 可以说 ✅ | 不能说 ❌ |
|---|---|
| 基于公开企业 handbook（Mattermost / GitLab / 37signals）构建评测集 | 脱敏自真实企业工单/内部数据 |
| 与自托管 Keycloak 完成 OIDC + JWK 轮换 E2E | 接入客户/某公司的生产 IdP |
| OCR 在公开扫描文档（政府公报 / Internet Archive 古籍）上实测 | 扫描了"公司合同/发票" |
| k6 压测部署在 Railway 的本人实例 | 生产环境并发验收 |
| 连接器 ownership 迁移脚本 + 合成数据验证（零删除） | 完成生产连接器数据迁移 |
| Graph ON/OFF 消融基于本项目 46 条 golden 集跑出真实差值 | 基于真实生产流量消融 |

**"真实"= 真实跑出来的、可复现的结果**，不是"真实公司数据"。公开语料 + 你自己的部署足够产出真结果。

---

## 1. 50–200 条脱敏业务问题集  → 用公开企业 handbook 自建

**可行性：✅ 完全可做，且正对应你 earlier 的 `external_policy` 候选。**

### 1.1 已验证的真实 handbook 源（直接可摄入）

| 来源 | 报销/差旅条款样例（已核验存在） | 摄入入口 |
|---|---|---|
| **Mattermost Handbook** – How to get paid | US/CA 双周发薪；报销走 Airbase（ACH/wire）；UK/DE 同理；ROW 承包商走 Airbase 发票；不支持 PayPal/Venmo/WorldRemit 等 | `https://handbook.mattermost.com/operations/finance/staff-member-expenses/how-to-get-paid` |
| **Mattermost** – Spend company money | 居家办公设备（笔记本支架 180 / 包 60 / 书 30 月 / 摄像头 75…）；专业发展 500 USD/财年；家庭网络 50 USD/月；差旅个人/公务分离 | `https://handbook.mattermost.com/operations/finance/staff-member-expenses/` |
| **GitLab Handbook** – Global T&E | 地面交通 $300/往返、$150/单程；停车/过路 $150/天；出差网络 $50/天；书 $60/年；名片 $150/季度； coworking $700/月；单笔 ≤$5,000 走 Navan、>$5,000 走采购；>90 天不报销 | `https://handbook.gitlab.com/handbook/finance/expenses` |
| **37signals / Basecamp Handbook** – Benefits | 居家办公设备 $1,000/3 年；coworking $200/月；无正式 expense policy（信任制）；AmEx 卡；收据门槛 $50 | `https://github.com/basecamp/handbook`（含 `benefits-and-perks.md`） |

> ⚠️ **直接解锁你 earlier 的 4 条 `external_policy` 候选**：它们引用的 `external/public/mattermost-handbook/.../how-to-get-paid.md` 正是上面第 1 行这个页面。我已把该页 HTML 下载到 `data-sources/handbooks/mattermost/how-to-get-paid.html`（602KB，含 63 处 "Airbase"、60 处 "reimbursement"，内容真实），摄入后即可把那 4 条从 hold 转 approved。

### 1.2 扩样路径（到 50–200）
- 摄入上述 3–4 个 handbook（Mattermost 多页、GitLab、Basecamp），每个可派生 10–30 条 factual / conflict / acl 题。
- 题型对齐：沿用现有 `external_policy` query_type，或直接并入 `exact_fact / conflict / synonym_abbrev / acl_restricted`。
- 标签写 `label_source = "human-validated-from-public-handbook"`，**不要**写 `human-authored-from-demo-vault`（那是 demo-vault 内部政策的口径）。

### 1.3 实际下载样本（已验证）
```
data-sources/handbooks/mattermost/how-to-get-paid.html   (602 KB, 真实内容)
data-sources/handbooks/gitlab/expenses.html              (2.3 MB, 真实内容)
data-sources/handbooks/basecamp/benefits-and-perks.md    (13.7 KB, 真实内容; 经 api.github.com contents API 获取的 clean markdown)
data-sources/handbooks/basecamp/basecamp-benefits.html   (323 KB, GitHub blob 视图页)
data-sources/handbooks/mattermost/how-to-spend-company-money.md (18.4 KB, 官方 Markdown)
data-sources/handbooks/mattermost/corporate-credit-card-policy.md (3.0 KB, 官方 Markdown)
data-sources/handbooks/gitlab/expenses-additional.html (2.3 MB, 官方 HTML)
data-sources/handbooks/gitlab/all-remote.html (2.2 MB, 官方 HTML)
data-sources/handbooks/basecamp/holidays.md (3.1 KB, GitHub Contents API)
data-sources/handbooks/basecamp/remote-work.md (6.5 KB, GitHub Contents API)
data-sources/handbooks/public-pages-manifest.json (来源、许可证、字节数、SHA-256)
```

---

## 2. 企业 IdP 的 JWK 轮换 + 端到端测试  → 自托管 Keycloak（免费）

**可行性：✅ 可做，需换说法（"自托管 Keycloak" 不是 "客户生产 IdP"）。**

### 2.1 资源
- 镜像：`quay.io/keycloak/keycloak:26.5.6` + `postgres:16`（docker compose，Red Hat 维护，开源）
- 一键 playbook（含 realm / client / JWKS 缓存 / RBAC 网关示例）：
  `https://github.com/quochuydev/keycloak-playbook` → `docker compose up -d`
- 参考部署文档：`https://selfhosting.sh/apps/keycloak`、`https://sysbrix.com/blog/guides-3/keycloak-docker-setup-guide-...`

### 2.2 你要测的"JWK 轮换"到底是什么（诚实 E2E 范围）
- Keycloak 自动维护签名密钥，JWKS 端点：`/realms/<realm>/protocol/openid-connect/certs`，可同时存在多把 key（新旧并存）。
- **实测动作**：
  1. 用 Keycloak Admin API 触发密钥轮换（`/admin/realms/{realm}/keys` 或重启后自动轮转）；
  2. 旧 token 在新 JWKS 下仍应由 `kid` 命中旧 key → 验证**平滑过渡**；
  3. 篡改/剔除对应 key 后，旧 token 应被拒 → 验证**失效签名拒绝**；
  4. 接你 MindGraph 的 `auth.py`：用 python-jose / pyjwt 走 JWKS 拉取 + `kid` 匹配，断言上述两种行为。
- 简历口径："与自托管 Keycloak 完成 OIDC 登录 + JWK 轮换下的令牌校验 E2E（平滑过渡 + 失效拒绝）"。

---

## 3. 扫描 PDF + OCR 引擎执行  → 公开扫描文档 + Tesseract/PaddleOCR

**可行性：✅ 可做。注意区分"带文字层 PDF"（测文本提取）与"纯图片 PDF"（真测 OCR）。**

### 3.1 已下载样本（已验证）
| 文件 | 页数 | 文字层 | 用途 |
|---|---|---|---|
| `data-sources/ocr/chinese-gov/ziran-ziyuan-tingsheng-guiding.pdf`（自然资源听证规定·图片版） | 13 | **有**（354 字/页1） | PDF 文本提取测试 |
| `data-sources/ocr/chinese-gov/guowuyuan-gongbao-202524.pdf`（国务院公报 2025-24） | 61 | **有**（473 字/页1） | PDF 文本提取测试 |

> 上述两份**带文字层**，适合验证"PDF→文本"抽取链路，但**不算真正的 OCR**（没走图像识别）。

### 3.2 真正的纯扫描（image-only）来源
- **Internet Archive 中文古籍**：`https://archive.org/details/cadal`（78,371 册中文书籍 PDF，多为扫描影像）
- **Internet Archive 报纸合集**：`https://archive.org/details/newspapers`（400,000+ 中文项，缩微胶片扫描 → image-only，是理想 OCR 靶标）
- 取一个具体 item 的 `..._pdf.pdf` 或 `..._text.pdf` 下载即可；下法见 `https://www.aeanet.org/how-to-download-a-pdf-from-the-internet-archive`。

### 3.3 OCR 引擎
- Tesseract（Google 维护，100+ 语言）：`https://github.com/tesseract-ocr/tesseract`；中文包 `chi_sim.traineddata`（`https://github.com/tesseract-ocr/tessdata`）
  ```bash
  # 例：对纯扫描 PDF 逐页渲染后 OCR
  pip install pytesseract pdf2image pillow
  tesseract scan.png out -l chi_sim --psm 6
  ```
- PaddleOCR（产业级，中英文 PP-OCRv3）：`pip install paddleocr`，适合做对照基准。

### 3.4 诚实口径
- "在公开政府公报与 Internet Archive 扫描古籍上实测 Tesseract/PaddleOCR 抽取与版面还原" ✅
- "扫描了公司合同/发票做 OCR" ❌（你没有）

---

## 4. 生产连接器历史 notes ownership 迁移  → 脚本 + 合成数据（诚实 Partial）

**可行性：⚠️ 只能做一半，本质依赖"有生产"。诚实做法是脚本 + 合成验证。**

- 写迁移脚本：扫描 `notes` 表，按 `created_by` / `source_system` 映射新 `owner_id`，**全部 `UPDATE ... WHERE id IN (...)` 批量、零删除**，先 `SELECT` dry-run 输出 diff 再执行。
- 用**合成生产级数据集**（随机生成 N=1k–10k 条带旧 owner 的 notes）验证：归属映射正确率 100%、零行丢失、可回滚。
- 简历/面试口径："实现 ownership 迁移脚本，并在合成 N 条数据集上验证映射正确、零删除、可回滚；**尚未在真实生产连接器运行**"。
- 这一项**保持 `Partial`** 是正确且诚实的，不要伪标 Done。

---

## 5. 生产并发 / 取消 / 部署级构建  → Railway + k6（真实 PaaS）

**可行性：✅ 完全可做，用你自己的部署实例。**

### 5.1 资源
- 部署：Railway（`https://railway.app`）免费额度；`https://docs.railway.app/guides/load-test-k6` 官方 k6 指南
- k6：`grafana/k6`（Go 单文件，CI 友好），教程 `https://oneuptime.com/blog/post/2026-02-02-k6-load-testing/view`
- 流式取消验证：对你 `/api/chat` 的 SSE/stream 端点发请求，中途断开，断言服务端 `finally` 释放资源、不泄漏。

### 5.2 实测内容
```javascript
// script.js — 例：阶梯加压 + 流式取消
export const options = {
  stages: [
    { duration: '1m', target: 50 },
    { duration: '3m', target: 50 },
    { duration: '1m', target: 100 },
    { duration: '3m', target: 100 },
    { duration: '1m', target: 0 },
  ],
  thresholds: {
    http_req_duration: ['p(95)<800'],
    http_req_failed: ['rate<0.01'],
  },
};
```
- 把 k6 作为 Railway service（`restart policy: Never`）跑一次，看 Metrics 标签的 CPU/内存是否平顶。
- 简历口径："部署至 Railway 并以 k6 压测本人实例（阶梯至 100 VU），验证流式取消与 Docker 构建；p95<800ms、错误率<1%"。

---

## 6. Graph ON/OFF 真实质量消融  → 用本项目 46 条 golden 集跑真实差值

**可行性：✅ 完全可做，零外部数据依赖。**

- 你现在的 `mindgraph_golden_v2.jsonl` 已含 **16 条图类题**（8 `graph_needed` + 8 `graph_control`），且 🔴/🟡 图管线 bug 已修。
- 跑法：同一检索管线，分别 `graph_enabled=True/False`，对 46 条算 `recall@5` / 引用正确率 / 拒答准确率，记录差值。
- 输出的是**真实跑出来的数**，直接进 `docs/upgrade/` 消融报告。
- 这一步不需要任何公开/公司数据，是 6 项里最容易出真实成果的一项。

---

## 7. 已实际下载的文件清单（data-sources/）

```
data-sources/
├── handbooks/
│   ├── mattermost/how-to-get-paid.html   ✅ 602KB 真实（解锁 external_policy 4 条候选，已 approved）
│   ├── gitlab/expenses.html              ✅ 2.3MB 真实
│   └── basecamp/
│       ├── benefits-and-perks.md         ✅ 13.7KB 真实（经 api.github.com contents API 获取的 clean markdown）
│       └── basecamp-benefits.html        ✅ 323KB 真实（GitHub blob 视图页）
└── ocr/chinese-gov/
    ├── ziran-ziyuan-tingsheng-guiding.pdf ✅ 13页 带文字层（PDF文本提取测试）
    └── guowuyuan-gongbao-202524.pdf       ✅ 61页 带文字层（PDF文本提取测试）
```

> 说明：`data-sources/` 是 staging 区，**未并入 `demo-vault/` 活动语料**。要正式摄入 handbook 扩充评测集时，再按 §1.2 流程导入并翻 `approved`。是否提交该目录由你定（建议在 `.gitignore` 加 `data-sources/` 或仅保留清单不提交大 PDF）。

---

## 8. 建议执行顺序（把 6 项变"5 Done + 1 Partial"）

1. **⑥ Graph 消融**（最快出真实数，零依赖）→ 写 `docs/upgrade/GRAPH_ABLATION_*.md`
2. **① 扩 golden 到 50–200**：摄入 `data-sources/handbooks/` + 更多 handbook，人工标注，翻 approved
3. **② Keycloak JWK E2E**：`docker compose up` → 跑轮换 + 失效拒绝测试
4. **③ OCR**：下载 1 个 IA 纯扫描件 → Tesseract/PaddleOCR 实测
5. **⑤ Railway + k6**：部署 + 压测 + 流式取消验证
6. **④ 连接器迁移**：写脚本 + 合成数据验证（诚实留 Partial）

---

## 9. 诚信标注速查（面试扛深挖）

- "基于公开企业 handbook 构建评测集" ✅
- "与自托管 Keycloak 完成 OIDC + JWK 轮换 E2E" ✅
- "OCR 在公开扫描文档上实测" ✅
- "k6 压测部署在 Railway 的本人实例" ✅
- "迁移脚本在合成数据集验证、零删除、可回滚" ✅（但注明未跑真实生产）
- 任何暗示"真实公司/生产/客户数据"的表述 ❌
