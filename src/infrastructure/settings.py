"""基于 Pydantic Settings 的多环境配置管理。

环境变量自动加载 (.env)，按 ENVIRONMENT 选择配置覆盖。
所有配置项有类型校验和默认值。
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


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
    API_HOST: str = "0.0.0.0"
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

    # ── LLM Provider — OpenAI Compatible (DeepSeek) ──
    CHAT_PROVIDER: Literal["zhipu", "deepseek", "anthropic"] = "deepseek"
    OPENAI_COMPAT_PROVIDER_NAME: str = "deepseek"
    OPENAI_COMPAT_BASE_URL: str = "https://api.deepseek.com"
    OPENAI_COMPAT_API_KEY: str = ""
    OPENAI_COMPAT_MODEL: str = "deepseek-v4-flash"
    OPENAI_COMPAT_MODELS: str = "deepseek-v4-flash"
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

    # ── 数据库 ──
    DATABASE_PATH: str = str(PROJECT_ROOT / "data" / "product" / "product.sqlite3")
    SQLITE_JOURNAL_MODE: str = "WAL"
    SQLITE_SYNCHRONOUS: str = "NORMAL"
    SQLITE_CACHE_SIZE: int = -20000  # 20MB

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
        if self.CHAT_PROVIDER == "deepseek" and not self.OPENAI_COMPAT_API_KEY:
            missing.append("OPENAI_COMPAT_API_KEY")
        if self.CHAT_PROVIDER == "anthropic" and not self.ANTHROPIC_API_KEY:
            missing.append("ANTHROPIC_API_KEY")
        return missing


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """获取全局配置单例（缓存，第一次加载后不变）。"""
    return Settings()
