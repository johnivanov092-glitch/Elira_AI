"""API роуты для Task Planner — персональный планировщик задач Elira AI."""
from fastapi import APIRouter
from pydantic import BaseModel

from app.application.planning.task_planner_service import (
    list_tasks,
    create_task,
    get_task,
    update_task,
    delete_task,
    task_stats,
)

router = APIRouter(prefix="/api/tasks", tags=["task_planner"])


class CreateTaskRequest(BaseModel):
    title: str
    description: str = ""
    category: str = "general"
    priority: str = "medium"
    due_date: str | None = None
    tags: list[str] | None = None


class UpdateTaskRequest(BaseModel):
    title: str | None = None
    description: str | None = None
    category: str | None = None
    priority: str | None = None
    status: str | None = None
    due_date: str | None = None
    tags: list[str] | None = None


@router.get("/list")
def api_list(status: str | None = None, category: str | None = None, limit: int = 100):
    return list_tasks(status=status, category=category, limit=limit)


@router.post("/create")
def api_create(req: CreateTaskRequest):
    return create_task(
        title=req.title,
        description=req.description,
        category=req.category,
        priority=req.priority,
        due_date=req.due_date,
        tags=req.tags,
    )


@router.get("/get/{tid}")
def api_get(tid: str):
    return get_task(tid)


@router.put("/update/{tid}")
def api_update(tid: str, req: UpdateTaskRequest):
    kwargs = {k: v for k, v in req.dict().items() if v is not None}
    return update_task(tid, **kwargs)


@router.delete("/delete/{tid}")
def api_delete(tid: str):
    return delete_task(tid)


@router.get("/stats")
def api_stats():
    return task_stats()
