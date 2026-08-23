from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, SecretStr

from app.application.workflows.db_path import get_workflow_db_path
from app.application.workflows.runtime import (
    cancel_workflow_run as _app_cancel_workflow_run,
    resume_workflow_run as _app_resume_workflow_run,
    start_workflow_run as _app_start_workflow_run,
)
from app.application.workflows.request_lifecycle import (
    get_request as _app_get_workflow_request,
    list_requests as _app_list_workflow_requests,
    resolve_request as _app_resolve_workflow_request,
)
from app.application.workflows.store import (
    create_workflow_template as _app_create_workflow_template,
    delete_workflow_template as _app_delete_workflow_template,
    get_workflow_run as _app_get_workflow_run,
    get_workflow_template as _app_get_workflow_template,
    init_db as _app_init_workflow_db,
    list_workflow_runs as _app_list_workflow_runs,
    list_workflow_templates as _app_list_workflow_templates,
    update_workflow_template as _app_update_workflow_template,
)
from app.schemas.workflow import (
    WorkflowListResponse,
    WorkflowResumeRequest,
    WorkflowRequest,
    WorkflowRequestListResponse,
    WorkflowRequestResolve,
    WorkflowRequestResolveResponse,
    WorkflowRun,
    WorkflowRunCreate,
    WorkflowRunListResponse,
    WorkflowTemplate,
    WorkflowTemplateCreate,
    WorkflowTemplateUpdate,
)


router = APIRouter(prefix="/api/agent-os", tags=["agent-os"])


class VaultCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    passphrase: SecretStr


class VaultUnlockRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    method: Literal["passphrase", "recovery"] = "passphrase"
    credential: SecretStr


class VaultSecretCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["password", "private_key", "token", "connection_string"]
    value: SecretStr
    asset_id: str | None = None
    lifecycle: Literal["temporary", "persistent"] = "persistent"


class VaultRotatePassphraseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    new_passphrase: SecretStr


class VaultPathRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str


class VaultMigrationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    secret_ref: str


def _workflow_db_path():
    db_path = get_workflow_db_path()
    _app_init_workflow_db(db_path=db_path)
    return db_path


def _raise_vault_http_error(exc: Exception) -> None:
    from app.infrastructure.it_ops import store as it_ops_store
    from app.infrastructure.secrets import vault

    if isinstance(exc, vault.VaultAuthenticationError):
        raise HTTPException(401, "vault credential is invalid or encrypted data was modified") from exc
    if isinstance(exc, vault.VaultLocked):
        raise HTTPException(423, "portable vault is locked") from exc
    if isinstance(exc, vault.VaultNotInitialized):
        raise HTTPException(409, "portable vault is not initialized") from exc
    if isinstance(exc, vault.SecretUnavailable):
        raise HTTPException(404, str(exc)) from exc
    if isinstance(exc, (vault.VaultFormatError, vault.VaultProvisioningError)):
        raise HTTPException(409, str(exc)) from exc
    if isinstance(exc, (vault.VaultError, ValueError)):
        raise HTTPException(400, str(exc)) from exc
    if isinstance(exc, it_ops_store.StoreUnavailable):
        raise HTTPException(503, "portable vault metadata store is unavailable") from exc
    raise exc


@router.get("/vault/status", summary="Get portable vault lifecycle state")
def get_vault_status():
    from app.infrastructure.secrets import vault

    try:
        return vault.status()
    except Exception as exc:
        _raise_vault_http_error(exc)


@router.post("/vault/create", summary="Create and unlock the portable vault")
def create_vault(body: VaultCreateRequest):
    from app.infrastructure.secrets import vault

    try:
        return vault.create(body.passphrase.get_secret_value())
    except Exception as exc:
        _raise_vault_http_error(exc)


@router.post("/vault/unlock", summary="Unlock the portable vault")
def unlock_vault(body: VaultUnlockRequest):
    from app.infrastructure.secrets import vault

    credential = body.credential.get_secret_value()
    try:
        if body.method == "recovery":
            return vault.unlock(recovery_key=credential)
        return vault.unlock(passphrase=credential)
    except Exception as exc:
        _raise_vault_http_error(exc)


