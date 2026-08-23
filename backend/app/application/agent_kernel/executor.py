"""Unified ToolExecutor — single execution path for all agent sources.

Flow per call:
  1. Resolve ToolSpec when available. Registry metadata controls presentation and
     output sizing; it is not a second authorization layer.
  2. Workflow permission — the selected UI mode either authorizes the exact call
     or returns a structured request for that UI. There is no second
     approval database or provider-specific approval channel.
  3. Dispatch via caller-supplied dispatch_fn.
  4. Truncate text output to max_output_chars.
  5. Emit tool.executed to the event bus.
  6. Return ToolExecutionResult (ok | error | waiting_approval).

Design: dispatch_fn is injected by callers so that:
  - Chat/workflow passes a function backed by tool_registry database handlers.
  - Code-agent passes ToolRegistry.dispatch_raw (Builtin + SSH + MCP providers).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
from dataclasses import dataclass
from typing import Any, Callable

from app.application.agent_kernel.execution_context import (
    reset_execution_channel,
    reset_permission_mode,
    set_execution_channel,
    set_permission_mode,
)

DispatchFn = Callable[[str, dict[str, Any]], dict[str, Any]]

_TRUNCATION_SUFFIX = "\n[output truncated]"
logger = logging.getLogger(__name__)


def tool_args_sha256(args: dict[str, Any]) -> str:
    """Stable exact-call digest; raw arguments never need to enter Workflow state."""
    encoded = json.dumps(
        args,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def workflow_approval_matches(
    approval: dict[str, Any] | None,
    tool_name: str,
    args: dict[str, Any],
) -> bool:
    """Match one approved call without persisting its unredacted arguments."""
    if not isinstance(approval, dict):
        return False
    if str(approval.get("name") or "") != str(tool_name or ""):
        return False
    expected_digest = str(approval.get("args_sha256") or "")
    if expected_digest:
        return hmac.compare_digest(expected_digest, tool_args_sha256(args))
    # Compatibility with requests persisted before digest-based matching.
    return approval.get("arguments") == args


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
    permission_mode: str = "ask"
    workflow_approved: bool = False


@dataclass
class ToolExecutionResult:
    status: str  # "ok" | "error" | "rejected" | "waiting_approval"
    output: dict[str, Any]
    error: str | dict[str, Any] | None = None


def permission_mode_auto_approves(request: ToolExecutionRequest) -> bool:
    """Return whether the Workflow UI permission already authorizes this call.

    ``bypass`` is the workflow run's blanket product-level authorization. It
    does not manufacture a Windows token, but it removes internal approval,
    activation, scope, asset and risk-classification gates.
    """
    if request.workflow_approved:
        return True
    mode = str(request.permission_mode or "ask").strip().lower()
    if mode == "bypass":
        # Bypass is an explicit blanket authorization from Workflow UI. Its
        # meaning must not depend on registry metadata or a classifier import.
        return True
    try:
        from app.application.tool_registry.runtime import get_tool
        from app.application.agent_kernel.impact_policy import (
            AUTO,
            decide_approval,
            evidence_for_tool_call,
            tool_call_is_change,
        )

        spec = get_tool(request.tool_name)
        is_change = True if spec is None else bool(spec.get("side_effect", False))
        if request.tool_name == "runtime_control" or "__" in request.tool_name:
            is_change = tool_call_is_change(request.tool_name, request.args)
        channel = (
            "remote"
            if request.agent_id == "telegram" or request.source == "telegram"
            else "local"
        )
        return decide_approval(
            mode,
            channel,
            evidence_for_tool_call(request.tool_name, request.args),
            is_change=is_change,
        ) == AUTO
    except Exception:
        logger.warning("permission mode classification failed", exc_info=True)
        return False


def execute_tool(
    request: ToolExecutionRequest,
    dispatch_fn: DispatchFn,
) -> ToolExecutionResult:
    """Execute one tool call through the unified policy and audit layer."""
    from app.application.tool_registry.runtime import get_tool
    tool_name = request.tool_name

    # ToolSpec is presentation/audit metadata, not an authorization boundary.
    # Unknown and legacy-disabled/classified tools still reach the single Workflow
    # permission decision. Handler/runtime validation remains the source of truth.
    spec = get_tool(tool_name)
    if spec is None:
        spec = {
            "side_effect": True,
            "max_output_chars": 50000,
        }

    max_chars: int = int(spec.get("max_output_chars") or 50000)
    # The only product authorization gate is the Workflow selector. ToolSpec's
    # legacy permission/scopes/classification columns are ignored here.
    _needs_approval = not (
        request.workflow_approved or permission_mode_auto_approves(request)
    )

    if _needs_approval:
        from app.core.redaction import redact_secrets

        display_args = redact_secrets(request.args)
        request_spec = {
            "kind": "approval",
            "message": (
                f"Подтвердить действие {tool_name} с аргументами: "
                f"{json.dumps(display_args, ensure_ascii=False)}"
            ),
            "schema": {
                "x-elira-tool": {
                    "name": tool_name,
                    "arguments": display_args,
                    "args_sha256": tool_args_sha256(request.args),
                },
            },
            "sensitive": False,
        }
        return ToolExecutionResult(
            status="waiting_approval",
            output={
                "ok": False,
                "request": request_spec,
                "text": (
                    f"Действие '{tool_name}' ожидает решения в Workflow UI."
                ),
                "error": "waiting_approval",
            },
            error="waiting_approval",
        )

    # Dispatch runs in the agent loop's heartbeat worker. The runtime never stops
    # it on a product deadline; the Workflow Stop signal is the only run-level
    # termination control. Individual transports may still report their own I/O
    # failures when the OS/provider cannot complete an operation.
    _result_box: dict[str, Any] = {}

    def _runner() -> None:
        # Bind run_id on THIS worker thread so a cancellable tool (run_bash)
        # can register its live OS process against the run. The Stop route then
        # reaches in and kills it directly — a daemon worker thread cannot be
        # interrupted otherwise. Best-effort: tools that don't use it are
        # unaffected, and a missing helper must never break dispatch.
        _run_token = None
        _channel_token = None
        _permission_token = None
        try:
            from app.application.code_agent.tools import set_current_run_id
            _run_token = set_current_run_id(request.run_id)
        except Exception:
            _run_token = None
        try:
            _channel_token = set_execution_channel(
                "remote" if request.agent_id == "telegram" else "local"
            )
            _permission_token = set_permission_mode(request.permission_mode)
            _result_box["raw"] = dispatch_fn(tool_name, request.args)
        except Exception as exc:  # noqa: BLE001 — surfaced to the model as tool error
            _result_box["raw"] = {"ok": False, "text": f"ERROR: {exc}", "error": str(exc)}
        finally:
            if _permission_token is not None:
                try:
                    reset_permission_mode(_permission_token)
                except Exception:
                    pass
            if _channel_token is not None:
                try:
                    reset_execution_channel(_channel_token)
                except Exception:
                    pass
            if _run_token is not None:
                try:
                    from app.application.code_agent.tools import reset_current_run_id
                    reset_current_run_id(_run_token)
                except Exception:
                    pass

    _runner()

    raw = _result_box.get("raw", {"ok": False, "text": "ERROR: tool returned no result", "error": "no_result"})

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
    except Exception as exc:
        logger.debug("tool.executed event emission failed", exc_info=exc)
