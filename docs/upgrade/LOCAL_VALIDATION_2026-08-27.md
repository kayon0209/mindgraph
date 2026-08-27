# 本地可解验证报告 — 2026-08-27（5 项全解锁）

> 针对 Cursor EXECUTION_STATUS 中"环境限制阻塞"的 5 项逐一本地解锁。
> 原则：**全部零生产依赖、零外部二进制（除 pip 包），结果可复现，口径诚实。**

## 结果总览

| # | 验证项 | 原状态(Cursor) | 本报告结果 | 工具/方法 |
|---|--------|----------------|------------|-----------|
| 1 | Keycloak JWK 轮换 E2E | 阻塞：Docker 引擎未运行 | ✅ **5/5 PASS** | 合成验证：本地假 IdP + MindGraph 真实 `oidc.validate_id_token`（零 Docker） |
| 2 | 纯图片 OCR | 阻塞：无 tesseract/pytesseract | ✅ **6/6 识别成功**，真纯图像页 605 字符 | rapidocr_onnxruntime（纯 pip，替代 Tesseract） |
| 3 | Basecamp Golden 案例 | 建议：4 条待人工审核 | ✅ 4 条候选已入库（pending） | 对照 `benefits-and-perks.md` fact-check 起草 |
| 4 | k6 压测 | 阻塞：无 k6 可执行文件、无部署 URL | ✅ **50 并发 30s，451 请求 0 失败**（~17 req/s） | locust 替代 k6（k6 二进制下载被网络策略拦截，如实标注）；压测目标=本地服务，无需部署 URL |
| 5 | 中文→英文跨语言召回 | 信号：外部子集 3/4 失败（Recall@5=0.25） | ✅ 实验量化：**英文翻译变体 Recall@5 0.25→1.0，MRR 0.25→0.875** | 复用混合检索管线跑中文 vs 英文变体 |

## 各验证详情

### 1. 合成 JWK 轮换 E2E（scripts/synthetic_jwk_rotation_e2e.py）
- 本地 `http.server` 假 IdP：`/.well-known/openid-configuration` + `/jwks`
- RSA-2048 密钥对 A → 签发 token → `oidc.validate_id_token` 通过
- 轮换到密钥对 B（JWKS 只含 B）→ 强制刷新缓存后：旧 token(A) **失效**、新 token(B) **通过**
- 同时记录 OIDC 缓存语义：未刷新缓存时旧 key 在 TTL 内仍可验证（标准过渡行为）
- 依赖：`cryptography`（pip，PyJWT RS256 必需，原 venv 缺失已补）
- 诚信口径：**合成数据验证**（非真实企业 IdP / 非生产环境）

### 2. 纯图片 OCR（data-sources/ocr/chinese-gov/ocr_verify_rapidocr.py）
- rapidocr_onnxruntime（纯 pip，内置 onnx 模型，无外部二进制）
- 靶标 `rendered/` 6 张 PNG 全部识别出中文；**真·纯图像页** `guowuyuan-gongbao-202524_p2.png`（605 字符，公报目录：第 814 号国务院令、外资管理条例修改等）、`p3.png`（65 字符）
- `ziran-ziyuan-tingsheng-guiding_p1-3.png` 363-372 字符（听证规定条文，2003 国土部令 22 号）
- 诚信口径：公开政府公报渲染图像 + rapidocr 验证，非真实企业扫描件

### 3. Basecamp Golden 候选（scripts/add_basecamp_candidates.py）
- 4 条候选已入 `evaluation/datasets/mindgraph_candidates_v2.jsonl`（42→46 行），`validation_status=pending`
- 全部对照 `benefits-and-perks.md` 原文 fact-check：医疗 75%/25%、Airbase $75 收据、401K 100% 匹配上限 6%、育儿假 16 周 100% 薪资
- schema 与已 approved 的 Mattermost 4 条完全一致（`external_policy` / `human-validated-from-data-source`）
- **approved 需用户人工审核**（诚信流程，与 Mattermost 一致）；备份 `.bak-basecamp` 已留

### 4. 本地负载压测（scripts/locustfile.py，locust 替代 k6）
- 目标：本地 MindGraph 服务（`uvicorn api.main:app` 单 worker，端口 8123）
- 结果（50 VU / 30s）：451 请求、**0 失败**、16.84 req/s
  - `/api/v1/health`：109 req，4.07 req/s
  - `/api/v1/config/public`：342 req，12.77 req/s
  - 延迟：avg 2494ms / p50 2700ms / p95 3100ms / p99 3300ms
