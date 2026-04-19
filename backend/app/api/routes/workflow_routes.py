from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.application.workflows.runtime import (
    cancel_workflow_run as _app_cancel_workflow_run,
    resume_workflow_run as _app_resume_workflow_run,
    start_workflow_run as _app_start_workflow_run,
)
from app.application.workflows.store import (
    create_workflow_template as _app_create_workflow_template,
    delete_workflow_template as _app_delete_workflow_template,
    get_workflow_run as _app_get_workflow_run,
    get_workflow_template as _app_get_workflow_template,
    list_workflow_runs as _app_list_workflow_runs,
    list_workflow_templates as _app_list_workflow_templates,
    update_workflow_template as _app_update_workflow_template,
)
from app.schemas.workflow import (
    WorkflowListResponse,
    WorkflowResumeRequest,
    WorkflowRun,
    WorkflowRunCreate,
    WorkflowRunListResponse,
    WorkflowTemplate,
    WorkflowTemplateCreate,
    WorkflowTemplateUpdate,
)
from app.services import workflow_engine as workflow_engine_service


router = APIRouter(prefix="/api/agent-os", tags=["agent-os"])


def _workflow_db_path():
    return workflow_engine_service.DB_PATH


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
