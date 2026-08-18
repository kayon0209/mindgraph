# MindGraph 部署指南

本指南面向实施/运维人员，覆盖：**本地试用 → 企业部署 → 功能启用 → 故障排查**。
配套配置文件 `.env.example`，复制为 `.env` 后按需修改。

---

## 1. 快速开始（本地试用，5 分钟）

### 1.1 前置依赖

- Python ≥ 3.11
- Node.js ≥ 18（Web 前端，可选）

### 1.2 启动步骤

```bash
# 1) 安装依赖
pip install -e ".[dev]"

# 2) 复制配置（demo 模式，无需密钥即可启动）
cp .env.example .env

# 3) 启动 API
python -m uvicorn api.main:app --reload

# 4)（可选）启动 Web 前端
cd web
npm install
npm run dev
```

打开 `http://localhost:5173`，提问并看到流式答案 + 来源 + 版本即算成功。

> demo 模式（`AUTH_MODE=demo`）仅用于本地试用，**不要用于生产**。

---

## 2. 环境变量清单

### 2.1 运行环境

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `ENVIRONMENT` | `development` | `development` / `staging` / `production` |
| `DEBUG` | `false` | 开启调试日志 |
| `LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` / `CRITICAL` |
| `LOG_FORMAT` | `console` | 生产建议 `json` |

### 2.2 服务

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `API_HOST` | `0.0.0.0` | API 监听地址 |
| `API_PORT` | `8000` | API 端口 |
| `CORS_ORIGINS` | `http://localhost:5173,app://obsidian.md` | 逗号分隔的允许来源 |

### 2.3 认证模式

| `AUTH_MODE` 值 | 行为 | 适用场景 |
|----------------|------|----------|
| `off` | 关闭认证，所有人匿名读写 | **仅限内网调试** |
| `api_key` | `X-API-Key` Header 鉴权 | 小团队 / 内部工具 |
| `bearer` | `Authorization: Bearer` 鉴权（接 OIDC 或 API Key） | **企业生产推荐** |
| `demo` | 简单会话，无真实鉴权 | 本地试用 |

切换到 `bearer` 或 `api_key` 后，必须在 `data/api_keys.json` 配置密钥与 ACL（见 §3.1）。

### 2.4 SSO / OIDC（Phase 5-4）

启用后 `Authorization: Bearer <JWT>` 优先走 OIDC；未启用或缺失时回退 API Key。

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `OIDC_ENABLED` | `false` | 是否启用 OIDC Bearer 校验 |
| `OIDC_ISSUER_URL` | 空 | IdP 签发方，如 `https://login.microsoftonline.com/{tenant}/v2.0` |
| `OIDC_CLIENT_ID` | 空 | MindGraph 在 IdP 注册的 client_id |
| `OIDC_CLIENT_SECRET` | 空 | 客户端密钥（RS256 公钥模式可留空） |
| `OIDC_AUDIENCE` | 空 | 校验 `aud` 声明；为空时回退到 `OIDC_CLIENT_ID` |
| `OIDC_ALGORITHMS` | `RS256` | 逗号分隔，如 `RS256,HS256` |
| `OIDC_JWKS_CACHE_TTL_SECONDS` | `600` | JWKS 公钥缓存时长 |
| `OIDC_ROLES_CLAIM` | `roles` | JWT 中角色数组的字段名 |
| `OIDC_WORKSPACES_CLAIM` | `workspaces` | JWT 中 workspace 列表的字段名 |
| `OIDC_DEPARTMENTS_CLAIM` | `departments` | JWT 中部门列表的字段名 |
| `OIDC_USERNAME_CLAIM` | `preferred_username` | JWT 中显示名的字段名 |

**claims → ACL 映射规则**：IdP 返回的 `workspaces` / `departments` 会自动转换成 `workspace:{值}` / `department:{值}` 加入主体的 `allow` 列表，检索与台账自动按此裁剪。

### 2.5 LLM Provider

