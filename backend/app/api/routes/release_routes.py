from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app.application.runtime.release_status import (
    ReleaseConfirmationError, confirm_release, get_release_status, rollback_release,
)
from app.core.release_runtime import STATUS_PATH


router = APIRouter(tags=["release"])


class ReleaseConfirmationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(pattern=r"^[a-f0-9]{32}$")


class ReleaseRollbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    active_release_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,63}$")
    previous_release_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,63}$")


def _port(request: Request) -> int:
    server = request.scope.get("server")
    return int(server[1]) if server is not None else 8000


@router.get(STATUS_PATH)
def release_status(request: Request) -> dict:
    # The listening port is supplied by ASGI, never a caller's Host header or
    # query parameter. Discovery cannot redirect a test platform to production.
    return get_release_status(port=_port(request))


@router.post("/api/release/confirm")
def release_confirm(body: ReleaseConfirmationRequest, request: Request) -> dict:
    try:
        confirm_release(request_id=body.request_id, port=_port(request))
    except ReleaseConfirmationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return {"ok": True}


@router.post("/api/release/rollback")
def release_rollback(body: ReleaseRollbackRequest, request: Request) -> dict:
    try:
        rollback_release(active_release_id=body.active_release_id,
                         previous_release_id=body.previous_release_id, port=_port(request))
    except ReleaseConfirmationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return {"ok": True}
