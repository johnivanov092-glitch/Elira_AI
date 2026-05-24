from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.application.elira_patch import runtime as elira_patch_runtime

router = APIRouter(prefix="/api/elira/patch", tags=["elira-patch"])

ERROR_DETAILS = {
    "outside_root": (403, "Path is outside project root"),
    "blocked": (403, "Path points to blocked area"),
    "is_directory": (400, "Path points to directory"),
    "target_not_found": (404, "Target file not found"),
    "no_backup": (404, "Backup not found for rollback"),
    "no_items": (400, "No items provided"),
    "history_not_found": (404, "History item not found"),
}


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


def raise_runtime_error(error: str | None, error_path: str | None = None) -> None:
    if not error:
        return

    if error_path and error == "is_directory":
        raise HTTPException(status_code=400, detail=f"Directory path: {error_path}")
    if error_path and error == "target_not_found":
        raise HTTPException(status_code=404, detail=f"Target file not found: {error_path}")

    status_code, detail = ERROR_DETAILS.get(error, (400, error))
    raise HTTPException(status_code=status_code, detail=detail)


@router.post("/diff")
def diff_patch(payload: DiffPayload):
    return elira_patch_runtime.diff_patch(payload.path, payload.original, payload.updated)


@router.post("/apply")
def apply_patch(payload: ApplyPatchPayload):
    result, error = elira_patch_runtime.apply_patch(payload.path, payload.content)
    raise_runtime_error(error)
    return result


@router.post("/apply-batch")
def apply_batch(payload: BatchApplyPayload):
    items = [{"path": item.path, "content": item.content} for item in payload.items]
    result, error, error_path = elira_patch_runtime.apply_batch(items)
    raise_runtime_error(error, error_path)
    return result


@router.post("/rollback")
def rollback_patch(payload: RollbackPayload):
    result, error = elira_patch_runtime.rollback_patch(payload.path)
    raise_runtime_error(error)
    return result


@router.post("/verify")
def verify_patch(payload: VerifyPayload):
    result, error = elira_patch_runtime.verify_patch(payload.path, payload.content)
    raise_runtime_error(error)
    return result


@router.post("/verify-batch")
def verify_batch(payload: BatchVerifyPayload):
    items = [{"path": item.path, "content": item.content} for item in payload.items]
    result, error, error_path = elira_patch_runtime.verify_batch(items)
    raise_runtime_error(error, error_path)
    return result


@router.get("/history/list")
def list_history(path: str = "", limit: int = 50):
    return elira_patch_runtime.list_history(path, limit)


@router.get("/history/get")
def get_history_item(id: int):
    result, error = elira_patch_runtime.get_history_item(id)
    raise_runtime_error(error)
    return result
