# 路线图外部验收依赖：公开数据可行性分析（2026-08-27）

> 背景：Cursor 完成本地可自动验收项后，路线图（`docs/UPGRADE_PLAN.md`）将 8 个 UG 项全部维持 `Partial`，
> 剩余"仍待"项对应 6 个外部验收依赖。本文核验 Cursor 实际改动，并分析这 6 项能否用**公开数据/免费服务**替代。
> 结论面向面试诚信边界：可复现的"真实结果" ≠ "真实公司数据"。

## 0. 再审查：Cursor 的"本地已完成"已逐项核验（属实）

- 🔴 图管线 fail-safe **已修**：`src/retrieval/mindgraph_pipeline.py:59-63` `_expand_graph` 包 `try/except`，
  图服务异常 → 记 warning + 清空 `graph_links` + 安全回退 Hybrid，不再整条检索崩溃。
- 🟡 关系生命周期过滤 **已修**：`src/application/mindgraph_graph_store.py:194-198` 按
  `relation_effective_from/to` 与 `target_date` 比较，过期/旧版本关系不再进入检索。
- Golden 12 → 46：**34 条晋升全部 schema 校验通过**；16 条图类题 0 个 mattermost/外部泄漏
  （13 条 `policies/`、3 条 `graph-control` 指向 `workflows/` 控制类 fixture）；答案型均有源、`graph_needed` 型均有 `expected_relations`。
- 全量测试 **282 passed / 2 skipped**（1 个 ERROR 为预存 `conftest.py:33` 环境变量超长，与改动无关）。无回归。
- 8 个 UG 维持 `Partial` 正确：本地可验收部分确实完成，6 个外部依赖确为外部验收性质，不能假标 Done。

## 1. 六项外部依赖 · 公开数据可行性

| # | 外部依赖（对应 UG） | 公开/免费能否真测 | 诚实做法 | 结论 |
|---|---|---|---|---|
| 1 | 真实 50–200 条脱敏业务问题集（UG-007） | ✅ 能 | 用公开企业 handbook（Mattermost/GitLab/Basecamp 等）+ demo-vault 自造题、人工校验。标签"基于公开 handbook 构建"，**勿**写"脱敏自真实企业工单" | 完全可行 |
| 2 | 真实企业 IdP 的 JWK 轮换 + E2E（UG-006） | ✅ 能（换说法） | 自托管 Keycloak（Docker 免费）或 Auth0/Okta 开发者档——有真实 JWK 轮换端点 + 真实登录流。标签"自托管 Keycloak/测试 IdP"，**勿**写"客户生产 Azure AD" | 可行，需换说法 |
| 3 | 真实扫描 PDF + OCR 引擎执行（UG-001） | ✅ 能 | 公开扫描文档（arXiv / 互联网档案馆 / 政府 FOIA / 公开年报）+ Tesseract/PaddleOCR，真跑 OCR | 完全可行 |
| 4 | 生产连接器历史 notes ownership 迁移（UG-004） | ⚠️ 只能做一半 | 写迁移脚本 + **合成生产级数据集**验证（零删除、归属正确）。**不能**声称"迁移了真实生产数据"——无生产环境 | 诚实地保持 Partial |
| 5 | 生产并发/取消/部署级构建验收（UG-005 + 部署） | ✅ 能 | 部署 Railway/Fly.io（真实 PaaS），k6 压自己实例、验证流式取消 + Docker 构建 | 完全可行 |
| 6 | Graph ON/OFF 真实质量消融（UG-002） | ✅ 能 | 在 46 条集（16 图类 + 已修管线）上跑 ON/OFF，量 Recall@5 / 引用正确率差，出真实跑出来的数 | 完全可行 |

**汇总**：6 项中 **5 项可用公开数据 + 免费/自托管服务真正测完并产出真实结果**；
仅第 4 项（生产连接器迁移）本质依赖"有生产"，可用"脚本 + 合成数据验证"诚实达成，标签写
"已实现并在合成 N 条数据集验证，尚未在真实生产连接器运行"——这是最诚实的简历写法。

## 2. 面试诚信红线（不可逾越）

- ✅ "基于公开企业 handbook 构建评测集" ／ ❌ "脱敏自真实企业数据"（你无真实企业数据）
- ✅ "与自托管 Keycloak 完成 OIDC + JWK 轮换 E2E" ／ ❌ "接入客户生产 IdP"
- ✅ "迁移脚本在合成生产级数据集验证、零删除" ／ ❌ "完成生产连接器数据迁移"
- 六项里的"真实" = **真实跑出来的可复现结果**，不是"真实公司数据"。公开语料 + 自有部署足够产出真实结果，且经得起深挖。

## 3. 建议推进顺序（把 6 项变成"5 Done + 1 诚实 Partial"）

1. golden 推到 50–200：摄入 Mattermost/GitLab/Basecamp 公开 handbook（external_policy 管线已就位，三个 handbook 已入库），人工标注。
2. Docker 起 Keycloak，接 OIDC，跑 JWK 轮换 + 失效签名拒绝 E2E。
3. 加 OCR 步骤（Tesseract），用公开扫描 PDF 跑 layout/表格保真。
4. 写连接器 ownership 迁移脚本，合成数据验证。
5. 部署 Railway，k6 压测 + 流式取消验证。
6. 跑 Graph ON/OFF 消融，记真实数字。

最终版图：8 UG 全 Partial → 本地全绿 + 5 个外部依赖实测通过 + 仅"生产迁移"诚实留 Partial。
