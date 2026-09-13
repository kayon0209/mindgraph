from fastapi import APIRouter, Depends, Request
from fastapi.responses import PlainTextResponse

from api.auth import current_actor, require_authenticated, require_role
from api.dependencies import get_container
from api.schemas.feedback import BadCase, BadCaseUpdate, FeedbackCreate, FeedbackRecord

router = APIRouter(tags=["feedback"])


@router.post("/feedback", response_model=FeedbackRecord, status_code=201)
def create_feedback(payload: FeedbackCreate, request: Request, principal: dict = Depends(require_authenticated)):
    """提交反馈：仅问答所属 principal 可写（一 request 一反馈）；他人与
    不存在的 request_id 返回同一 404，不暴露存在性（安全审查 F1）。"""
    return get_container().feedback.create_feedback(payload, principal_id=current_actor(request))


# ── bad-cases 是质量管理员面：含全体用户问答内容，仅 admin 可读（安全审查 F1） ──


@router.get("/bad-cases/export", response_class=PlainTextResponse, dependencies=[Depends(require_role("admin"))])
def export_bad_cases(status: str | None = None, category: str | None = None):
    return get_container().feedback.export_bad_cases(status, category)


@router.get("/bad-cases", response_model=list[BadCase], dependencies=[Depends(require_role("admin"))])
def list_bad_cases(status: str | None = None, category: str | None = None):
    return get_container().feedback.list_bad_cases(status, category)


@router.get("/bad-cases/{bad_case_id}", response_model=BadCase, dependencies=[Depends(require_role("admin"))])
def get_bad_case(bad_case_id: str):
    return get_container().feedback.get_bad_case(bad_case_id)


@router.patch("/bad-cases/{bad_case_id}", response_model=BadCase, dependencies=[Depends(require_role("admin"))])
def update_bad_case(bad_case_id: str, payload: BadCaseUpdate):
    return get_container().feedback.update_bad_case(bad_case_id, payload)
