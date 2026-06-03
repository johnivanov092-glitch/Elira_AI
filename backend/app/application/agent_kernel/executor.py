"""Unified ToolExecutor — single execution path for all agent sources.

Flow per call:
  1. Resolve ToolSpec. Fail-closed gates (each emits tool.invalid_spec, blocks
     before dispatch): no spec (unknown_toolspec), policy_classified=0
     (unclassified_tool), invalid permission tier, unknown scope in the spec.
  1e. Forbidden tier — a classified permission == "forbidden" is blocked immediately.
  2. Policy preflight via sandbox (rate-limit + context-budget + per-call allowed_tools).
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

    from app.application.tool_registry.store import (
        VALID_PERMISSIONS as _VALID_PERMS,
        VALID_SCOPES as _VALID_SCOPES,
    )

    tool_name = request.tool_name

    # 1. Resolve ToolSpec. The kernel is fail-closed: a tool with no spec, an
    # unclassified spec, an invalid permission tier, or an unknown scope in its
    # persisted spec must NEVER reach dispatch. Each such case emits
    # tool.invalid_spec so the integrity problem is auditable.
    spec = get_tool(tool_name)

    # 1a. No spec at all → unknown to the policy layer. Block before dispatch.
    if spec is None:
        _emit_invalid_spec(request, "unknown_toolspec")
        return ToolExecutionResult(
            status="blocked",
            output={
                "ok": False,
                "text": f"Tool '{tool_name}' has no registered ToolSpec — blocked (fail-closed).",
                "error": "unknown_toolspec",
            },
            error="unknown_toolspec",
        )

    max_chars: int = int(spec.get("max_output_chars") or 50000)

    # 1b. Unclassified spec → never runs until an admin classifies it (Tool API
    # PATCH sets policy_classified=true). Covers freshly-discovered plugin/MCP
    # tools and any row migrated in before classification existed.
    if not spec.get("policy_classified"):
        _emit_invalid_spec(request, "unclassified_tool")
        return ToolExecutionResult(
            status="blocked",
            output={
                "ok": False,
                "text": f"Tool '{tool_name}' is not policy-classified — blocked (fail-closed).",
                "error": "unclassified_tool",
            },
            error="unclassified_tool",
        )

    # 1b'. Disabled spec → not executable through the unified path either. Defense
    # in depth so a classified-but-disabled tool (e.g. an admin-classified plugin
    # left disabled, or a stale/disabled MCP tool) cannot run via the kernel.
    if not spec.get("enabled", True):
        _emit_blocked(request, f"tool '{tool_name}' is disabled")
        return ToolExecutionResult(
            status="blocked",
            output={
                "ok": False,
                "text": f"Tool '{tool_name}' is disabled and cannot be executed.",
                "error": "tool_disabled",
            },
            error="tool_disabled",
        )

    # 1c. Invalid permission tier → must never be treated as "auto".
    if spec.get("permission") not in _VALID_PERMS:
        _emit_invalid_spec(request, f"invalid_permission:{spec.get('permission')!r}")
        return ToolExecutionResult(
            status="blocked",
            output={
                "ok": False,
                "text": f"Tool '{tool_name}' has an invalid permission tier and cannot be executed.",
                "error": "invalid_permission",
            },
            error="invalid_permission",
        )

    # 1d. Unknown scope in the persisted spec → fail-closed (a corrupted or
    # forward-dated spec must not slip an unrecognized capability past the gate).
    _bad_scopes = [s for s in (spec.get("scopes") or []) if s not in _VALID_SCOPES]
    if _bad_scopes:
        _emit_invalid_spec(request, f"unknown_scope:{_bad_scopes}")
        return ToolExecutionResult(
            status="blocked",
            output={
                "ok": False,
                "text": f"Tool '{tool_name}' declares unknown scope(s) {_bad_scopes} — blocked (fail-closed).",
                "error": "unknown_scope",
            },
            error="unknown_scope",
        )

    # 1e. Forbidden tier — a classified, deliberate admin block. No approval possible.
    if spec.get("permission") == "forbidden":
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

    # 2. Policy preflight — rate-limit, context-budget, and per-call allowed_tools.
    # selected_tools=[tool_name] enforces the agent's allowed_tools list at tool-call
    # granularity. An empty allowed_tools grant means unrestricted, so default agents
    # are unaffected; an explicitly tool-restricted agent is blocked per call here.
    try:
        preflight_or_raise(
            agent_id=request.agent_id,
            num_ctx=0,
            selected_tools=[tool_name],
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

    # 2b. Scope enforcement (P9.2A2). A tool may run only if its declared scopes
    # are within the agent's granted scopes. An empty grant means "unrestricted"
    # (mirrors allowed_tools), so existing flows are unaffected until an agent is
    # explicitly scope-restricted.
    _tool_scopes = list((spec or {}).get("scopes") or [])
    if _tool_scopes:
        try:
            from app.application.monitoring import runtime as _mon
            _granted = {str(s) for s in ((_mon.get_agent_limit(request.agent_id) or {}).get("allowed_scopes") or [])}
        except Exception:
            _granted = set()
        if _granted:  # empty grant = unrestricted
            _missing = [s for s in _tool_scopes if s not in _granted]
            if _missing:
                _emit_blocked(request, f"scope_block:{_missing}")
                return ToolExecutionResult(
                    status="blocked",
                    output={
                        "ok": False,
                        "text": f"Tool '{tool_name}' requires scopes {_missing} not granted to agent '{request.agent_id}'.",
                        "error": "scope_block:" + ",".join(_missing),
                    },
                    error="scope_block",
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
        # A stable run_id is required so the approval can be matched on retry.
        # Block empty AND whitespace-only run_id uniformly.
        if not str(request.run_id or "").strip():
            _emit_blocked(request, "approval_requires_run_id")
            return ToolExecutionResult(
                status="blocked",
                output={
                    "ok": False,
                    "text": (
                        f"Tool '{tool_name}' requires approval but the request carries no run_id. "
                        "Retry with a stable run_id so the approval can be matched."
                    ),
                    "error": "approval_requires_run_id",
                },
                error="approval_requires_run_id",
            )

        import uuid as _uuid
        from app.application.monitoring import runtime as _mon

        _mon.expire_old_approvals()
        existing = _mon.find_approved_approval(
            tool_name=tool_name,
            agent_id=request.agent_id,
            source=request.source,
            run_id=request.run_id,
            project_scope_id=request.project_scope_id,
            args=request.args,
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

    # 4. Dispatch. In-process handlers get an OBSERVED deadline only — we time the
    # call and emit tool.timeout if it overran, but we do NOT pretend to cancel a
    # synchronous call. Hard timeouts live where they can be enforced: subprocess
    # (subprocess.run timeout) and MCP (per-request deadline).
    import time as _time
    _t0 = _time.monotonic()
    try:
        raw = dispatch_fn(tool_name, request.args)
    except Exception as exc:
        raw = {"ok": False, "text": f"ERROR: {exc}", "error": str(exc)}
    _elapsed = _time.monotonic() - _t0
    _budget = int((spec or {}).get("timeout_seconds") or 0)
    if _budget > 0 and _elapsed > _budget:
        _emit_timeout(request, _elapsed, _budget)

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
                "success": result.get("ok", True),
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


def _emit_timeout(req: ToolExecutionRequest, elapsed: float, budget: int) -> None:
    try:
        from app.application.event_bus import runtime as _eb
        _eb.emit_event(
            event_type="tool.timeout",
            payload={
                "tool_name": req.tool_name,
                "agent_id": req.agent_id,
                "source": req.source,
                "run_id": req.run_id,
                "elapsed_seconds": round(elapsed, 3),
                "budget_seconds": budget,
                "observed": True,
            },
        )
    except Exception:
        pass


def _emit_invalid_spec(req: ToolExecutionRequest, reason: str) -> None:
    """Audit a fail-closed block caused by a missing/invalid/unclassified ToolSpec.

    Distinct from sandbox.policy.blocked (a policy decision on a valid spec): this
    flags an integrity problem with the spec itself — the tool never reaches dispatch.
    """
    try:
        from app.application.event_bus import runtime as _eb
        _eb.emit_event(
            event_type="tool.invalid_spec",
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
