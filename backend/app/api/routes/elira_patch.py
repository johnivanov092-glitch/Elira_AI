from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.application.projects.patch_operations import (
    apply_file,
    build_diff_text,
    diff_stats,
    resolve_project_path,
    rollback_file,
    verify_file,
)
from app.infrastructure.db.patch_history_db import get_history_item, list_history

router = APIRouter(prefix="/api/elira/patch", tags=["elira-patch"])


class ApplyPatchPayload(BaseModel):
    path: str = Field(min_length=1)
    content: str


class RollbackPayload(BaseModel):
    path: str = Field(min_length=1)


class VerifyPayload(BaseModel):
    path: str = Field(min_length=1)
    content: Optional[str] = None


class DiffPayload(BaseModel):
    path: str = Field(min_length=1)
    original: str
    updated: str


class BatchApplyItem(BaseModel):
    path: str = Field(min_length=1)
    content: str


class BatchApplyPayload(BaseModel):
    items: List[BatchApplyItem]


class BatchVerifyItem(BaseModel):
    path: str = Field(min_length=1)
    content: Optional[str] = None


class BatchVerifyPayload(BaseModel):
    items: List[BatchVerifyItem]


@router.post("/diff")
def diff_patch(payload: DiffPayload):
    diff_text = build_diff_text(payload.path, payload.original, payload.updated)
    return {
        "status": "ok",
        "path": payload.path,
        "diff_text": diff_text,
        "stats": diff_stats(diff_text),
    }


@router.post("/apply")
def apply_patch(payload: ApplyPatchPayload):
    result = apply_file(payload.path, payload.content, action="apply")
    return {
        "status": "ok",
        "path": payload.path,
        "backup_path": result["backup_path"],
        "history_id": result["history_id"],
        "applied_at": datetime.utcnow().isoformat(),
    }


@router.post("/apply-batch")
def apply_batch(payload: BatchApplyPayload):
    if not payload.items:
        raise HTTPException(status_code=400, detail="No items provided")

    # Validate all paths before applying any
    for item in payload.items:
        target = resolve_project_path(item.path)
        if target.is_dir():
            raise HTTPException(status_code=400, detail=f"Directory path: {item.path}")
        if not target.exists():
            raise HTTPException(status_code=404, detail=f"Target file not found: {item.path}")

    results = []
    try:
        for item in payload.items:
            result = apply_file(item.path, item.content, action="apply-batch")
            results.append({
                "path": item.path,
                "history_id": result["history_id"],
                "backup_path": result["backup_path"],
            })
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        "status": "ok",
        "count": len(results),
        "items": results,
        "applied_at": datetime.utcnow().isoformat(),
    }


@router.post("/rollback")
def rollback_patch(payload: RollbackPayload):
    result = rollback_file(payload.path)
    return {
        "status": "ok",
        "path": payload.path,
        "history_id": result["history_id"],
        "rolled_back_at": datetime.utcnow().isoformat(),
    }


@router.post("/verify")
def verify_patch(payload: VerifyPayload):
    try:
        result = verify_file(payload.path, payload.content)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    line_count = result["line_count"]
    file_size = result["file_size"]
    changed = result["changed_vs_disk"]
    checks = [
        "File exists",
        "File readable as UTF-8",
        f"Lines: {line_count}",
        f"Size: {file_size} bytes",
        "Matches disk" if not changed else "Differs from disk version",
    ]
    return {
        "status": "ok",
        "path": payload.path,
        "changed_vs_disk": changed,
        "checks": checks,
        "stats": result["stats"],
        "diff_text": result["diff_text"],
        "verified_at": datetime.utcnow().isoformat(),
    }


@router.post("/verify-batch")
def verify_batch(payload: BatchVerifyPayload):
    if not payload.items:
        raise HTTPException(status_code=400, detail="No items provided")

    results = []
    total_added = total_removed = 0
    try:
        for item in payload.items:
            result = verify_file(item.path, item.content)
            stats = result["stats"]
            total_added += stats["added"]
            total_removed += stats["removed"]
            results.append({
                "path": item.path,
                "changed_vs_disk": result["changed_vs_disk"],
                "stats": stats,
                "diff_text": result["diff_text"],
            })
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        "status": "ok",
        "count": len(results),
        "items": results,
        "summary": {"added": total_added, "removed": total_removed},
        "verified_at": datetime.utcnow().isoformat(),
    }


@router.get("/history/list")
def patch_history_list(path: str = "", limit: int = 50):
    return {"items": list_history(path=path, limit=limit)}


@router.get("/history/get")
def patch_history_get(id: int):
    item = get_history_item(id)
    if not item:
        raise HTTPException(status_code=404, detail="History item not found")
    item["stats"] = diff_stats(item.get("diff_text") or "")
    return item
