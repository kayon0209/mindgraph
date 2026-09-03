import time

from fastapi import APIRouter

from api.dependencies import get_container
from infrastructure.settings import get_settings


router = APIRouter(tags=["system"])
_settings = get_settings()
_started_at = time.time()


@router.get("/health")
def health():
    """Liveness + 基础可观测字段（走查 S2）：uptime 供外部 Uptime 监控接
    入；db_probe 区分「进程活」与「数据库可查」——进程卡死/SQLite 阻塞
    场景下，监控能第一时间报警而不是等到用户发现。"""
    db_probe = "ok"
    try:
        container = get_container()
        container.database.fetch_one("SELECT 1 AS alive")
    except Exception as exc:
        db_probe = f"error: {type(exc).__name__}"
    return {
        "status": "ok" if db_probe == "ok" else "degraded",
        "uptime_seconds": round(time.time() - _started_at, 1),
        "db_probe": db_probe,
        "worker_enabled": _settings.TASK_WORKER_ENABLED,
    }


@router.get("/readiness")
def readiness():
    try:
        container = get_container()
        status = container.knowledge.index_status()
        return {"ready": status.status == "ready", "index": status.model_dump(mode="json"), "provider_available": container.provider.available}
    except Exception as exc:
        return {"ready": False, "error": str(exc), "provider_available": False}


@router.get("/config/public")
def public_config():
    container = get_container()
    categories = sorted({item.knowledge_category for item in container.document_lifecycle.list()}) if hasattr(container, "document_lifecycle") else []
    return {
        "retrieval_strategies": ["dense", "bm25", "hybrid", "hybrid_rerank"],
        "default_retrieval_strategy": "hybrid",
        "chat_models": container.provider_registry.capabilities() if hasattr(container, "provider_registry") else [{"provider": getattr(container.provider, "provider_name", "test"), "model": container.provider.model_name, "configured": container.provider.available, "verified": False}],
        "default_chat_provider": getattr(getattr(container, "provider_registry", None), "default_provider", getattr(container.provider, "provider_name", "zhipu")),
        "max_upload_bytes": 2097152,
        "privacy_log_questions": container.chat.privacy_log_questions,
        "evaluation_queue": "in_process_non_durable",
        "knowledge_categories": categories,
        "authority_weights": {
            "official_policy": 0.020, "official_guideline": 0.015, "approved_faq": 0.010,
            "user_uploaded_reference": 0.005, "external_reference": 0.0,
        },
    }
