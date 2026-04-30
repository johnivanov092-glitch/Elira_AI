"""HTTP layer for the Elira patch endpoints.

All business/storage logic lives in
``app.application.elira_patch.runtime``; this module keeps only the
FastAPI router, Pydantic request models, and the thin delegating handlers
that translate runtime error kinds into HTTPException codes.
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.application.elira_patch import runtime as patch_runtime

router = APIRouter(prefix="/api/elira/patch", tags=["elira-patch"])

# ── error kind -> (status, detail) ──────────────────────────────────────────
_PATH_ERRORS = {
    "outside_root": (403, "Path is outside project root"),
    "blocked":      (403, "Path points to blocked area"),
    "is_directory": (400, "Path points to directory"),
    "not_found":    (404, "Target file not found"),
    "no_backup":    (404, "Backup not found for rollback"),
}


def _raise(kind: str, path: str = "") -> None:
    status, detail = _PATH_ERRORS.get(kind, (400, "Invalid path"))
    if path:
        detail = f"{detail}: {path}"
    raise HTTPException(status_code=status, detail=detail)


# ── Pydantic models ──────────────────────────────────────────────────────────

class ApplyPatchPayload(BaseModel):
    path: str = Field(min_length=1)
    content: str = ""


class RollbackPayload(BaseModel):
    path: str = Field(min_length=1)


class VerifyPayload(BaseModel):
    path: str = Field(min_length=1)
    content: Optional[str] = None


class DiffPayload(BaseModel):
    path: str = Field(min_length=1)
    original: str = ""
    updated: str = ""


class BatchApplyItem(BaseModel):
    path: str = Field(min_length=1)
    content: str = ""


class BatchApplyPayload(BaseModel):
    items: List[BatchApplyItem] = []


class BatchVerifyItem(BaseModel):
    path: str = Field(min_length=1)
    content: Optional[str] = None


class BatchVerifyPayload(BaseModel):
    items: List[BatchVerifyItem] = []


# ── Handlers ─────────────────────────────────────────────────────────────────

@router.post("/diff")
def diff_patch(payload: DiffPayload):
    return patch_runtime.compute_diff(payload.path, payload.original, payload.updated)


@router.post("/apply")
def apply_patch(payload: ApplyPatchPayload):
    result, err = patch_runtime.apply_patch(payload.path, payload.content)
    if err:
        _raise(err)
    return result


@router.post("/apply-batch")
def apply_batch(payload: BatchApplyPayload):
    if not payload.items:
        raise HTTPException(status_code=400, detail="No items provided")
    items = [{"path": i.path, "content": i.content} for i in payload.items]
    result, err, err_path = patch_runtime.batch_apply(items)
    if err:
        _raise(err, err_path or "")
    return result


@router.post("/rollback")
def rollback_patch(payload: RollbackPayload):
    result, err = patch_runtime.rollback_patch(payload.path)
    if err:
        _raise(err)
    return result


@router.post("/verify")
def verify_patch(payload: VerifyPayload):
    result, err = patch_runtime.verify_patch(payload.path, payload.content)
    if err:
        _raise(err)
    return result


@router.post("/verify-batch")
def verify_batch(payload: BatchVerifyPayload):
    if not payload.items:
        raise HTTPException(status_code=400, detail="No items provided")
    items = [{"path": i.path, "content": i.content} for i in payload.items]
    result, err, err_path = patch_runtime.batch_verify(items)
    if err:
        _raise(err, err_path or "")
    return result


@router.get("/history/list")
def list_history(path: str = "", limit: int = 50):
    return patch_runtime.list_history(path, limit)


@router.get("/history/get")
def get_history_item(id: int):
    result = patch_runtime.get_history_item(id)
    if result.get("status") == "not_found":
        raise HTTPException(status_code=404, detail="History item not found")
    return result
