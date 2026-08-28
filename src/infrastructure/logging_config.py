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
import sys
from datetime import datetime, timezone
from typing import Any


class StructuredFormatter(logging.Formatter):
    """JSON 结构化日志格式化器。"""

    SENSITIVE_FIELDS = {"api_key", "password", "token", "secret", "authorization", "credit_card"}

    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        if record.exc_info and record.exc_info[1]:
            log_entry["exception"] = {
                "type": type(record.exc_info[1]).__name__,
                "message": str(record.exc_info[1]),
            }

        # 注入 extra 字段
        for key, value in record.__dict__.items():
            if key not in {
                "args", "asctime", "created", "exc_info", "exc_text", "filename",
                "funcName", "levelname", "levelno", "lineno", "module", "msecs",
                "msg", "name", "pathname", "process", "processName", "relativeCreated",
                "stack_info", "thread", "threadName",
            }:
                if isinstance(value, (str, int, float, bool, list, dict, type(None))):
                    if key.lower() in self.SENSITIVE_FIELDS:
                        log_entry[key] = "[REDACTED]"
                    else:
                        log_entry[key] = value

        return json.dumps(log_entry, ensure_ascii=False, default=str)


class ColoredFormatter(logging.Formatter):
    """开发环境带颜色的格式化器。"""

    COLORS = {
        logging.DEBUG: "\033[36m",     # cyan
        logging.INFO: "\033[32m",      # green
        logging.WARNING: "\033[33m",   # yellow
        logging.ERROR: "\033[31m",     # red
        logging.CRITICAL: "\033[35m",  # magenta
    }
    RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        color = self.COLORS.get(record.levelno, "")
        record.levelname = f"{color}{record.levelname}{self.RESET}"
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
