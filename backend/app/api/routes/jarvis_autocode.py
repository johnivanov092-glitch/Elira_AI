from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/jarvis/autocode", tags=["jarvis-autocode"])


class AutoCodeRequest(BaseModel):
    path: str = Field(..., min_length=1)
    content: str = ""
    goal: str = Field(..., min_length=1)
    max_steps: int = Field(2, ge=1, le=5)


def _make_patch(content: str, goal: str) -> str:
    marker = (
        "\n\n# AUTO-CODING NOTE\n"
        f"# Goal: {goal.strip()}\n"
        "# Review this patch before applying.\n"
    )
    if marker.strip() in content:
        return content
    return (content or "").rstrip() + marker


@router.post("/suggest")
async def suggest_patch(payload: AutoCodeRequest) -> Dict[str, Any]:
    if not payload.path.strip():
        raise HTTPException(status_code=400, detail="path is required")

    patch = _make_patch(payload.content, payload.goal)
    return {
        "ok": True,
        "summary": "Generated bounded patch proposal. Review before apply.",
        "steps_used": min(payload.max_steps, 1),
        "patch": patch,
        "path": payload.path,
    }


@router.post("/loop")
async def run_loop(payload: AutoCodeRequest) -> Dict[str, Any]:
    current = payload.content
    steps = []
    limit = min(payload.max_steps, 5)

    for index in range(limit):
        next_content = _make_patch(current, f"{payload.goal} [step {index + 1}]")
        changed = next_content != current
        steps.append(
            {
                "step": index + 1,
                "changed": changed,
                "message": "proposal built",
            }
        )
        current = next_content
        if changed:
            break

    return {
        "ok": True,
        "path": payload.path,
        "steps": steps,
        "final_patch": current,
    }
