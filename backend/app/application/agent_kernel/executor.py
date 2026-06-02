"""Unified ToolExecutor — single execution path for all agent sources.

Flow per call:
  1. Resolve ToolSpec (permission, timeout, max_output_chars).
  1b. Forbidden tier — permission == "forbidden" is blocked immediately.
  2. Policy preflight via sandbox (rate-limit + context-budget check).
  3. Approval gate — permission == "require_approval" requires a valid human
     approval (ApprovalStore in agent_monitor.db); otherwise the call returns
     "waiting_approval" until one is granted.
  4. Dispatch via caller-supplied dispatch_fn.
  5. Truncate text output to max_output_chars.
  6. Emit tool.executed / sandbox.policy.blocked / tool.approval_pending to the event bus.
  7. Return ToolExecutionResult (ok | error | blocked | forbidden | waiting_approval).

Design: dispatch_fn is injected by callers so that:
  - Chat/workflow passes a function backed by tool_registry database handlers.
  - Code-agent passes ToolRegistry.dispatch_raw (Builtin + SSH + MCP providers).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


DispatchFn = Callable[[str, dict[str, Any]], dict[str, Any]]

_TRUNCATION_SUFFIX = "\n[output truncated]"


@dataclass
class ToolExecutionRequest:
    run_id: str
    agent_id: str
    project_scope_id: str
    tool_name: str
    args: dict[str, Any]
    source: str
    workflow_id: str = ""
    step_id: str = ""


@dataclass
class ToolExecutionResult:
    status: str  # "ok" | "error" | "blocked" | "forbidden" | "waiting_approval"
    output: dict[str, Any]
    error: str | None = None


def execute_tool(
    request: ToolExecutionRequest,
    dispatch_fn: DispatchFn,
) -> ToolExecutionResult:
    """Execute one tool call through the unified policy and audit layer."""
    from app.application.tool_registry.runtime import get_tool
    from app.application.agent_registry.sandbox import SandboxPolicyError, preflight_or_raise

    tool_name = request.tool_name

    # 1. Resolve ToolSpec for limits (best-effort — fall back to safe defaults)
    spec = get_tool(tool_name)
    max_chars: int = int((spec or {}).get("max_output_chars") or 50000)

    # 1b. Forbidden tier — no approval possible, immediate block.
    if spec and spec.get("permission") == "forbidden":
        _emit_blocked(request, f"tool '{tool_name}' is forbidden")
        return ToolExecutionResult(
            status="forbidden",
            output={
                "ok": False,
                "text": f"Tool '{tool_name}' is permanently forbidden and cannot be executed.",
                "error": f"forbidden:{tool_name}",
            },
            error=f"forbidden:{tool_name}",
        )

    # 2. Policy preflight — rate-limit and context-budget check.
    # selected_tools is intentionally omitted: the allowed_tools sandbox list is a
    # session-level concern, already checked by callers (run_code_agent,
    # step_executor). Per-tool allowlisting via ToolSpec.permission is enforced
    # in Шаг 4 (ApprovalStore). Passing selected_tools here would block
    # native code-agent tools that are not in the tool_registry allowed_tools list.
    try:
        preflight_or_raise(
            agent_id=request.agent_id,
            num_ctx=0,
            run_id=request.run_id,
            workflow_id=request.workflow_id,
            step_id=request.step_id,
            route=request.source,
        )
    except SandboxPolicyError as exc:
        _emit_blocked(request, str(exc))
        return ToolExecutionResult(
            status="blocked",
            output={"ok": False, "text": f"ERROR: sandbox blocked '{tool_name}': {exc}", "error": str(exc)},
            error=str(exc),
        )

    # 3. Approval gate (implemented in Шаг 4/P1) — require_approval tools
    # need a valid human approval before dispatch.
    #
    # Exception: run_bash with a read-only allowlisted command auto-executes
    # without approval (P4 Шаг 11). This covers status/inspection commands
    # like `git status`, `ls`, `pytest --collect-only`, etc.
    _needs_approval = bool(spec and spec.get("permission") == "require_approval")
    if _needs_approval and tool_name == "run_bash":
        _cmd = str(request.args.get("command", "")).strip()
        if _cmd:
            try:
                from app.application.code_agent.tools import is_shell_safe
                if is_shell_safe(_cmd):
                    _needs_approval = False
            except Exception:
                pass  # conservative: keep approval requirement on import error

    if _needs_approval:
        import uuid as _uuid
        from app.application.monitoring import runtime as _mon

        _mon.expire_old_approvals()
        existing = _mon.find_approved_approval(
            tool_name=tool_name,
            agent_id=request.agent_id,
            run_id=request.run_id,
        )
        if existing:
            _mon.update_approval_status(existing["id"], status="used")
        else:
            approval = _mon.create_approval(
                id=_uuid.uuid4().hex,
                tool_name=tool_name,
                agent_id=request.agent_id,
                source=request.source,
                run_id=request.run_id,
                project_scope_id=request.project_scope_id,
                args=request.args,
            )
            _emit_approval_pending(request, approval["id"])
            # Best-effort Telegram notification — never blocks the agent
            try:
                from app.application.telegram.runtime import send_approval_notification
                send_approval_notification(approval)
            except Exception:
                pass
            return ToolExecutionResult(
                status="waiting_approval",
                output={
                    "ok": False,
                    "approval_id": approval["id"],
                    "text": (
                        f"Tool '{tool_name}' requires approval. "
                        f"Approve at /api/agent-os/approvals/{approval['id']}/approve"
                    ),
                    "error": f"waiting_approval:{approval['id']}",
                },
                error=f"waiting_approval:{approval['id']}",
            )

    # 4. Dispatch
    try:
        raw = dispatch_fn(tool_name, request.args)
    except Exception as exc:
        raw = {"ok": False, "text": f"ERROR: {exc}", "error": str(exc)}

    if not isinstance(raw, dict):
        raw = {"text": str(raw)}

    # Normalise: callers expect a "text" key for LLM feedback
    if "text" not in raw:
        import json as _json
        raw = {**raw, "text": _json.dumps(raw, ensure_ascii=False)}

    # 5. Truncate text output
    text = raw.get("text", "")
    if isinstance(text, str) and len(text) > max_chars:
        raw = {**raw, "text": text[:max_chars] + _TRUNCATION_SUFFIX}

    # 6. Emit audit event
    status = "ok" if raw.get("ok", True) else "error"
    _emit_executed(request, raw, status)

    return ToolExecutionResult(
        status=status,
        output=raw,
        error=raw.get("error") if status == "error" else None,
    )


def _emit_executed(req: ToolExecutionRequest, result: dict, status: str) -> None:
    try:
        from app.application.event_bus import runtime as _eb
        _eb.emit_event(
            event_type="tool.executed",
            payload={
                "tool_name": req.tool_name,
                "agent_id": req.agent_id,
                "source": req.source,
                "project_scope_id": req.project_scope_id,
                "run_id": req.run_id,
                "workflow_id": req.workflow_id,
                "step_id": req.step_id,
                "status": status,
                "ok": result.get("ok", True),
                "error": result.get("error"),
            },
        )
    except Exception:
        pass


def _emit_approval_pending(req: ToolExecutionRequest, approval_id: str) -> None:
    try:
        from app.application.event_bus import runtime as _eb
        _eb.emit_event(
            event_type="tool.approval_pending",
            payload={
                "tool_name": req.tool_name,
                "agent_id": req.agent_id,
                "source": req.source,
                "project_scope_id": req.project_scope_id,
                "run_id": req.run_id,
                "approval_id": approval_id,
            },
        )
    except Exception:
        pass


def _emit_blocked(req: ToolExecutionRequest, reason: str) -> None:
    try:
        from app.application.event_bus import runtime as _eb
        _eb.emit_event(
            event_type="sandbox.policy.blocked",
            payload={
                "tool_name": req.tool_name,
                "agent_id": req.agent_id,
                "source": req.source,
                "project_scope_id": req.project_scope_id,
                "run_id": req.run_id,
                "reason": reason,
            },
        )
    except Exception:
        pass
