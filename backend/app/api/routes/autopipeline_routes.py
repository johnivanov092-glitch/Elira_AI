"""API роуты для Autopipelines — cron-задачи Elira AI."""
import threading

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/api/pipelines", tags=["autopipelines"])

# Ручной прогон через UI делает полный ReAct-цикл агента (минуты на локальной
# 35B-модели). Держать на это время открытым HTTP-соединение нельзя — клиент
# таймаутит раньше, чем `run_pipeline_now` допишет `last_result`/логи, и юзер
# видит «нет ответа», хотя бэкенд отработал. Поэтому эндпойнт fire-and-forget:
# прогон уходит в фоновый поток (та же функция, тот же journal-lock, те же
# записи в БД), а UI поллит /list+/logs до появления свежего результата.
_active_runs: set[str] = set()
_active_runs_lock = threading.Lock()


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
    from app.application.autopipeline.runtime import list_pipelines
    return list_pipelines()


@router.post("/create")
def api_create(req: CreatePipelineRequest):
    from app.application.autopipeline.runtime import create_pipeline
    return create_pipeline(req.name, req.task_type, req.task_data, req.interval_minutes, req.enabled)


@router.get("/get/{pid}")
def api_get(pid: str):
    from app.application.autopipeline.runtime import get_pipeline
    return get_pipeline(pid)


@router.put("/update/{pid}")
def api_update(pid: str, req: UpdatePipelineRequest):
    from app.application.autopipeline.runtime import update_pipeline
    kwargs = {k: v for k, v in req.dict().items() if v is not None}
    return update_pipeline(pid, **kwargs)


@router.delete("/delete/{pid}")
def api_delete(pid: str):
    from app.application.autopipeline.runtime import delete_pipeline
    return delete_pipeline(pid)


@router.post("/run/{pid}")
def api_run_now(pid: str):
    """Запускает прогон в фоне и сразу возвращает управление.

    Сам прогон (`run_pipeline_now` → `_execute_task` → агент-цикл) НЕ меняется:
    он по-прежнему пишет `last_result`/`last_error`/логи по завершении. Здесь
    меняется только то, что мы не блокируем HTTP-ответ на всё время прогона.
    """
    from app.application.autopipeline.runtime import get_pipeline, run_pipeline_now

    p = get_pipeline(pid)
    if not p.get("ok"):
        return {"ok": False, "error": p.get("error", "Pipeline не найден")}

    with _active_runs_lock:
        if pid in _active_runs:
            # Прогон уже идёт — не плодим параллельные запуски того же pipeline.
            return {"ok": True, "started": False, "running": True, "pipeline_id": pid}
        _active_runs.add(pid)

    def _runner() -> None:
        try:
            run_pipeline_now(pid)
        finally:
            with _active_runs_lock:
                _active_runs.discard(pid)

    threading.Thread(target=_runner, name=f"pipeline-run-{pid}", daemon=True).start()
    return {"ok": True, "started": True, "running": True, "pipeline_id": pid}


@router.get("/logs/{pid}")
def api_logs(pid: str, limit: int = 20):
    from app.application.autopipeline.runtime import get_pipeline_logs
    return get_pipeline_logs(pid, limit)


@router.get("/scheduler/status")
def api_scheduler_status():
    from app.application.autopipeline.runtime import scheduler_status
    return scheduler_status()


@router.post("/scheduler/start")
def api_scheduler_start():
    from app.application.autopipeline.runtime import start_scheduler
    return start_scheduler()


@router.post("/scheduler/stop")
def api_scheduler_stop():
    from app.application.autopipeline.runtime import stop_scheduler
    return stop_scheduler()