| Provider | 关键变量 | 申请地址 |
|----------|----------|----------|
| DeepSeek（默认） | `OPENAI_COMPAT_API_KEY` | https://platform.deepseek.com/ |
| 智谱 | `ZHIPU_API_KEY` | https://open.bigmodel.cn/ |
| Anthropic | `ANTHROPIC_API_KEY` | https://console.anthropic.com/ |

通过 `CHAT_PROVIDER=deepseek|zhipu|anthropic` 切换。启动时会自动校验必填项，缺失会在日志中告警。

### 2.6 检索

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `RETRIEVAL_CANDIDATE_COUNT` | `20` | 候选池大小 |
| `RETRIEVAL_FINAL_TOP_K` | `5` | 最终返回片段数 |
| `BM25_K1` / `BM25_B` | `1.5` / `0.75` | BM25 参数 |
| `RRF_CONSTANT` | `60` | RRF 融合常数 |
| `RERANKER_ENABLED` | `false` | 是否启用 CrossEncoder 重排 |
| `BGE_LOCAL_FILES_ONLY` | `true` | 离线模式，不联网下载模型 |

### 2.7 数据库与备份

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `DATABASE_PATH` | `data/product/product.sqlite3` | SQLite 路径（自动创建） |
| `SQLITE_JOURNAL_MODE` | `WAL` | 日志模式 |
| `BACKUP_ENABLED` | `true` | 是否定时备份 |
| `BACKUP_INTERVAL_HOURS` | `24` | 备份间隔 |
| `BACKUP_RETENTION_DAYS` | `30` | 备份保留天数 |

---

## 3. 功能启用

### 3.1 ACL 权限过滤（企业部署核心）

MindGraph 的权限模型基于 **workspace + department + ACL 标签**，在检索前裁剪，而非生成后过滤。

#### 3.1.1 通过 API Key 配置（`data/api_keys.json`）

```json
{
  "keys": {
    "finance-user-key": {
      "name": "finance_user",
      "roles": ["read"],
      "workspaces": ["corp"],
      "departments": ["finance"],
      "enabled": true
    },
    "admin-key": {
      "name": "admin",
      "roles": ["admin"],
      "enabled": true
    }
  }
}
```

- `roles` 含 `admin` / `owner` / `superuser` → 获得 `*` 通配权限（可见全部）；
- `workspaces` / `departments` → 自动转成 `workspace:{值}` / `department:{值}` 加入 `allow`。

#### 3.1.2 通过笔记 Frontmatter 配置

每篇 Markdown 的 frontmatter 可声明该笔记的归属与可见性：

```yaml
---
workspace: corp
department: finance
acl:
  allow:
    - workspace:corp
    - department:finance
  deny:
    - department:hr
acl_public: false   # true 表示全员可见（覆盖 ACL）
---
```

#### 3.1.3 通过目录级 `acl.json` 配置（连接器同步时继承）

在同步源根目录或子目录放 `acl.json`，子树自动继承：

```json
{
  "workspaces": {
    "corp": { "acl": { "allow": ["workspace:corp"] } }
  }
}
```

#### 3.1.4 验证

```bash
# 回归测试（越权访问必须 0）
pytest tests/test_access_control.py -v
```

审计记录写入 `access_audit` 表，可查：

```sql
SELECT actor, action, resource, decision, reason, created_at
FROM access_audit
ORDER BY created_at DESC LIMIT 50;
```

---

### 3.2 目录连接器（本地 Markdown 增量同步）

#### 3.2.1 调用同步 API

```bash
POST /api/v1/connectors/directories
Content-Type: application/json
X-API-Key: <write-key>

{
  "source_path": "/data/knowledge/corp",
  "workspace": "corp",
  "department": "finance",
  "acl_public": false,
  "trigger_index": true
}
```

#### 3.2.2 目录结构约定

```
/data/knowledge/corp/
├── acl.json                 # 根 ACL（可选）
├── finance/
│   ├── expense.md           # frontmatter 优先；缺失则继承 finance 作为 department
│   └── budget.md
└── hr/
    └── leave.md
```