@router.post("/vault/lock", summary="Lock the portable vault")
def lock_vault():
    from app.infrastructure.secrets import vault

    try:
        return vault.lock()
    except Exception as exc:
        _raise_vault_http_error(exc)


@router.post("/vault/secrets", summary="Write a secret and return only its opaque ref")
def create_vault_secret(body: VaultSecretCreateRequest):
    from app.infrastructure.secrets import vault

    try:
        secret_ref = vault.put_secret(
            kind=body.kind,
            value=body.value.get_secret_value(),
            asset_id=body.asset_id,
            lifecycle=body.lifecycle,
        )
        return {"ok": True, "secret_ref": secret_ref}
    except Exception as exc:
        _raise_vault_http_error(exc)


@router.post("/vault/rotate-passphrase", summary="Rotate portable vault passphrase")
def rotate_vault_passphrase(body: VaultRotatePassphraseRequest):
    from app.infrastructure.secrets import vault

    try:
        return vault.rotate_passphrase(body.new_passphrase.get_secret_value())
    except Exception as exc:
        _raise_vault_http_error(exc)


@router.post("/vault/rotate-recovery", summary="Rotate and return a new recovery key once")
def rotate_vault_recovery():
    from app.infrastructure.secrets import vault

    try:
        return vault.rotate_recovery_key()
    except Exception as exc:
        _raise_vault_http_error(exc)


@router.post("/vault/backup", summary="Write an encrypted portable vault backup")
def backup_vault(body: VaultPathRequest):
    from app.infrastructure.secrets import vault

    try:
        return vault.backup(body.path)
    except Exception as exc:
        _raise_vault_http_error(exc)


@router.post("/vault/restore", summary="Restore an encrypted portable vault backup")
def restore_vault(body: VaultPathRequest):
    from app.infrastructure.secrets import vault

    try:
        return vault.restore(body.path)
    except Exception as exc:
        _raise_vault_http_error(exc)


@router.post("/vault/migrate-wincred", summary="Explicitly migrate one legacy WinCred secret")
def migrate_vault_secret(body: VaultMigrationRequest):
    from app.infrastructure.secrets import vault

    try:
        return vault.migrate_legacy_secret(body.secret_ref.strip())
    except Exception as exc:
        _raise_vault_http_error(exc)


