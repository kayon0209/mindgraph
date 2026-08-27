# API 层约定（`src/api/`）

FastAPI 路由与 Web 接入层：HTTP 入口、认证（API Key / OIDC）、中间件、异常处理与 MCP Server 挂载。

## 职责

- 校验并解析 HTTP 输入，调用应用服务（application services）完成业务逻辑
- 集中处理错误并统一脱敏，避免内部异常泄漏给客户端
- 挂载认证、限流、安全头等中间件，以及只读 MCP Server

## 必须遵守的边界

- 路由**不得**直接访问 SQLite、文件、模型 SDK 或检索器——一律通过应用服务访问
- API 错误通过集中式 handler 脱敏后返回

## 相关

- 服务编排与依赖容器：`src/application/`
- 启动方式与端口：`docs/DEPLOYMENT.md`
- 认证与权限模型：`docs/MCP_PERMISSION_MATRIX.md`
