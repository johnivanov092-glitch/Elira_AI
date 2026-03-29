from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field
from typing import List
from datetime import datetime

router = APIRouter(prefix="/api/elira/phase20", tags=["elira-phase20-queue"])


class PreviewQueuePayload(BaseModel):
    goal: str = Field(min_length=1)
    targets: List[str] = []


@router.post("/preview-queue")
def preview_queue(payload: PreviewQueuePayload):
    items = []
    for index, path in enumerate(payload.targets):
        items.append({
            "order": index + 1,
            "path": path,
            "status": "queued",
            "mode": "preview",
        })

    return {
        "status": "ok",
        "goal": payload.goal,
        "count": len(items),
        "items": items,
        "created_at": datetime.utcnow().isoformat(),
    }
