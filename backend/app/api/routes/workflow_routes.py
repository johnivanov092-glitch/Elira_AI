from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

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
import app.application.workflows.engine as workflow_engine


router = APIRouter(prefix="/api/agent-os", tags=["agent-os"])


@router.post("/workflows", response_model=WorkflowTemplate, summary="Create workflow template")
def create_workflow(body: WorkflowTemplateCreate):
    try:
        return workflow_engine.create_workflow_template(body.model_dump())
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/workflows", response_model=WorkflowListResponse, summary="List workflow templates")
def list_workflows(
    include_disabled: bool = Query(False),
    source: str | None = Query(None),
):
    workflows, total = workflow_engine.list_workflow_templates(
        include_disabled=include_disabled,
        source=source,
    )
    return WorkflowListResponse(workflows=workflows, total=total)


@router.get("/workflows/{workflow_id}", response_model=WorkflowTemplate, summary="Get workflow template")
def get_workflow(workflow_id: str):
    workflow = workflow_engine.get_workflow_template(workflow_id)
    if not workflow:
        raise HTTPException(404, f"Workflow '{workflow_id}' not found")
    return workflow


@router.patch("/workflows/{workflow_id}", response_model=WorkflowTemplate, summary="Update workflow template")
def patch_workflow(workflow_id: str, body: WorkflowTemplateUpdate):
    try:
        return workflow_engine.update_workflow_template(workflow_id, body.model_dump(exclude_none=True))
    except ValueError as exc:
        if "not found" in str(exc).lower():
            raise HTTPException(404, str(exc)) from exc
        raise HTTPException(400, str(exc)) from exc


@router.delete("/workflows/{workflow_id}", summary="Delete workflow template")
def delete_workflow(workflow_id: str):
    return workflow_engine.delete_workflow_template(workflow_id)


@router.post("/workflow-runs", response_model=WorkflowRun, summary="Start workflow run")
def create_workflow_run(body: WorkflowRunCreate):
    try:
        return workflow_engine.start_workflow_run(
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
    runs, total = workflow_engine.list_workflow_runs(
        workflow_id=workflow_id,
        status=status,
        limit=limit,
        offset=offset,
    )
    return WorkflowRunListResponse(runs=runs, total=total)


@router.get("/workflow-runs/{run_id}", response_model=WorkflowRun, summary="Get workflow run")
def get_workflow_run(run_id: str):
    run = workflow_engine.get_workflow_run(run_id)
    if not run:
        raise HTTPException(404, f"Workflow run '{run_id}' not found")
    return run


@router.post("/workflow-runs/{run_id}/resume", response_model=WorkflowRun, summary="Resume paused workflow run")
def resume_workflow_run(run_id: str, body: WorkflowResumeRequest):
    try:
        return workflow_engine.resume_workflow_run(run_id, context_patch=body.context_patch)
    except ValueError as exc:
        if "not found" in str(exc).lower():
            raise HTTPException(404, str(exc)) from exc
        raise HTTPException(400, str(exc)) from exc


@router.post("/workflow-runs/{run_id}/cancel", response_model=WorkflowRun, summary="Cancel workflow run")
def cancel_workflow_run(run_id: str):
    try:
        return workflow_engine.cancel_workflow_run(run_id)
    except ValueError as exc:
        if "not found" in str(exc).lower():
            raise HTTPException(404, str(exc)) from exc
        raise HTTPException(400, str(exc)) from exc
