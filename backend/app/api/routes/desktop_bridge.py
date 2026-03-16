from __future__ import annotations

import os
import platform
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel, Field


router = APIRouter(prefix="/api/desktop", tags=["desktop-bridge"])


class OpenProjectRequest(BaseModel):
    project_path: str = Field(..., min_length=1)


@router.get("/handshake")
def desktop_handshake():
    return {
        "status": "ok",
        "app": "Jarvis Work",
        "mode": "desktop-ready",
        "platform": platform.platform(),
        "cwd": str(Path.cwd()),
    }


@router.get("/workspace")
def desktop_workspace():
    return {
        "status": "ok",
        "workspace": {
            "name": "Jarvis Work",
            "sections": [
                "Chat",
                "Agents",
                "Runs",
                "Timeline",
                "Memory",
                "Library",
                "Settings",
            ],
            "desktop": True,
        },
    }


@router.post("/open-project")
def open_project(payload: OpenProjectRequest):
    path = Path(payload.project_path).expanduser().resolve()
    return {
        "status": "ok" if path.exists() else "not_found",
        "project_path": str(path),
        "exists": path.exists(),
        "is_dir": path.is_dir(),
    }
