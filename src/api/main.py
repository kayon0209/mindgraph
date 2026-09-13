"""
FastAPI 应用入口 — 生产级配置。
- 全局异常处理
- 安全中间件（CORS / 安全 Headers / 速率限制）
- 请求追踪 ID
- 结构化日志
- 健康检查
"""
from __future__ import annotations

from contextlib import asynccontextmanager
import logging

from fastapi import Depends, FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException

from api.auth import require_authenticated
from api.dependencies import get_container
from application.task_worker_runner import maybe_start_task_worker
from api.exception_handlers import (
    authentication_error_handler,
    authorization_error_handler,
    http_exception_handler,
    product_error_handler,
    rate_limit_handler,
    unhandled_error_handler,
    validation_error_handler,
    value_error_handler,
)
from api.middleware import (
    LoggingMiddleware,
    RateLimitMiddleware,
    RequestSizeLimitMiddleware,
    SecurityHeadersMiddleware,
    TimingMiddleware,
    WatchdogMiddleware,
)
from api.routes import (
    assist,
    chat,
    connectors,
    evaluation,
    feedback,
    governance,
    health,
    knowledge,
    mcp,
    mindgraph_chat,
    mindgraph_readonly,
)
from domain.errors import (
    AuthenticationError,
    AuthorizationError,
    ProductError,
    RateLimitError,
)
from infrastructure.logging_config import configure_logging
from infrastructure.settings import get_settings
from infrastructure.sqlite_runtime import require_safe_sqlite_runtime

# ── 日志配置（使用 logging_config 中的结构化日志） ──
_settings = get_settings()
configure_logging(
    level=_settings.LOG_LEVEL,
    log_format="json" if _settings.is_production else "console",
)

logger = logging.getLogger("mindgraph.api")

# ── 应用生命周期 ──


def _warn_if_auth_disabled() -> None:
    """``AUTH_MODE=off`` 启动时必须告警：说明当前生效的授权范围。

    off 模式默认只读（写权限需 ``AUTH_OFF_ALLOW_WRITES=true`` 显式开启），
    但"完全关闭鉴权"本身就不该出现在对外可达的部署里，所以在启动日志中
    明确写出当前角色与风险，避免又一次"静默全权"。
    """
    from api.auth import _auth_mode, _off_mode_roles

    if _auth_mode() != "off":
        return
    roles = _off_mode_roles()
    write_enabled = "write" in roles
    logger.warning(
        "auth_disabled_at_startup",
        extra={
            "auth_mode": "off",
            "roles": roles,
            "write_enabled": write_enabled,
            "hint": (
                "AUTH_MODE=off 授予写权限，仅限本机单人开发，切勿对外暴露端口"
                if write_enabled
                else "AUTH_MODE=off 为只读；需要写操作请设 AUTH_OFF_ALLOW_WRITES=true（仅本机开发）"
            ),
        },
    )


def _warn_if_index_diverges() -> None:
    """启动时比对「``notes`` 声明可检索的文档」与「活跃索引实际覆盖的文档」。

    2026-09-09 的真实分叉：``notes`` 表 25 篇全部 ``index_status='ready'``，而
    ``CURRENT`` 指向的索引只有 4 篇 / 69 chunks——索引规模、按源过滤、ACL 过滤
    同时失真，而整条链路上没有任何提示。这里把它变成启动日志里的一行结论。

    但"语料只收 vault 根目录"是**已拍板的口径**（见 ``INDEX_INCLUDED_SUBTREES``），
    子目录缺的那批属"已接受的范围外缺失"。因此分三档记录：

    - 完全一致 → INFO ``index_corpus_consistent``；
    - 只剩范围外缺失 → INFO ``index_corpus_scope_declared``（可见但不报警）；
    - 范围内缺失 / 索引里有 notes 不认识的东西 → **ERROR** ``index_corpus_divergence``。

    这样告警长期为真的情况不会出现——否则它会变成没人看的背景噪音，
    09-09 那类真事故反而更容易藏在里面。
    """
    from application.index_metadata import audit_index_consistency, parse_included_subtrees
    from infrastructure.retrieval_factory import INDEX_ROOT

    included = parse_included_subtrees(getattr(_settings, "INDEX_INCLUDED_SUBTREES", ""))
    report = audit_index_consistency(
        index_root=INDEX_ROOT,
        db_path=_settings.DATABASE_PATH,
        included_subtrees=included,
    )
    if report["consistent"]:
        logger.info("index_corpus_consistent", extra=report)
    elif report["scope_consistent"]:
        logger.info("index_corpus_scope_declared", extra=report)
    else:
        logger.error("index_corpus_divergence", extra=report)


