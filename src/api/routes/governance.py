from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from api.auth import require_role
from api.dependencies import get_container


router = APIRouter(prefix="/governance", tags=["governance"])

# 写操作（注册数据集/写标注/写评审/改提示词）只允许 admin 角色：
# off 模式下"已认证"等于匿名可写，会污染评测治理数据并动摇验收结论。
# 与关系审核端点（mindgraph_readonly.resolve_relation）的门槛保持一致。
_require_admin = require_role("admin")


class DatasetCreate(BaseModel):
    dataset_id: str
    version: str
    dataset_type: str
    purpose: str
    cases: list[dict[str, Any]] = Field(default_factory=list)
    annotation_status: str = "incomplete"
    change_note: str


class AnnotationCreate(BaseModel):
    case_id: str
    payload: dict[str, Any]
    reviewer: str | None = None
    status: str = "draft"


class HumanReviewCreate(BaseModel):
    run_id: str
    case_id: str
    reviewer: str
    scores: dict[str, float]
    reason: str | None = None


class PromptCreate(BaseModel):
    prompt_id: str
    version: str
    content: str
    notes: str
    status: str = "active"


@router.get("/datasets")
def list_datasets():
    return get_container().governance.list_datasets()


@router.post("/datasets", status_code=201)
def create_dataset(payload: DatasetCreate, _admin: dict = Depends(_require_admin)):
    return get_container().governance.register_dataset(**payload.model_dump())


@router.get("/datasets/{dataset_id}/{version}/annotations")
def list_annotations(dataset_id: str, version: str, include_holdout_labels: bool = Query(False)):
    return get_container().governance.list_annotations(dataset_id, version, include_holdout_labels)


@router.post("/datasets/{dataset_id}/{version}/annotations", status_code=201)
def create_annotation(dataset_id: str, version: str, payload: AnnotationCreate, _admin: dict = Depends(_require_admin)):
    return get_container().governance.annotate(dataset_id, version, **payload.model_dump())


@router.get("/human-reviews/{run_id}")
def list_human_reviews(run_id: str):
    return get_container().governance.list_human_reviews(run_id)


@router.post("/human-reviews", status_code=201)
def create_human_review(payload: HumanReviewCreate, _admin: dict = Depends(_require_admin)):
    return get_container().governance.add_human_review(**payload.model_dump())


@router.get("/prompts")
def list_prompts():
    return get_container().governance.list_prompts()


@router.post("/prompts", status_code=201)
def create_prompt(payload: PromptCreate, _admin: dict = Depends(_require_admin)):
    return get_container().governance.create_prompt(**payload.model_dump())


@router.post("/judge/validate")
def validate_judge(payload: dict[str, Any]):
    return get_container().governance.validate_judge_result(payload).model_dump(mode="json")


@router.post("/thresholds")
def calculate_thresholds(rows: list[dict[str, Any]], thresholds: list[float] = Query(...)):
    return get_container().governance.threshold_experiment(rows, thresholds)


@router.post("/ablations/validate")
def validate_ablation(configuration: dict[str, list[Any]]):
    return get_container().governance.validate_ablation(configuration)
