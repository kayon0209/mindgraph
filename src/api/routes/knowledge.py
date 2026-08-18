from fastapi import APIRouter, Depends, File, Form, UploadFile, Query, Request

from api.auth import require_role, resolve_access_scope
from api.dependencies import get_container
from api.schemas.knowledge import DocumentRecord, IndexStatus


router = APIRouter(prefix="/knowledge", tags=["knowledge"])


@router.post("/documents", response_model=DocumentRecord, status_code=201)
async def upload_document(file: UploadFile = File(...), category: str = Form("upload"), _auth: dict = Depends(require_role("write"))):
    if not (file.filename or "").lower().endswith(".md"):
        raise ValueError("Only Markdown files are supported")
    if file.content_type not in {"text/markdown", "text/plain", "application/octet-stream"}:
        raise ValueError("Unsupported upload content type")
    return get_container().knowledge.upload(file.filename or "document.md", await file.read(), category)


@router.get("/documents", response_model=list[DocumentRecord])
def list_documents():
    return get_container().knowledge.list_documents()


@router.get("/documents/{document_id}", response_model=DocumentRecord)
def get_document(document_id: str):
    return get_container().knowledge.get_document(document_id)


@router.delete("/documents/{document_id}", response_model=DocumentRecord)
def delete_document(document_id: str, _auth: dict = Depends(require_role("write"))):
    return get_container().knowledge.delete(document_id)


@router.post("/index/rebuild", response_model=IndexStatus)
def rebuild_index(_auth: dict = Depends(require_role("write"))):
    return get_container().knowledge.rebuild()


@router.get("/index/status", response_model=IndexStatus)
def index_status():
    return get_container().knowledge.index_status()


@router.post("/versions", status_code=201)
async def upload_document_version(file: UploadFile = File(...), logical_document_id: str | None = Form(None),
                                  version: str = Form("v1"), category: str = Form("other"),
                                  authority_level: str = Form("user_uploaded_reference"),
                                  effective_date: str | None = Form(None), expiration_date: str | None = Form(None),
                                  workspace: str | None = Form(None), department: str | None = Form(None),
                                  acl_json: str = Form("{}"), acl_public: bool = Form(False),
                                  _auth: dict = Depends(require_role("write"))):
    if file.filename and not any(file.filename.lower().endswith(ext) for ext in (".md", ".txt", ".pdf", ".docx", ".xlsx")):
        raise ValueError("Unsupported file type. Allowed: .md, .txt, .pdf, .docx, .xlsx")
    VALID_AUTHORITY = {"official_policy", "official_guideline", "approved_faq", "user_uploaded_reference", "external_reference"}
    if authority_level not in VALID_AUTHORITY:
        raise ValueError(f"Invalid authority_level. Allowed: {', '.join(sorted(VALID_AUTHORITY))}")
    return get_container().document_lifecycle.create_version(file.filename or "document", await file.read(), logical_document_id,
        version, category, authority_level, effective_date, expiration_date,
        workspace=workspace, department=department, acl_json=acl_json, acl_public=acl_public).model_dump(mode="json")


@router.get("/versions")
def list_document_versions(request: Request, status: str | None = None, category: str | None = None):
    scope = resolve_access_scope(request)
    return [item.model_dump(mode="json") for item in get_container().document_lifecycle.list(status, category, access_scope=scope)]


@router.get("/versions/{document_id}")
def get_document_version(document_id: str):
    return get_container().document_lifecycle.get(document_id).model_dump(mode="json")


@router.post("/versions/{document_id}/transition")
def transition_document(document_id: str, target: str = Query(...), _auth: dict = Depends(require_role("write"))):
    return get_container().document_lifecycle.transition(document_id, target).model_dump(mode="json")


@router.post("/index/incremental-rebuild")
def incremental_rebuild(_auth: dict = Depends(require_role("write"))):
    return get_container().index_lifecycle.build()


@router.get("/index/versions")
def list_index_versions():
    return get_container().index_lifecycle.versions()


@router.get("/index/versions/{version}")
def get_index_version(version: str):
    return get_container().index_lifecycle.get(version)


@router.post("/index/versions/{version}/activate")
def activate_index_version(version: str, reason: str = "manual activation", _auth: dict = Depends(require_role("write"))):
    return get_container().index_lifecycle.activate(version, reason=reason)


@router.post("/index/rollback")
def rollback_index(reason: str = "manual rollback", _auth: dict = Depends(require_role("write"))):
    return get_container().index_lifecycle.rollback(reason=reason)
