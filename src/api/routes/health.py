import json

from fastapi import APIRouter

from api.dependencies import get_container
from infrastructure.settings import get_settings

router = APIRouter(tags=["system"])
_settings = get_settings()


@router.get("/health")
def health():
    try:
        governance = governance_health(get_container())
    except Exception:
        governance = _unavailable_governance_health()
    return {"status": "ok", "governance": governance}


def _unavailable_governance_health() -> dict:
    return {
        "schema_ready": False,
        "last_reconciled_at": None,
        "last_reconciliation_status": "unavailable",
        "pending_case_count": None,
        "active_index_governed": False,
    }


def governance_health(container) -> dict:
    """Return aggregate governance readiness without private identifiers."""
    database = container.database
    version = database.fetch_one("SELECT version FROM schema_meta LIMIT 1")
    tables = {
        str(row["name"])
        for row in database.fetch_all("SELECT name FROM sqlite_master WHERE type='table'")
    }
    schema_ready = bool(
        version
        and int(version["version"]) >= 9
        and {
            "governance_cases",
            "governance_case_notes",
            "governance_note_state",
            "governance_events",
        }.issubset(tables)
    )
    if not schema_ready:
        return _unavailable_governance_health()

    state = database.fetch_one(
        "SELECT MAX(updated_at) AS updated_at, COUNT(*) AS count FROM governance_note_state"
    )
    pending = database.fetch_one(
        "SELECT COUNT(*) AS count FROM governance_cases WHERE status='proposed'"
    )
    last_reconciled_at = state["updated_at"] if state and state["count"] else None
    active_index_governed = False
    root = getattr(container, "mindgraph_index_root", None)
    if root is not None:
        try:
            current = (root / "CURRENT").read_text(encoding="utf-8").strip()
            manifest = json.loads((root / current / "manifest.json").read_text(encoding="utf-8"))
            active_index_governed = bool(
                manifest.get("governance_as_of")
                and manifest.get("governance_policy_version")
            )
        except (OSError, ValueError, TypeError):
            active_index_governed = False
    return {
        "schema_ready": True,
        "last_reconciled_at": last_reconciled_at,
        "last_reconciliation_status": "completed" if last_reconciled_at else "not_run",
        "pending_case_count": int(pending["count"]) if pending else 0,
        "active_index_governed": active_index_governed,
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
