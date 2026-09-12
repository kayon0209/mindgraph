import json

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile

from api.auth import current_actor, require_role, resolve_access_scope
from api.dependencies import get_container
from api.schemas.knowledge import DocumentRecord, IndexStatus
from application.access_control import note_acl_matches, record_access_audit


router = APIRouter(prefix="/knowledge", tags=["knowledge"])


@router.post(
    "/documents",
    response_model=DocumentRecord,
    status_code=201,
    deprecated=True,
    summary="[已弃用] 只落文件的旧上传入口，请改用 POST /knowledge/versions",
)
async def upload_document(file: UploadFile = File(...), category: str = Form("upload"), _auth: dict = Depends(require_role("write"))):
    """旧上传入口：只把文件写进 uploads/，不产生 document_versions、页级账本、
    解析诊断与 OCR 能力（P4 现场核对：用它上传的材料页级账本 0 条）。

    保留它只为不破坏**仓库外**的历史调用方（脚本、已分发的旧客户端），行为一字未改；
    仓库内调用方已全部切走——桌面端 ``ui/api_client.py`` 与 web 前端都只走
    ``POST /knowledge/versions``，客户端上的旧方法已删除（tests/test_milestone3.py
    有断言守着）。本路由自身仍有兼容性测试覆盖，行为改动不会被漏测。

    为什么不干脆让它内部委托 ``create_version``：那等于把"上传"的语义**悄悄**换成
    另一套——返回模型从 DocumentRecord 变成 DocumentVersionModel，落盘位置、幂等键
    （logical_document_id + version + checksum）、状态机全都不同。历史调用方会在完全
    无法察觉的情况下被改变行为，这比"两条路能力不同"更危险。收口办法是让旧路大声
    显形（``deprecated=True`` + 摘要写明），把新能力放在新入口。
    """
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
def rebuild_index(force: bool = Query(False, description="确认索引缩水后仍要激活（默认拒绝）"), _auth: dict = Depends(require_role("write"))):
    return get_container().knowledge.rebuild(force=force)


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
    try:
        parsed_acl = json.loads(acl_json) if acl_json else {}
    except json.JSONDecodeError as exc:
        raise ValueError("acl_json must be a valid JSON object") from exc
    if not isinstance(parsed_acl, dict):
        raise ValueError("acl_json must be a JSON object")
    return get_container().document_lifecycle.create_version(file.filename or "document", await file.read(), logical_document_id,
        version, category, authority_level, effective_date, expiration_date,
        workspace=workspace, department=department, acl_json=acl_json, acl_public=acl_public).model_dump(mode="json")


@router.get("/versions")
def list_document_versions(request: Request, status: str | None = None, category: str | None = None):
    scope = resolve_access_scope(request)
    return [item.model_dump(mode="json") for item in get_container().document_lifecycle.list(status, category, access_scope=scope)]


@router.get("/versions/{document_id}")
def get_document_version(document_id: str, request: Request):
    # 与 list 接口保持一致的 ACL 语义：无权限时 404（不泄漏存在性）
    scope = resolve_access_scope(request)
    record = get_container().document_lifecycle.get(document_id)
    if scope is not None and not note_acl_matches(record.model_dump(mode="python"), scope):
        raise HTTPException(status_code=404, detail="Document version not found")
    return record.model_dump(mode="json")


@router.post("/versions/{document_id}/transition")
def transition_document(document_id: str, request: Request, target: str = Query(...),
                        _auth: dict = Depends(require_role("write"))):
    container = get_container()
    record = container.document_lifecycle.transition(document_id, target)
    record_access_audit(
        container.database,
        actor=current_actor(request),
        action="transition_document",
        resource=f"document_versions/{document_id}",
        decision="allow",
        metadata={"target": target},
    )
    return record.model_dump(mode="json")


@router.post("/index/incremental-rebuild")
def incremental_rebuild(force: bool = Query(False, description="确认切分口径/文档覆盖变化后仍要激活（默认拒绝）"), _auth: dict = Depends(require_role("write"))):
    """P2：force 逃生口——与 /index/rebuild?force= 同语义（409 后显式确认才放行）。

    用 ``Query`` 而不是 ``Form``：既有客户端发的是空 POST（无 body、无
    Content-Type: application/json 之外的声明），Form 参数会让它们在没做任何
    改变的情况下直接 422；而且 force 是个开关，语义上就该在 query string 上。
    """
    return get_container().index_lifecycle.build(force=force)


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
