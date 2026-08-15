from fastapi import APIRouter, BackgroundTasks

from api.dependencies import get_container
from api.schemas.evaluation import EvaluationRun, EvaluationRunCreate


router = APIRouter(prefix="/evaluations", tags=["evaluation"])


@router.post("/runs", response_model=EvaluationRun, status_code=202)
def create_run(payload: EvaluationRunCreate, background_tasks: BackgroundTasks):
    run = get_container().evaluation.create(payload)
    background_tasks.add_task(get_container().evaluation.execute, run.run_id)
    return run


@router.get("/runs", response_model=list[EvaluationRun])
def list_runs():
    return get_container().evaluation.list()


@router.get("/runs/{run_id}", response_model=EvaluationRun)
def get_run(run_id: str):
    return get_container().evaluation.get(run_id)


@router.get("/runs/{run_id}/failures")
def get_failures(run_id: str):
    return {"run_id": run_id, "failed_cases": get_container().evaluation.get(run_id).failed_cases}
