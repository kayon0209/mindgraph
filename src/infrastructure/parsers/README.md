# 解析器适配约定（`src/infrastructure/parsers/`）

文档解析适配层：把不同格式的文件转换为统一的结构化元素序列，供上层索引与检索使用。

## 职责

- 每种文件格式对应一个 `DocumentParser` 适配器
- 返回有序的结构化元素、警告信息与需要 OCR 的页面列表

## 必须遵守的边界

- 解析器**不得**直接创建检索 chunk——chunk 化由上层统一完成
- 解析器选择统一收敛在 `ParserRegistry`，避免各处散落分支逻辑

## 相关

- 适配层约定：`src/infrastructure/`
- 文档生命周期与入库流程：`src/application/`
