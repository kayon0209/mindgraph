# 基础设施层约定（`src/infrastructure/`）

适配层：为领域接口提供具体实现，包括 SQLite、文件解析、检索索引与模型 SDK 接入。

## 职责

- 实现领域接口：持久化（SQLite）、文档解析、检索后端、模型/Provider SDK
- 封装外部依赖的具体细节，隔离变化点

## 必须遵守的边界

- 可以导入 `domain` 与 `retrieval` 包
- API 与 UI 模块**不得**绕过应用服务直接使用本层

## 子模块

- `parsers/`：各文件格式的 `DocumentParser` 适配器（见该目录 README）

## 相关

- 领域接口：`src/domain/`
- 检索实现：`src/retrieval/`
- 部署与配置：`docs/DEPLOYMENT.md`
