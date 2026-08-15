from fastapi import APIRouter, Query
from fastapi.responses import PlainTextResponse

from api.dependencies import get_container
from api.schemas.feedback import BadCase, BadCaseUpdate, FeedbackCreate, FeedbackRecord


router = APIRouter(tags=["feedback"])


@router.post("/feedback", response_model=FeedbackRecord, status_code=201)
def create_feedback(payload: FeedbackCreate):
    return get_container().feedback.create_feedback(payload)


@router.get("/bad-cases/export", response_class=PlainTextResponse)
def export_bad_cases(status: str | None = None, category: str | None = None):
    return get_container().feedback.export_bad_cases(status, category)


@router.get("/bad-cases", response_model=list[BadCase])
def list_bad_cases(status: str | None = None, category: str | None = None):
    return get_container().feedback.list_bad_cases(status, category)


@router.get("/bad-cases/{bad_case_id}", response_model=BadCase)
def get_bad_case(bad_case_id: str):
    return get_container().feedback.get_bad_case(bad_case_id)


@router.patch("/bad-cases/{bad_case_id}", response_model=BadCase)
def update_bad_case(bad_case_id: str, payload: BadCaseUpdate):
    return get_container().feedback.update_bad_case(bad_case_id, payload)
