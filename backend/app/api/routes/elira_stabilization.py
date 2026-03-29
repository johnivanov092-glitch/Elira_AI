from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field
from typing import List, Dict, Any
from datetime import datetime

router = APIRouter(prefix="/api/elira/stabilization", tags=["elira-stabilization"])


class PreflightPayload(BaseModel):
    phase20_queue_items: List[Dict[str, Any]] = []
    phase20_execution_state: Dict[str, Any] = {}
    phase21_run: Dict[str, Any] = {}
    staged_paths: List[str] = []


@router.post("/preflight")
def preflight(payload: PreflightPayload):
    queue_count = len(payload.phase20_queue_items or [])
    checkpoint_count = len((payload.phase20_execution_state or {}).get("checkpoints", []) or [])
    controller_steps = len((payload.phase21_run or {}).get("controller", {}).get("steps", []) or [])
    staged_count = len(payload.staged_paths or [])

    warnings = []
    if queue_count == 0:
        warnings.append("Preview queue is empty.")
    if checkpoint_count == 0:
        warnings.append("Execution state has no checkpoints.")
    if controller_steps == 0:
        warnings.append("Phase21 controller is not prepared.")
    if staged_count == 0:
        warnings.append("No staged files selected.")

    checks = [
        {"name": "queue", "ok": queue_count > 0, "value": queue_count},
        {"name": "checkpoints", "ok": checkpoint_count > 0, "value": checkpoint_count},
        {"name": "controller_steps", "ok": controller_steps > 0, "value": controller_steps},
        {"name": "staged_paths", "ok": staged_count > 0, "value": staged_count},
    ]

    return {
        "status": "ok",
        "ready": all(item["ok"] for item in checks),
        "checks": checks,
        "warnings": warnings,
        "created_at": datetime.utcnow().isoformat(),
    }
