"""API роуты для Autopipelines — cron-задачи Elira AI."""
from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/api/pipelines", tags=["autopipelines"])


class CreatePipelineRequest(BaseModel):
    name: str
    task_type: str = "prompt"
    task_data: dict = {}
    interval_minutes: int = 60
    enabled: bool = True


class UpdatePipelineRequest(BaseModel):
    name: str | None = None
    task_type: str | None = None
    task_data: dict | None = None
    interval_minutes: int | None = None
    enabled: bool | None = None


@router.get("/list")
def api_list():
    from app.application.autopipeline.autopipeline_service import list_pipelines
    return list_pipelines()


@router.post("/create")
def api_create(req: CreatePipelineRequest):
    from app.application.autopipeline.autopipeline_service import create_pipeline
    return create_pipeline(req.name, req.task_type, req.task_data, req.interval_minutes, req.enabled)


@router.get("/get/{pid}")
def api_get(pid: str):
    from app.application.autopipeline.autopipeline_service import get_pipeline
    return get_pipeline(pid)


@router.put("/update/{pid}")
def api_update(pid: str, req: UpdatePipelineRequest):
    from app.application.autopipeline.autopipeline_service import update_pipeline
    kwargs = {k: v for k, v in req.dict().items() if v is not None}
    return update_pipeline(pid, **kwargs)


@router.delete("/delete/{pid}")
def api_delete(pid: str):
    from app.application.autopipeline.autopipeline_service import delete_pipeline
    return delete_pipeline(pid)


@router.post("/run/{pid}")
def api_run_now(pid: str):
    from app.application.autopipeline.autopipeline_service import run_pipeline_now
    return run_pipeline_now(pid)


@router.get("/logs/{pid}")
def api_logs(pid: str, limit: int = 20):
    from app.application.autopipeline.autopipeline_service import get_pipeline_logs
    return get_pipeline_logs(pid, limit)


@router.get("/scheduler/status")
def api_scheduler_status():
    from app.application.autopipeline.autopipeline_service import scheduler_status
    return scheduler_status()


@router.post("/scheduler/start")
def api_scheduler_start():
    from app.application.autopipeline.autopipeline_service import start_scheduler
    return start_scheduler()


@router.post("/scheduler/stop")
def api_scheduler_stop():
    from app.application.autopipeline.autopipeline_service import stop_scheduler
    return stop_scheduler()
