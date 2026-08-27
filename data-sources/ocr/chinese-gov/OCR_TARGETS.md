# 纯扫描 OCR 靶标（本地渲染，零外部依赖）

**用途**：验证 MindGraph 文档 OCR 解析 route 对「纯图像中文文档」的识别能力。

**重要**：这些 PNG 是**像素图（无文字层）**，OCR 引擎必须走图像识别，不能靠 PDF 文本提取——这正是要验证的能力点。

## 来源
- `ziran-ziyuan-tingsheng-guiding.pdf`（13 页，带文字层）
- `guowuyuan-gongbao-202524.pdf`（61 页，其中 p2/p3 为纯图像页）
- 渲染工具：`render_ocr_targets.py`（PyMuPDF，200dpi）

## 靶标清单（`rendered/`）

| 文件 | 尺寸 | 性质 | 作为靶标 |
|------|------|------|----------|
| `guowuyuan-gongbao-202524_p2.png` | 1654×2339 | **真·纯图像**（源页文字层=0） | ⭐ 最优 |
| `guowuyuan-gongbao-202524_p3.png` | 1654×2339 | **真·纯图像**（源页文字层=0） | ⭐ 最优 |
| `guowuyuan-gongbao-202524_p1.png` | 1654×2339 | 带文字层页渲染图 | 次选 |
| `ziran-ziyuan-tingsheng-guiding_p1.png` | 1700×2200 | 带文字层页渲染图 | 次选 |
| `ziran-ziyuan-tingsheng-guiding_p2.png` | 1700×2200 | 带文字层页渲染图 | 次选 |
| `ziran-ziyuan-tingsheng-guiding_p3.png` | 1700×2200 | 带文字层页渲染图 | 次选 |

## 给 Cursor 的跑法
1. 装 Tesseract（含中文包 `chi_sim`）：UB-Mannheim installer，安装时勾选 **Additional language data → Chinese (Simplified)**。
   - 或沙箱内拉便携版 `tesseract.exe` 加入 PATH。
2. `pip install pytesseract pillow`
3. 运行 `ocr_verify.py`（已写好，对 `rendered/` 全部 PNG 跑 `image_to_string(lang='chi_sim')`）。
   - 若 tesseract 不在 PATH，取消 `ocr_verify.py` 中 `tesseract_cmd` 那行注释并指向实际路径。
4. **预期**：纯图像 `p2/p3` 应能识别出国务院公报正文中文；输出文本非空即证明 OCR 管线通。

## 诚信标注（上简历/文档时）
- 可称：「使用公开政府公报 PDF 渲染的纯图像页作为 OCR 靶标，经 Tesseract(chi_sim) 验证可识别」。
- 不可称：「脱敏自真实企业扫描件 / 生产文档」。