- **workspace**：frontmatter > 请求参数 `workspace` > 目录第一级；
- **department**：frontmatter > 请求参数 `department` > 目录第一级；
- **ACL**：frontmatter `acl` > 目录 `acl.json` > 请求参数 `acl_json`。

#### 3.2.3 增量同步行为

| 场景 | 行为 |
|------|------|
| 新增文件 | 写入 `notes`，`index_status=pending` |
| 修改文件 | content_hash 变更 → `index_status=pending` |
| 删除文件 | 物理剪枝（notes + note_relations），需 `force` 重建索引排除 |
| 内容未变 | 保留原 `index_status`，不重复处理 |

同步结果写入 `connector_syncs` 表：

```sql
SELECT connector_id, status, file_count, added, updated, pruned, started_at, finished_at
FROM connector_syncs ORDER BY started_at DESC;
```

#### 3.2.4 验证

```bash
pytest tests/test_directory_connector.py -v
```

---

### 3.3 MCP 只读工具

MindGraph 提供两种 MCP 传输：

#### 3.3.1 本地 stdio MCP（开发者调试）

```bash
python -m mcp_server
```

从 stdin 逐行读取 JSON-RPC，向 stdout 写出响应。principal 可通过 `MCP_PRINCIPAL` 环境变量注入（仅本地调试）。

#### 3.3.2 企业 HTTP MCP

```
POST /api/v1/mcp
Authorization: Bearer <token>   # 走 OIDC 或 API Key
Content-Type: application/json

{
  "jsonrpc": "2.0",
  "id": "1",
  "method": "tools/call",
  "params": {
    "name": "mindgraph_search",
    "arguments": { "query": "差旅住宿标准", "top_k": 5 }
  }
}
```

#### 3.3.3 可用工具（全部只读）

| 工具 | 说明 | ACL |
|------|------|-----|
| `mindgraph_list_notes` | 台账列表 | ✅ 按主体裁剪 |
| `mindgraph_get_note` | 单篇笔记详情 | ✅ 越权返回 not_found |
| `mindgraph_search` | 语义检索（不生成答案） | ✅ 裁剪 chunk |
| `mindgraph_evaluation_overview` | 评测概览 | ✅ 统计按可见范围 |
| `mindgraph_list_relations` | confirmed 关系列表 | ✅ 双端可见才返回 |

工具清单：`GET /api/v1/mcp/tools`
健康检查：`GET /api/v1/mcp/health`

所有工具调用写入 `access_audit`（`action=mcp_*`）。

#### 3.3.4 验证

```bash
pytest tests/test_mcp.py -v
```

---

### 3.4 SSO / OIDC

#### 3.4.1 配置步骤

1. 在企业 IdP（Azure AD / Keycloak / Authentik 等）注册一个客户端，获取 `client_id`；
2. 在 `.env` 中配置：

```bash
AUTH_MODE=bearer
OIDC_ENABLED=true
OIDC_ISSUER_URL=https://login.microsoftonline.com/{tenant}/v2.0
OIDC_CLIENT_ID=mindgraph
OIDC_AUDIENCE=mindgraph
OIDC_ALGORITHMS=RS256
```

3. 在 IdP 侧为用户/组的 JWT 注入 `workspaces` / `departments` / `roles` claim（字段名可通过 `OIDC_*_CLAIM` 调整）；
4. 重启服务，带 `Authorization: Bearer <JWT>` 请求验证。

#### 3.4.2 认证优先级

```
请求进入 → 检查 OIDC Bearer → 命中则用 OIDC principal
                              → 未命中则检查 X-API-Key
                              → 均无则匿名
```

#### 3.4.3 验证

```bash
pytest tests/test_oidc.py -v
```

---

## 4. 数据库与升级

### 4.1 Schema 版本

当前 `SCHEMA_VERSION=7`。启动时自动迁移：

- `notes` 表新增 `workspace` / `department` / `acl_json` / `acl_public` 列；
- 新增 `connector_syncs` 表（连接器同步审计）；
- 新增 `access_audit` 表（权限审计）。

老库迁移不丢数据，迁移逻辑见 `infrastructure/database.py` 的 `_ensure_columns`。

### 4.2 备份

