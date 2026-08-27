# UI 层约定（`src/ui/`）

Streamlit 客户端：面向用户的 Web 交互界面，通过唯一后端入口与 API 通信。

## 职责

- 提供问答、证据检查、关系审核与评测结果比较等交互页面
- 通过 `api_client.py` 调用后端 API

## 必须遵守的边界

- `api_client.py` 是 UI 唯一的后端集成点
- Streamlit 页面**不得**导入应用服务、检索实现、模型 SDK、SQLite 或评测内部实现

## 相关

- 后端 API：`src/api/`
- 完整 Web 工作台（Docker）：`web/`，启动方式见根 `README.md`「路径 B」
