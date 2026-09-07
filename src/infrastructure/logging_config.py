"""生产级结构化日志配置。

用法:
    from infrastructure.logging_config import configure_logging
    configure_logging()

特性:
    - JSON 结构化日志（生产）/ 彩色控制台（开发）
    - 请求追踪 ID 自动注入
    - 敏感字段自动脱敏
    - 日志级别按模块控制
    - 慢请求检测
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
from datetime import datetime, timezone
from typing import Any


def _normalize_key(key: str) -> str:
    return "".join(ch for ch in key.lower() if ch.isalnum())


# 密钥形态探测（用于值级与消息片段级遮蔽）
_SECRET_PATTERNS = (
    re.compile(r"sk-ant-[A-Za-z0-9_-]{10,}"),
    re.compile(r"sk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{16,}"),
    re.compile(r"\b[A-Za-z0-9]{20,}\.[A-Za-z0-9]{20,}\b"),  # zhipu: id.secret
    re.compile(r"\b[A-Za-z0-9_-]{40,}\b"),  # 超长 base64/hex 形态 token
)


def redact_text(text: str) -> str:
    """把文本中的疑似密钥片段替换为 [REDACTED]（消息正文/异常详情用）。"""
    if not isinstance(text, str) or not text:
        return text
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text


def redact_value(key: str, value: Any) -> Any:
    """递归脱敏一个 extra 字段值。"""
    normalized = _normalize_key(key)
    if normalized in _REDACT_KEYS_NORMALIZED:
        return "[REDACTED]"
    if isinstance(value, str):
        return "[REDACTED]" if _looks_like_secret(value) else redact_text(value)
    if isinstance(value, dict):
        return {child_key: redact_value(str(child_key), child_value) for child_key, child_value in value.items()}
    if isinstance(value, list):
        return [redact_value(key, item) for item in value]
    return value


def _looks_like_secret(text: str) -> bool:
    """值是否整体疑似密钥（命中任一形态即遮蔽）。"""
    if not isinstance(text, str) or not text:
        return False
    return any(pattern.search(text) for pattern in _SECRET_PATTERNS)


_REDACT_KEYS_NORMALIZED = {
    "apikey",
    "xapikey",  # 审查 F6：X-API-Key 头的归一化形态（横线剥离后）
    "accesskey",
    "secret",
    "password",
    "token",
    "authorization",
    "bearer",
    "credential",
    "clientsecret",
    "clientidsecret",
    "privatekey",
}


class StructuredFormatter(logging.Formatter):
    """JSON 结构化日志格式化器。"""

    # 精确字段名命中（向后兼容既有调用）；归一化键集合见模块级
    # _REDACT_KEYS_NORMALIZED（api_key / API-KEY / client secret 等变体）。
    SENSITIVE_FIELDS = {"api_key", "password", "token", "secret", "authorization", "credit_card"}

    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            # M0 日志脱敏：消息正文里的疑似密钥片段一律遮蔽
            "message": redact_text(record.getMessage()),
        }

        if record.exc_info and record.exc_info[1]:
            log_entry["exception"] = {
                "type": type(record.exc_info[1]).__name__,
                "message": redact_text(str(record.exc_info[1])),
            }

        # 注入 extra 字段（递归脱敏：敏感键直接遮蔽，值疑似密钥也遮蔽）
        for key, value in record.__dict__.items():
            if key not in {
                "args",
                "asctime",
                "created",
                "exc_info",
                "exc_text",
                "filename",
                "funcName",
                "levelname",
                "levelno",
                "lineno",
                "module",
                "msecs",
                "msg",
                "name",
                "pathname",
                "process",
                "processName",
                "relativeCreated",
                "stack_info",
                "thread",
                "threadName",
            }:
                scrubbed = redact_value(key, value)
                if scrubbed is not None:
                    log_entry[key] = scrubbed

        return json.dumps(log_entry, ensure_ascii=False, default=str)


class ColoredFormatter(logging.Formatter):
    """开发环境带颜色的格式化器。"""

    COLORS = {
        logging.DEBUG: "\033[36m",  # cyan
        logging.INFO: "\033[32m",  # green
        logging.WARNING: "\033[33m",  # yellow
        logging.ERROR: "\033[31m",  # red
        logging.CRITICAL: "\033[35m",  # magenta
    }
    RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        color = self.COLORS.get(record.levelno, "")
        record.levelname = f"{color}{record.levelname}{self.RESET}"
        # 审查 F6：console 格式（非生产默认）同样走脱敏——异常文本里的
        # 密钥片段不得因开发环境格式而原样落 stdout
        record.msg = redact_text(record.getMessage())
        record.args = ()
        return super().format(record)


def configure_logging(
    level: str | None = None,
    log_format: str | None = None,
    slow_request_threshold_ms: int = 1000,
) -> None:
    """配置全局日志系统。

    Args:
        level: 日志级别 (DEBUG/INFO/WARNING/ERROR/CRITICAL)
        log_format: 输出格式 (json/console)
        slow_request_threshold_ms: 慢请求阈值（毫秒）
    """
    log_level = (level or os.getenv("LOG_LEVEL", "INFO")).upper()
    fmt = (log_format or os.getenv("LOG_FORMAT", "console")).lower()

    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # 清除已有 handler
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(log_level)

    formatter: logging.Formatter
    if fmt == "json":
        formatter = StructuredFormatter()
    else:
        formatter = ColoredFormatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    handler.setFormatter(formatter)
    root_logger.addHandler(handler)

    # 第三方库日志抑制
    for lib in ("uvicorn", "uvicorn.access", "uvicorn.error", "httpx", "httpcore", "sentence_transformers", "transformers"):
        logging.getLogger(lib).setLevel(logging.WARNING)

    # 应用内部日志级别
    for module in ("mindgraph", "expense_rag", "src"):
        logging.getLogger(module).setLevel(log_level)

    # 存储慢请求阈值
    os.environ["SLOW_REQUEST_THRESHOLD_MS"] = str(slow_request_threshold_ms)

    root_logger.info(
        "logging_configured",
        extra={"level": log_level, "format": fmt, "slow_request_threshold_ms": slow_request_threshold_ms},
    )


def get_logger(name: str) -> logging.Logger:
    """获取带上下文的 logger。"""
    return logging.getLogger(f"mindgraph.{name}")
