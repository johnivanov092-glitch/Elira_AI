from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List
from uuid import uuid4

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/api/jarvis/run-history", tags=["jarvis-run-history"])

RUNS: List[Dict[str, Any]] = []


class RunCreateRequest(BaseModel):
    path: str = ""
    status: str = "previewed"
    goal: str = ""
    events: List[Dict[str, Any]] = []


class RunUpdateRequest(BaseModel):
    run_id: str
    status: str = ""
    event: Dict[str, Any] | None = None


@router.get("/list")
async def list_runs() -> Dict[str, Any]:
    return {"items": RUNS[:100]}


@router.post("/create")
async def create_run(payload: RunCreateRequest) -> Dict[str, Any]:
    run_id = str(uuid4())
    item = {
        "id": run_id,
        "path": payload.path,
        "goal": payload.goal,
        "status": payload.status,
        "timestamp": datetime.utcnow().isoformat(),
        "events": payload.events or [],
    }
    RUNS.insert(0, item)
    return {"ok": True, "run_id": run_id, "item": item}


@router.post("/update")
async def update_run(payload: RunUpdateRequest) -> Dict[str, Any]:
    for item in RUNS:
        if item["id"] == payload.run_id:
            if payload.status:
                item["status"] = payload.status
            if payload.event:
                item.setdefault("events", []).append(payload.event)
            return {"ok": True, "item": item}
    return {"ok": False, "message": "run not found"}