@router.post("/workflows", response_model=WorkflowTemplate, summary="Create workflow template")
def create_workflow(body: WorkflowTemplateCreate):
    try:
        return _app_create_workflow_template(
            db_path=_workflow_db_path(),
            template=body.model_dump(),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/workflows", response_model=WorkflowListResponse, summary="List workflow templates")
def list_workflows(
    include_disabled: bool = Query(False),
    source: str | None = Query(None),
):
    workflows, total = _app_list_workflow_templates(
        db_path=_workflow_db_path(),
        include_disabled=include_disabled,
        source=source,
    )
    return WorkflowListResponse(workflows=workflows, total=total)


@router.get("/workflows/{workflow_id}", response_model=WorkflowTemplate, summary="Get workflow template")
def get_workflow(workflow_id: str):
    workflow = _app_get_workflow_template(
        db_path=_workflow_db_path(),
        workflow_id=workflow_id,
    )
    if not workflow:
        raise HTTPException(404, f"Workflow '{workflow_id}' not found")
    return workflow


@router.patch("/workflows/{workflow_id}", response_model=WorkflowTemplate, summary="Update workflow template")
def patch_workflow(workflow_id: str, body: WorkflowTemplateUpdate):
    try:
        return _app_update_workflow_template(
            db_path=_workflow_db_path(),
            workflow_id=workflow_id,
            updates=body.model_dump(exclude_none=True),
        )
    except ValueError as exc:
        if "not found" in str(exc).lower():
            raise HTTPException(404, str(exc)) from exc
        raise HTTPException(400, str(exc)) from exc


@router.delete("/workflows/{workflow_id}", summary="Delete workflow template")
def delete_workflow(workflow_id: str):
    return _app_delete_workflow_template(
        db_path=_workflow_db_path(),
        workflow_id=workflow_id,
    )


@router.post("/workflow-runs", response_model=WorkflowRun, summary="Start workflow run")
def create_workflow_run(body: WorkflowRunCreate):
    try:
        return _app_start_workflow_run(
            db_path=_workflow_db_path(),
            workflow_id=body.workflow_id,
            workflow_input=body.input,
            context=body.context,
            trigger_source=body.trigger_source,
            permission_mode=body.permission_mode,
        )
    except ValueError as exc:
        if "not found" in str(exc).lower():
            raise HTTPException(404, str(exc)) from exc
        raise HTTPException(400, str(exc)) from exc


@router.get("/workflow-runs", response_model=WorkflowRunListResponse, summary="List workflow runs")
def list_workflow_runs(
    workflow_id: str | None = Query(None),
    status: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    runs, total = _app_list_workflow_runs(
        db_path=_workflow_db_path(),
        workflow_id=workflow_id,
        status=status,
        limit=limit,
        offset=offset,
    )
    return WorkflowRunListResponse(runs=runs, total=total)


@router.get("/workflow-runs/{run_id}", response_model=WorkflowRun, summary="Get workflow run")
def get_workflow_run(run_id: str):
    run = _app_get_workflow_run(
        db_path=_workflow_db_path(),
        run_id=run_id,
    )
    if not run:
        raise HTTPException(404, f"Workflow run '{run_id}' not found")
    return run


@router.get(
    "/workflow-runs/{run_id}/requests",
    response_model=WorkflowRequestListResponse,
    summary="List workflow requests for a run",
)
def list_workflow_run_requests(
    run_id: str,
    status: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    db_path = _workflow_db_path()
    _app_init_workflow_db(db_path=db_path)
    if not _app_get_workflow_run(db_path=db_path, run_id=run_id):
        raise HTTPException(404, f"Workflow run '{run_id}' not found")
    requests, total = _app_list_workflow_requests(
        db_path=db_path,
        run_id=run_id,
        status=status,
        limit=limit,
        offset=offset,
    )
    return WorkflowRequestListResponse(requests=requests, total=total)


@router.get(
    "/workflow-requests",
    response_model=WorkflowRequestListResponse,
    summary="List workflow requests",
)
def list_workflow_requests(
    status: str | None = Query("pending"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    db_path = _workflow_db_path()
    requests, total = _app_list_workflow_requests(
        db_path=db_path,
        status=status,
        limit=limit,
        offset=offset,
    )
    return WorkflowRequestListResponse(requests=requests, total=total)


@router.get(
    "/workflow-requests/{request_id}",
    response_model=WorkflowRequest,
    summary="Get workflow request",
)
def get_workflow_request(request_id: str):
    db_path = _workflow_db_path()
    _app_init_workflow_db(db_path=db_path)
    request = _app_get_workflow_request(db_path=db_path, request_id=request_id)
    if not request:
        raise HTTPException(404, f"Workflow request '{request_id}' not found")
    return request


@router.post(
    "/workflow-requests/{request_id}/resolve",
    response_model=WorkflowRequestResolveResponse,
    summary="Resolve workflow request",
)
def resolve_workflow_request(request_id: str, body: WorkflowRequestResolve):
    db_path = _workflow_db_path()
    _app_init_workflow_db(db_path=db_path)
    try:
        return _app_resolve_workflow_request(
            db_path=db_path,
            request_id=request_id,
            action=body.action,
            values=body.values,
        )
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        if "not found" in str(exc).lower():
            raise HTTPException(404, str(exc)) from exc
        raise HTTPException(400, str(exc)) from exc


@router.post("/workflow-runs/{run_id}/resume", response_model=WorkflowRun, summary="Resume paused workflow run")
def resume_workflow_run(run_id: str, body: WorkflowResumeRequest):
    try:
        return _app_resume_workflow_run(
            run_id,
            db_path=_workflow_db_path(),
            context_patch=body.context_patch,
        )
    except ValueError as exc:
        if "not found" in str(exc).lower():
            raise HTTPException(404, str(exc)) from exc
        raise HTTPException(400, str(exc)) from exc


@router.post("/workflow-runs/{run_id}/cancel", response_model=WorkflowRun, summary="Cancel workflow run")
def cancel_workflow_run(run_id: str):
    try:
        return _app_cancel_workflow_run(
            run_id,
            db_path=_workflow_db_path(),
        )
    except ValueError as exc:
        if "not found" in str(exc).lower():
            raise HTTPException(404, str(exc)) from exc
        raise HTTPException(400, str(exc)) from exc