```bash
# 手动备份
sqlite3 data/product/product.sqlite3 ".backup data/backups/$(date +%Y%m%d).sqlite3"
```

定时备份由 `BACKUP_ENABLED=true` + `BACKUP_INTERVAL_HOURS=24` 控制。

### 4.3 索引重建

```bash
# 全量重建（通过 API）
POST /api/v1/knowledge/index/rebuild

# 增量重建
POST /api/v1/knowledge/index/incremental-rebuild
```

索引版本原子切换（`CURRENT.tmp` → `replace`），失败自动回滚。

---

## 5. 安全加固清单（生产上线前）

| 项 | 要求 |
|----|------|
| `AUTH_MODE` | 不允许 `demo` / `off`，至少 `api_key`，推荐 `bearer` |
| OIDC | 生产环境接企业 IdP，不使用共享密钥 |
| `data/api_keys.json` | 限制文件权限 `chmod 600`，不入 Git |
| ACL 回归测试 | `pytest tests/test_access_control.py` 全绿 |
| 越权检查 | `access_audit` 表中 `decision=deny` 记录可追溯 |
| CORS | `CORS_ORIGINS` 只列前端域名，不开放 `*` |
| 速率限制 | `RATE_LIMIT_ENABLED=true` |
| HTTPS | 生产环境必须通过反向代理提供 TLS |
| 日志 | `LOG_FORMAT=json`，`LOG_LEVEL=INFO` |
| 备份 | `BACKUP_ENABLED=true`，验证可恢复 |

---

## 6. 故障排查

### 6.1 检索返回空结果

| 可能原因 | 排查方法 |
|----------|----------|
| 索引未构建 | `GET /api/v1/knowledge/index/status`，status=missing 则 `POST /index/rebuild` |
| 笔记 `index_status` 不是 ready | `SELECT note_id,index_status FROM notes WHERE index_status<>'ready'` |
| ACL 裁剪过滤掉所有结果 | 查 `access_audit` 是否有 `decision=deny`；确认 principal 的 `workspaces`/`departments` 配置 |

### 6.2 OIDC token 校验失败

```
日志关键字：oidc_token_validation_failed
```

| 可能原因 | 排查方法 |
|----------|----------|
| `OIDC_ISSUER_URL` 不对 | 与 IdP 的 discovery 文档对比 |
| `audience` 不匹配 | 检查 `OIDC_AUDIENCE` 是否等于 token 的 `aud` |
| JWKS 端点不可达 | `curl {ISSUER}/.well-known/openid-configuration` 确认网络可达 |
| 算法不匹配 | `OIDC_ALGORITHMS` 需包含 IdP 实际签名算法 |

### 6.3 连接器同步失败

```
SQL: SELECT * FROM connector_syncs WHERE status='failed'
```

| 可能原因 | 排查方法 |
|----------|----------|
| 源目录不存在 | 确认 `source_path` 是绝对路径且为目录 |
| 文件编码非 UTF-8 | 日志中看 `errors` 字段 |
| 权限不足 | 进程对源目录的读写权限 |

### 6.4 MCP 工具调用失败

```
JSON-RPC error code -32603 = 工具执行异常
```

查 `access_audit` 中 `action=mcp_*` 的记录，确认 `decision` 与 `reason`。

---

## 7. 测试

```bash
# 全套企业能力回归测试
pytest tests/test_access_control.py tests/test_directory_connector.py tests/test_mcp.py tests/test_oidc.py -v

# 完整测试套件
pytest --no-cov
```

关键测试集：

| 测试文件 | 覆盖 |
|----------|------|
| `test_access_control.py` | ACL 裁剪、越权拒绝、审计写入 |
| `test_directory_connector.py` | 增量同步、删除剪枝、workspace/department 回填 |
| `test_mcp.py` | MCP 只读工具、ACL、审计 |
| `test_oidc.py` | OIDC claims → principal、Bearer 解析、端到端 ACL |
| `test_policy_metadata.py` | schema 迁移、策略版本冲突 |
| `test_api.py` | API 契约、错误格式、安全头 |
