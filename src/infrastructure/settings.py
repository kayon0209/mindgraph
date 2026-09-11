"""基于 Pydantic Settings 的多环境配置管理。

环境变量自动加载 (.env)，按 ENVIRONMENT 选择配置覆盖。
所有配置项有类型校验和默认值。
"""
from __future__ import annotations

from functools import lru_cache
import logging
from pathlib import Path
from typing import Literal

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    """全局应用配置。"""

    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── 运行环境 ──
    ENVIRONMENT: Literal["development", "staging", "production"] = "development"
    DEBUG: bool = False
    LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    LOG_FORMAT: Literal["json", "console"] = "console"

    # ── 服务 ──
    API_HOST: str = "0.0.0.0"  # nosec B104 -- 容器化部署默认绑定，生产经反向代理暴露，可用 API_HOST 环境变量覆盖
    API_PORT: int = 8000
    API_BASE_URL: str = "http://localhost:8000/api/v1"
    STREAMLIT_PORT: int = 8501
    STREAMLIT_ORIGIN: str = "http://localhost:8501"
    CORS_ORIGINS: str = ""  # 逗号分隔

    # ── 认证 ──
    AUTH_MODE: Literal["off", "api_key", "bearer", "demo"] = "demo"
    API_KEY_HEADER: str = "X-API-Key"
    SESSION_TIMEOUT_SECONDS: int = 3600

    # ── SSO / OIDC（Phase 5-4） ──
    OIDC_ENABLED: bool = False
    OIDC_ISSUER_URL: str = ""  # e.g. https://login.microsoftonline.com/{tenant}/v2.0
    OIDC_CLIENT_ID: str = ""
    OIDC_CLIENT_SECRET: str = ""
    OIDC_AUDIENCE: str = ""  # 可选；为空时回退到 client_id
    OIDC_ALGORITHMS: str = "RS256"  # 逗号分隔
    OIDC_JWKS_CACHE_TTL_SECONDS: int = 600
    OIDC_ROLES_CLAIM: str = "roles"
    OIDC_WORKSPACES_CLAIM: str = "workspaces"
    OIDC_DEPARTMENTS_CLAIM: str = "departments"
    OIDC_USERNAME_CLAIM: str = "preferred_username"

    # ── 企业连接器 ──
    CONNECTOR_ALLOWED_ROOTS: str = ""  # 逗号分隔；knowledge/ 始终作为受控根目录

    # ── 安全 ──
    RATE_LIMIT_ENABLED: bool = False
    RATE_LIMIT_MAX_REQUESTS: int = 60
    RATE_LIMIT_WINDOW_SECONDS: int = 60
    MAX_UPLOAD_BYTES: int = 10 * 1024 * 1024  # 10MB
    PRIVACY_LOG_QUESTIONS: bool = True

    # ── LLM Provider — 智谱 ──
    ZHIPU_API_KEY: str = ""
    ZHIPU_MODEL: str = "glm-4.7"
    ZHIPU_VERIFIED: bool = False

    # ── LLM Provider — OpenAI 兼容槽 ──
    # 命名必须与**真实后端**一致。历史遗留：该槽的 provider 名写作 "deepseek"，
    # 但端点早在 2026-08-27 就已改指 Gitee AI（见
    # FULL-AUDIT-OPTIMIZATION-2026-08-27.md §329），结果评测记录把
    # qwen3.8-flash 标成了 deepseek，一被追问就会穿。现按真实后端起名 gitee。
    # "deepseek" 保留为**已弃用别名**：见 _remap_deprecated_chat_provider，
    # 会自动改写并打告警，不静默失效。
    CHAT_PROVIDER: Literal["zhipu", "gitee", "anthropic", "deepseek"] = "gitee"
    OPENAI_COMPAT_PROVIDER_NAME: str = "gitee"
    OPENAI_COMPAT_BASE_URL: str = "https://ai.gitee.com/v1"
    OPENAI_COMPAT_API_KEY: str = ""
    OPENAI_COMPAT_MODEL: str = "qwen3.8-flash"
    OPENAI_COMPAT_MODELS: str = "qwen3.8-flash"
    OPENAI_COMPAT_VERIFIED: bool = False
    CHAT_TIMEOUT_SECONDS: int = 60
    CHAT_MAX_RETRIES: int = 1
    MCP_TIMEOUT_SECONDS: float = 15.0
    MCP_MAX_BATCH_ITEMS: int = 20

    # ── LLM Provider — Anthropic ──
    ANTHROPIC_API_KEY: str = ""
    ANTHROPIC_MODEL: str = "claude-sonnet-4-20250514"

    # ── Embedding ──
    BGE_MODEL_NAME: str = "BAAI/bge-small-zh-v1.5"
    BGE_MODEL_REVISION: str = ""
    BGE_BATCH_SIZE: int = 32
    BGE_LOCAL_FILES_ONLY: bool = True

    # ── Retrieval ──
    RETRIEVAL_CANDIDATE_COUNT: int = 20
    RETRIEVAL_FINAL_TOP_K: int = 5
    BM25_K1: float = 1.5
    BM25_B: float = 0.75
    RRF_CONSTANT: int = 60
    RERANKER_ENABLED: bool = False
    RERANKER_MODEL_NAME: str = "BAAI/bge-reranker-base"
    RERANKER_LOCAL_FILES_ONLY: bool = True
    RERANK_TOP_N: int = 10

    # ── Graph 路由（计划 Phase 5 发布闸门） ──
    # 消融闸门（evaluation/ablation_runner.evaluate_graph_gate）满足
    # Recall@5≥+5pp、延迟≤3x 后，由人工决策把结论写回这里：
    # True = Adaptive 路由默认允许图扩展（OR 语义，全局生效；回滚请改回 False，
    # 不提供 per-request opt-out —— 见 ADR-002 Gate-to-config flow）；
    # False（默认）= 图保持实验态、仅客户端 opt-in。
    GRAPH_DEFAULT_ENABLED: bool = False

    # ── 问题概念挖掘（阶段B：仅数据积累，不进入检索路径） ──
    # 聊天落库后自动增量挖掘（规则式，无 LLM）；只产出 proposed 候选，需 HITL 确认。
    CONCEPT_MINE_AUTO_ENABLED: bool = True
    # 累计 N 条新提问后触发一次后台自动挖掘
    CONCEPT_MINE_AUTO_MIN_NEW_QUESTIONS: int = 20
    # 覆盖缺口面板只展示出现次数 ≥ 该值的未收录概念
    CONCEPT_MINE_GAP_MIN_SEEN: int = 2

    # ── Assist（M0/M1 受治理的 agent 交付面） ──
    # 特性开关模式：默认关闭（off 态与既有 REST/SSE/MCP/Chat 行为字节兼容），
    # 由部署方在 .env 显式开启。开启后：
    # - ASSIST_ENABLED=True：挂载 /api/v1/assist（REST + SSE），进程内复用
    #   ChatService 应用服务（无 HTTP/MCP 自调用），ACL + 审计与 /chat 一致；
    # - ASSIST_MCP_ENABLED=True：额外把 Assist 暴露为只读 MCP 工具
    #   mindgraph_assist（复用同一应用服务）。
    ASSIST_ENABLED: bool = False
    ASSIST_MCP_ENABLED: bool = False
    # Assist 通道的时限与上限（沿用 chat 检索语义，仅作为通道级护栏）
    ASSIST_TIMEOUT_SECONDS: float = 60.0
    ASSIST_MAX_TOP_K: int = 10

    # ── Agentic Evidence Layer 阶段开关（ADR-003；M0 仅登记，不消费） ──
    # 依据《MindGraph Agent 化实施方案》M0 要求登记、后续里程碑按序消费：
    # - AGENT_ASSIST_ENABLED：M2 内置确定性 Assist（计划/工具轨迹/澄清协议）；
    # - AGENT_TASKS_ENABLED：M4 可恢复后台任务与 artifacts（Gate G1 之后）；
    # - AGENT_WRITE_TOOLS_ENABLED：M5 受控 MCP 写工具（M4 稳定后）；
    # - CONVERSATION_PERSISTENCE_ENABLED：M3 服务端会话持久化。
    # 本阶段（M0/M1）打开这些开关不得产生任何运行时行为变化。
    AGENT_ASSIST_ENABLED: bool = False
    AGENT_TASKS_ENABLED: bool = False
    AGENT_WRITE_TOOLS_ENABLED: bool = False
    CONVERSATION_PERSISTENCE_ENABLED: bool = False
    # Assist 单请求工具调用预算（M2 AgentExecutionPolicy 消费；M0 仅登记）
    AGENT_MAX_TOOL_CALLS: int = 3
    # Assist 请求 deadline（秒；M2 消费；MCP 通道沿用协作式 deadline 机制）
    AGENT_REQUEST_DEADLINE_SECONDS: float = 45.0
    # ── M4-A worker 运行参数（方案 §12；TASK_WORKER_ENABLED 由 lifespan 消费） ──
    # true = API 启动时拉起单实例后台轮询线程执行 agent_tasks；false = 仅
    # 留在 queued（需外部调用 run_until_drained）。单实例单 worker：启动时
    # 防重检查（同库不允许两个运行线程），不假称多实例安全（ADR-004）。
    TASK_WORKER_ENABLED: bool = False
    TASK_LEASE_SECONDS: float = 120.0
    TASK_MAX_ATTEMPTS: int = 3
    # 空轮询间隔（秒）：无任务时线程休眠时长
    TASK_POLL_INTERVAL_SECONDS: float = 2.0
    # ── M5-A 工具 B：submit_evidence_feedback（中风险，独立开关） ──
    # true = tools/list 暴露 mindgraph_submit_evidence_feedback（preview/submit
    # 两段确认模式）。回滚：置回 false 即隐藏，已提交反馈保留（质量账本不回滚）。
    AGENT_FEEDBACK_TOOL_ENABLED: bool = False
    # ── M5-A 工具 C：propose_relation（高风险，独立开关） ──
    # true = tools/list 暴露 mindgraph_propose_relation（preview/submit 两段
    # 确认 + 仅 proposed + 三端 ACL + 双向幂等）。回滚：置回 false 即隐藏，
    # 已创建的 proposed 候选留在审核队列（HITL 流不受影响）。
    AGENT_PROPOSE_RELATION_TOOL_ENABLED: bool = False
    # ── M3-E 会话保留策略（方案：可配置 retention；执行=到期归档，不物理删） ──
    # 0 = 不启用自动保留期（会话永久 active，由用户手动归档）；>0 = 创建会话时
    # 写入 retention_until = now + N 天，由 conversation runner 到期归档。
    CONVERSATION_RETENTION_DAYS: int = 0

    # ── 数据库 ──
    DATABASE_PATH: str = str(PROJECT_ROOT / "data" / "product" / "product.sqlite3")
    SQLITE_JOURNAL_MODE: str = "WAL"
    SQLITE_SYNCHRONOUS: str = "NORMAL"
    SQLITE_CACHE_SIZE: int = -20000  # 20MB

    # ── 索引语料范围（产品决策，2026-09-10 拍板） ──
    # 活跃索引**声明只覆盖 vault 根目录**的 markdown；子目录（policies/、
    # workflows/、cases/、external/public/）显式声明为"不在索引范围"。
    # 审计据此把"范围外缺失"（INFO，已接受的口径）与"真分叉"（ERROR）分开，
    # 避免一个已知缺口变成永远消不掉的假警报。
    # 逗号分隔的一级子树名；空字符串 = 仅根目录。
    # ⚠️ 修改此值 = 改语料口径：索引内容会变，已公布的检索指标（R@5 等）随即
    # 失效，必须重跑消融并重新公布。
    INDEX_INCLUDED_SUBTREES: str = ""

    # ── 切分策略（PR-03 单一来源）──
    # 预设名（见 application.chunking_policy）；空 = legacy_v1（历史参数
    # 500/1200/50 的精确快照）。未知名在运行时 fail-closed 拒绝。
    # ⚠️ 换策略 = 改切分输出与 chunk ID 分母：必须重建索引并重跑检索回归，
    # 已公布指标（R@5 等）随即失效。
    CHUNKING_POLICY: str = ""

    # ── 索引激活一致性门禁（PR-04）──
    # 激活前比对候选索引与活跃索引的切分口径/文档覆盖，不一致则拒绝改写 CURRENT。
    # 2026-09-11 实测：69 chunks（扁平）与 98 chunks（StructuredChunker）两个版本
    # 在同一根内并存，CURRENT 被切换过而无任何阻止 —— 本 flag 就为阻断这类
    # 「未经认可的口径切换」而设。默认 fail-closed；置 false 即恢复旧激活流程
    # （回滚路径），但切换后果仍由人承担。
    INDEX_CONSISTENCY_GATE: bool = True

    # ── 缓存 ──
    CACHE_ENABLED: bool = True
    ANSWER_CACHE_TTL_SECONDS: int = 3600
    EMBEDDING_CACHE_SIZE: int = 10000

    # ── 备份 ──
    BACKUP_ENABLED: bool = True
    BACKUP_INTERVAL_HOURS: int = 24
    BACKUP_RETENTION_DAYS: int = 30
    BACKUP_DIR: str = str(PROJECT_ROOT / "data" / "backups")

    # ── 监控 ──
    SLOW_REQUEST_THRESHOLD_MS: int = 1000
    HEALTH_CHECK_INTERVAL_SECONDS: int = 30

    @model_validator(mode="after")
    def _remap_deprecated_chat_provider(self) -> Settings:
        """防止「provider 名字与真实后端不符」再次隐身。

        历史问题：OpenAI 兼容槽的 provider 名长期写作 ``deepseek``，但端点早已改指
        Gitee AI（qwen3.8-flash），评测记录因此把模型标成 deepseek。这里做两件事：

        1. 若槽名仍是 ``deepseek`` 而 ``OPENAI_COMPAT_BASE_URL`` 的 host 里并不含
           ``deepseek``，直接告警——名字可证伪，不能沉默；
        2. 若 ``CHAT_PROVIDER`` 是已弃用别名且与槽名不一致，改写为槽名（避免旧
           ``.env`` 因注册表按名路由而突然起不来），同时告警。
        """
        slot_name = self.OPENAI_COMPAT_PROVIDER_NAME
        base_host = self.OPENAI_COMPAT_BASE_URL.split("//")[-1].split("/")[0].lower()
        if "deepseek" in slot_name.lower() and "deepseek" not in base_host:
            logger.warning(
                "chat_provider_label_mismatch",
                extra={
                    "provider_name": slot_name,
                    "base_url_host": base_host,
                    "model": self.OPENAI_COMPAT_MODEL,
                    "hint": "provider 名应与真实后端一致，否则评测记录会标错来源",
                },
            )
        if self.CHAT_PROVIDER == "deepseek" and slot_name != "deepseek":
            logger.warning(
                "chat_provider_alias_deprecated",
                extra={"configured": self.CHAT_PROVIDER, "effective": slot_name},
            )
            self.CHAT_PROVIDER = slot_name  # type: ignore[assignment]
        return self

    @field_validator("ZHIPU_API_KEY", "OPENAI_COMPAT_API_KEY", "ANTHROPIC_API_KEY", mode="before")
    @classmethod
    def strip_api_keys(cls, v: str | None) -> str:
        return (v or "").strip()

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def normalize_cors(cls, v: str) -> str:
        if not v:
            return "http://localhost:8501"
        return v

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]

    @property
    def cors_allow_credentials(self) -> bool:
        """通配符 origin 与 allow_credentials=true 组合会让任意站点以凭据模式
        跨域调用本地 API（drive-by localhost 风险）；仅在显式枚举来源时启用凭据。"""
        return "*" not in self.cors_origin_list

    @property
    def connector_allowed_root_list(self) -> tuple[Path, ...]:
        return tuple(Path(item.strip()).resolve() for item in self.CONNECTOR_ALLOWED_ROOTS.split(",") if item.strip())

    @property
    def rate_limit_effective(self) -> bool:
        """计划 Phase 7 要求"限额与超时生效"：生产环境强制开启速率限制，
        非生产环境可经 RATE_LIMIT_ENABLED 显式开启。"""
        return self.RATE_LIMIT_ENABLED or self.is_production

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"

    @property
    def openapi_enabled(self) -> bool:
        return not self.is_production

    def validate_required_keys(self) -> list[str]:
        """检查必要的 API Key 是否配置。"""
        missing = []
        if self.CHAT_PROVIDER == "zhipu" and not self.ZHIPU_API_KEY:
            missing.append("ZHIPU_API_KEY")
        if self.CHAT_PROVIDER == "anthropic" and not self.ANTHROPIC_API_KEY:
            missing.append("ANTHROPIC_API_KEY")
        # 其余取值都落在 OpenAI 兼容槽（gitee 等），按槽校验而不是写死名字。
        if self.CHAT_PROVIDER not in {"zhipu", "anthropic"} and not self.OPENAI_COMPAT_API_KEY:
            missing.append("OPENAI_COMPAT_API_KEY")
        return missing


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """获取全局配置单例（缓存，第一次加载后不变）。"""
    return Settings()
