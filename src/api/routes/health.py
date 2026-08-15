from fastapi import APIRouter

from api.dependencies import get_container
from infrastructure.settings import get_settings


router = APIRouter(tags=["system"])
_settings = get_settings()


@router.get("/health")
def health():
    return {"status": "ok"}


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
