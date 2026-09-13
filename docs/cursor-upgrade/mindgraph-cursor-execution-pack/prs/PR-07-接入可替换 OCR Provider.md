# PR-07｜接入可替换 OCR Provider

阶段：M1  
依赖：PR-06

## ⚠️ 现场核对修正（2026-09-11 实测）

**修正 1：靶标已经有了，而且就是为这个 PR 准备的。**
`data-sources/ocr/chinese-gov/`（**已提交进 git**）内含：

| 资产 | 说明 |
|---|---|
| `rendered/guowuyuan-gongbao-202524_p2.png` / `_p3.png` | ⭐ **真·纯图像页**（源页文字层 = 0，1654×2339）——最优靶标 |
| `rendered/*_p1.png` 等 4 张 | 带文字层页的渲染图（次选，用来对照） |
| `OCR_TARGETS.md` | 靶标清单与跑法（含诚信标注口径） |
| `ocr_verify_rapidocr.py` | **rapidocr 探针，已写好可直接跑** |
| `ocr_verify.py` | Tesseract 探针 |
| `render_ocr_targets.py` | PyMuPDF 200dpi 页图渲染 |

→ 不要新造 fixture，直接用这套。

**修正 2：OCR 引擎当前一个都没装。** `.venv` 实测：

```
missing  : pytesseract  paddleocr  rapidocr_onnxruntime  fitz(PyMuPDF)
installed: PIL
pyproject.toml:46 → ocr = ["paddleocr>=2.9.0", "paddlepaddle>=3.0.0"]   ← optional extra，未安装
```

**建议路径：`rapidocr-onnxruntime`。** 理由：纯 pip、无外部二进制、跨平台，与本项目
「Local Profile 零外部依赖」的定位一致；且 `ocr_verify_rapidocr.py` 已写好可直接作为探针。
Tesseract 方案需装系统二进制 + `chi_sim` 语言包，作为次选。
`pyproject.toml` 已声明的 `paddleocr` extra 本 PR 可以不启用——**若决定改用 rapidocr，要同步更新该 extra 并说明取舍**。

**修正 3：`ocr_required_pages` 的产出侧已存在。** `src/infrastructure/parsers/pdf.py:35, 45-46, 93`
已经在收集 `ocr_pages` 并写入 `ocr_required_pages`。本 PR 是**接消费侧**（把标记页送 OCR），
不是从零加标记。现场核对时先确认这条链路的真实缺口。

## 目标

扫描页通过显式 OCR Provider 处理，低质量或失败结果不能进入 active index。

## 真实业务失败

当前 PDFParser 只标记 ocr_required_pages，扫描 PDF 无法进入可检索状态。

## 必须先阅读

- `src/infrastructure/parsers/pdf.py`
- `src/application/document_lifecycle_service.py`
- `src/infrastructure/settings.py`
- `pyproject.toml`
- `tests/test_document_intelligence.py`

## 范围内

- 定义 OCRProvider Protocol 与本地实现。
- 按页调用，记录 model/version/confidence/latency。
- 支持 timeout、retry、cancel。
- 低置信输出进入 needs_review 或 parse_failed。

## 范围外

- 不默认依赖云 OCR。
- 不把 OCR 文本当成已验证事实。
- 不自动激活低置信页面。

## 实施顺序

1. 获取最新 main SHA、git status 和现有基线。
2. 复现业务失败或证明当前缺口。
3. 先补失败测试与契约测试。
4. 实现最小兼容改动。
5. 运行目标测试、相关回归与必要评测。
6. 输出新旧差异、已知风险和回滚步骤。

## 测试矩阵

- 扫描 PDF 成功。
- 超时/空结果/乱码/部分页失败。
- 其他页缓存复用。
- 敏感日志不含全文。

## 验收标准

- 扫描 fixture 可检索且页码可追溯。
- 失败页不污染索引。
- OCR 关闭时旧行为保持。

## 回滚

关闭 OCR flag；保留页级诊断，索引回切旧版本。

## Cursor 可复制指令

```text
你只执行 PR-07「接入可替换 OCR Provider」，不得顺手做后续任务。

先读取 01-GLOBAL-GUARDRAILS.md、本任务书和“必须先阅读”中的仓库文件。先输出现场核对报告和最小改动计划，不要立即写代码。确认仓库现状与任务书一致后，先补失败测试，再做最小实现。完成后运行目标测试、相关回归和必要评测，报告真实输出、行为/指标差异、风险与回滚步骤。若发现任务前提不成立或需要破坏现有契约，停止实现并提交差异报告。
```