- 发现：单 worker + 同步端点线程池下 50 并发 p95≈3.1s——真实瓶颈信号；生产承载需多 worker（`--workers N`）或异步端点。**未做任何性能调优声明**
- 工具替代说明：k6 v2.2.0 二进制下载（objects.githubusercontent.com）被网络策略重置，gh 通道同失败；改用纯 Python locust 完成同等验证，压测目标为本地服务（不依赖部署 URL）

### 5. 跨语言召回探针（scripts/cross_language_probe.py）
- 中文基线复现：Recall@5=0.25（与 Cursor 报告一致）
- 英文翻译变体：**Recall@5=1.0 / MRR=0.875**（4/4 命中）
- 根因：`BAAI/bge-small-zh-v1.5` 中文嵌入对英文文档编码失效 + BM25 无法跨语言词法匹配；翻译成英文后双分支均可命中
- 决策依据：若将外部 corpus 纳入质量门禁，**需引入查询翻译/双语 query 扩展**；短期可"接受+如实标注跨语言不承诺"

## 复现命令

```bash
# 1. JWK 合成 E2E（venv 需 cryptography）
.venv/Scripts/python.exe scripts/synthetic_jwk_rotation_e2e.py

# 2. OCR（managed env 需 rapidocr_onnxruntime）
"…/envs/default/Scripts/python.exe" data-sources/ocr/chinese-gov/ocr_verify_rapidocr.py

# 3. Basecamp 候选（已执行；重跑会去重）
.venv/Scripts/python.exe scripts/add_basecamp_candidates.py

# 4. 压测（managed env 需 locust；服务先起）
PYTHONPATH=src .venv/Scripts/python.exe -m uvicorn api.main:app --host 127.0.0.1 --port 8123
"…/envs/default/Scripts/locust.exe" -f scripts/locustfile.py --headless -u 50 -r 10 -t 30s --host http://127.0.0.1:8123 --only-summary

# 5. 跨语言探针
.venv/Scripts/python.exe scripts/cross_language_probe.py
```

## 追加（同日 16:20 用户批准后执行）

### 6. Basecamp 4 条 approved 进 golden（scripts/promote_basecamp_approved.py）
- golden 50→54、candidates 46→42；`validation_status=approved`（用户人工审核通过，与 Mattermost 流程一致）
- 测试断言 50→54 已更新；数据集测试 41 passed；全量 **282 passed / 2 skipped**（零回归）

### 7. 跨语言查询翻译 v2（检索层 `query_variants` + RRF 融合）
- 实现：`src/retrieval/mindgraph_pipeline.py` `retrieve()` 新增 `query_variants` 参数；`_rrf_merge_variants` 按 `1/(k+rank)` 跨变体 RRF 融合（跨语言场景 score 不可比、rank 可比；与 chat 层同语言 max-score 合并互补）。单变体路径零回归。
- 数据集：8 条 external golden 加 `query_translations`（人工英译，`scripts/add_query_translations.py`，provenance 记入 notes）
- 验证（`scripts/run_external_variant_eval.py`，8 条 external）：
  - 中文基线：**Recall@5=0.625 / MRR=0.4167**（mattermost 3 条失败；basecamp 因含数字/百分比 token 全命中）
  - 变体融合：**Recall@5=1.0 / MRR=0.9375**（8/8 全命中）
- 诚信口径：翻译为**人工提供**（human-translated，标注于 notes），非 LLM 自动翻译

### 8. 真 Keycloak JWK 轮换 E2E（✅ 完成，ALL PASS）
- `scripts/keycloak_jwk_rotation_e2e.py`：docker compose 起 `quay.io/keycloak/keycloak:26.5.6` → admin API 建 realm+client → client credentials 签真实 token → MindGraph `oidc.validate_id_token` 通过
- 轮换：components API 新建 priority 200 的 `rsa-generated` provider（**kid 真实切换** `hft1qn0w…` → `9MdfBs5u…`）→ 删除旧 provider → 强制刷新 JWKS → **旧 token 失效、新 token 有效**
- **4/4 PASS**；容器已清理（镜像保留，复现：`docker compose -f docker-compose.keycloak.yml up -d` 后跑脚本）
- 修正记录：`POST /admin/realms/{realm}/keys/rotate` 在 Keycloak 26.5.6 返回 405（无此 REST 端点），正确路径是 **components API 增删 key provider**（`POST /admin/realms/{realm}/components` + `DELETE .../components/{id}`）

## 未决项（需用户动作）
1. **压测延迟**：p95 3.1s 为单 worker 本地数据；生产建议多 worker，非本次范围
2. **git 提交**：所有改动（含 Cursor 的）仍未提交；`.bak` 与大 PDF 在未跟踪列表，提交前建议加 `.gitignore`