def _clarification_salt_warning_needed(settings) -> bool:
    """是否需要在启动时警告"澄清盐未配置"。

    门控理由：澄清卡生成端由 AGENT_ASSIST_ENABLED 控制、resume 端点由
    ASSIST_ENABLED 控制；两者都关时这条告警只是噪音，会训练运维忽略启动日志
    （真正的告警就淹在里面了）。纯函数，便于直接测三条组合。
    """
    return bool(settings.ASSIST_ENABLED or settings.AGENT_ASSIST_ENABLED) and not settings.MINDGRAPH_CLARIFICATION_SALT


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时初始化 ServiceContainer，关闭时清理资源。"""
    require_safe_sqlite_runtime()
    logger.info("application_starting", extra={"environment": _settings.ENVIRONMENT})
    _warn_if_auth_disabled()
    _warn_if_index_diverges()
    # P3：澄清 resume 依赖跨进程稳定盐；缺省时 resume 返回 server_misconfigured。
    # 启动即警告（而不是等用户撞上），部署文档见 .env.example。
    if _clarification_salt_warning_needed(_settings):
        logger.warning(
            "clarification_salt_missing",
            extra={"hint": "set MINDGRAPH_CLARIFICATION_SALT (e.g. `python -c \"import secrets; print(secrets.token_hex(32))\"`) to enable resumable clarifications"},
        )
    container = get_container()
    logger.info("service_container_initialized")
    # M4-A 缺口修复：TASK_WORKER_ENABLED=true 时拉起单实例任务轮询线程
    # （flag 关闭零行为变化；runner 在关闭时随进程退出）
    task_runner = maybe_start_task_worker(container)
    yield
    logger.info("application_shutting_down")
    if task_runner is not None:
        task_runner.stop()
    # 清理连接池等资源
    try:
        container.database.close()
    except Exception:  # 停机期尽力清理，失败不阻断退出
        logger.debug("database_close_failed", exc_info=True)
    logger.info("application_stopped")


# ── FastAPI 应用 ──

app = FastAPI(
    title="MindGraph API",
    description="企业制度与决策依据知识服务 — 基于可溯源 Hybrid RAG 与受控关系扩展",
    version="3.1.0",
    lifespan=lifespan,
    docs_url="/api/docs" if _settings.openapi_enabled else None,
    redoc_url="/api/redoc" if _settings.openapi_enabled else None,
    openapi_url="/api/openapi.json" if _settings.openapi_enabled else None,
)

# ── CORS 中间件 ──

app.add_middleware(
    CORSMiddleware,
    allow_origins=_settings.cors_origin_list,
    # 通配符 origin 时必须关闭凭据（P2-8）：否则任意网页可携带凭据跨域调用。
    allow_credentials=_settings.cors_allow_credentials,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Content-Type", "Authorization", "X-Request-ID", "X-API-Key"],
    expose_headers=["X-Request-ID", "X-RateLimit-Remaining", "X-RateLimit-Reset"],
    max_age=600,
)

# ── 安全中间件（顺序重要：先添加的在内层） ──

app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RequestSizeLimitMiddleware, max_body_bytes=_settings.MAX_UPLOAD_BYTES)
app.add_middleware(TimingMiddleware)
app.add_middleware(WatchdogMiddleware, timeout_seconds=30.0)  # P1-P1：全局软看门狗
app.add_middleware(LoggingMiddleware)  # 必须在 RateLimit 之后添加（内层），以确保 request_id 已设置

# ── 速率限制 ──
# 生产环境强制开启（settings.rate_limit_effective）；非生产默认关闭可显式打开。

if _settings.rate_limit_effective:
    app.add_middleware(
        RateLimitMiddleware,
        max_requests=_settings.RATE_LIMIT_MAX_REQUESTS,
        window_seconds=_settings.RATE_LIMIT_WINDOW_SECONDS,
    )

# ── 异常处理器注册 ──


def register_exception_handlers(target: FastAPI) -> None:
    """把统一异常处理器装到 target 上。

    单独成函数是为了"用 FastAPI() 只挂某个子路由"的契约测试：漏掉这一步时，
    业务异常（409/422/404）会退化成 500，测试测的就不是线上行为了。
    """
    target.add_exception_handler(ProductError, product_error_handler)  # type: ignore[arg-type]
    target.add_exception_handler(AuthenticationError, authentication_error_handler)  # type: ignore[arg-type]
    target.add_exception_handler(AuthorizationError, authorization_error_handler)  # type: ignore[arg-type]
    target.add_exception_handler(RateLimitError, rate_limit_handler)  # type: ignore[arg-type]
    target.add_exception_handler(RequestValidationError, validation_error_handler)  # type: ignore[arg-type]
    target.add_exception_handler(ValueError, value_error_handler)  # type: ignore[arg-type]
    target.add_exception_handler(StarletteHTTPException, http_exception_handler)  # type: ignore[arg-type]
    target.add_exception_handler(Exception, unhandled_error_handler)


register_exception_handlers(app)

# ── 路由注册 ──

API_PREFIX = "/api/v1"
app.include_router(health.router, prefix=API_PREFIX)
for route in (chat.router, connectors.router, knowledge.router, evaluation.router, feedback.router, governance.router, mindgraph_chat.router, mindgraph_readonly.router, mcp.router):
    app.include_router(route, prefix=API_PREFIX, dependencies=[Depends(require_authenticated)])

# M1：Assist（受治理的 agent 交付面）——默认关闭，仅在配置显式开启时挂载
# （特性开关在启动期读取，与 ServiceContainer 一致；off 态完全不暴露路由）。
if _settings.ASSIST_ENABLED:
    app.include_router(assist.router, prefix=API_PREFIX, dependencies=[Depends(require_authenticated)])

# M3：服务端会话——CONVERSATION_PERSISTENCE_ENABLED 默认关闭时完全不挂载。
if _settings.CONVERSATION_PERSISTENCE_ENABLED:
    from api.routes import conversation_stream as conversation_stream_route
    from api.routes import conversations as conversations_route

    app.include_router(conversations_route.router, prefix=API_PREFIX, dependencies=[Depends(require_authenticated)])
    app.include_router(conversation_stream_route.router, prefix=API_PREFIX, dependencies=[Depends(require_authenticated)])

# M4-A：后台任务——AGENT_TASKS_ENABLED 默认关闭时完全不挂载。
if _settings.AGENT_TASKS_ENABLED:
    from api.routes import agent_tasks as agent_tasks_route

    app.include_router(agent_tasks_route.router, prefix=API_PREFIX, dependencies=[Depends(require_authenticated)])

# ── 根路径 ──

@app.get("/", include_in_schema=False)
async def root():
    return {
        "service": "MindGraph",
        "version": "3.1.0",
        "docs": "/api/docs",
        "health": f"{API_PREFIX}/health",
    }
