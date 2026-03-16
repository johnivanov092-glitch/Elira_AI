from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.services.safe_patch_engine_service import SafePatchEngineService

try:
    from app.services.run_trace_service import RunTraceService
except Exception:
    RunTraceService = None

router = APIRouter(prefix="/api/phase11", tags=["phase11"])

run_trace_service = RunTraceService() if RunTraceService else None
engine = SafePatchEngineService(project_root=".", run_trace_service=run_trace_service)


class PatchPreviewRequest(BaseModel):
    file_path: str = Field(..., min_length=1)
    new_content: str


class PatchApplyRequest(BaseModel):
    file_path: str = Field(..., min_length=1)
    new_content: str
    expected_old_sha256: str | None = None


class PatchVerifyRequest(BaseModel):
    file_path: str = Field(..., min_length=1)


class PatchRollbackRequest(BaseModel):
    backup_id: str = Field(..., min_length=1)


@router.get("/status")
def status():
    return {
        "status": "ok",
        "phase": 11,
        "engine": "safe_patch_engine",
    }


@router.post("/patch/preview")
def patch_preview(payload: PatchPreviewRequest):
    return engine.preview_patch(
        file_path=payload.file_path,
        new_content=payload.new_content,
    )


@router.post("/patch/apply")
def patch_apply(payload: PatchApplyRequest):
    return engine.apply_patch(
        file_path=payload.file_path,
        new_content=payload.new_content,
        expected_old_sha256=payload.expected_old_sha256,
    )


@router.post("/patch/rollback")
def patch_rollback(payload: PatchRollbackRequest):
    return engine.rollback_patch(payload.backup_id)


@router.post("/patch/verify")
def patch_verify(payload: PatchVerifyRequest):
    return engine.verify_patch(payload.file_path)


@router.get("/patch/backups")
def patch_backups(limit: int = 50):
    return engine.list_backups(limit=limit)
