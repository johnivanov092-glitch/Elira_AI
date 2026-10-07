"""One selected tool call and its existing Workflow execution branches.

The coordinator owns messages, evidence, accounting and terminal completion
fields. These generators publish live execution events and return one outcome;
they never select the next tool or model turn.
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Generator

from app.application.agent_kernel.executor import (
    ToolExecutionRequest,
    ToolExecutionResult,
    permission_mode_auto_approves,
    tool_args_sha256,
)
from app.application.code_agent.loop_helpers import _truncate
from app.application.code_agent.run_control import (
    begin_workflow_response,
    wait_workflow_response,
)
from app.core.redaction import redact_secrets


_LLM_HEARTBEAT_EVERY = 10.0
RUNTIME_WORKFLOW_STATUSES = frozenset({
    "needs_input", "needs_secret", "needs_elevation", "waiting_approval",
})


@dataclass(frozen=True)
class ExecutionOutcome:
    result: ToolExecutionResult | None
    terminal: dict[str, Any] | None = None


@dataclass(frozen=True)
class WorkflowOutcome:
    tool_text: str = ""
    response: dict[str, Any] | None = None
    cancelled: bool = False
    terminal: dict[str, Any] | None = None


def _cancelled_event(step: int) -> dict[str, Any]:
    return {
        "type": "done", "ok": False, "steps": step,
        "stop_reason": "cancelled", "error": "Cancelled by user",
    }


def _paused_event(
    step: int, status: str, request: dict[str, Any], response_id: str | None = None,
) -> dict[str, Any]:
    return {
        "type": "done", "ok": False, "steps": step,
        "stop_reason": "workflow_request", "status": status,
        **({"response_id": response_id} if response_id is not None else {}),
        "request": request, "error": None,
    }


def _exec_with_heartbeat(thunk, step, cancel_event: threading.Event | None = None):
    """Run a blocking tool call (thunk) in a daemon thread, yielding `heartbeat`
    events every _LLM_HEARTBEAT_EVERY seconds while it runs. This keeps progress
    observable during a long network scan, build or test without imposing a
    product deadline. The FINAL yielded item is
    {'__result__': <ToolExecutionResult>}; the caller passes heartbeats through
    and unwraps the result."""
    box: dict[str, Any] = {}
    finished = threading.Event()

    def _run() -> None:
        try:
            box["r"] = thunk()
        except BaseException as exc:  # noqa: BLE001 — re-raised in the generator
            box["e"] = exc
        finally:
            finished.set()

    threading.Thread(target=_run, daemon=True).start()
    # Poll finer than the heartbeat interval, else a tool that finishes between
    # polls (or a small interval) never triggers the elapsed-time check.
    _poll = min(1.0, max(0.02, _LLM_HEARTBEAT_EVERY / 2.0))
    _last = time.monotonic()
    while not finished.wait(timeout=_poll):
        if cancel_event is not None and cancel_event.is_set():
            yield {
                "__result__": ToolExecutionResult(
                    status="error",
                    output={
                        "ok": False,
                        "text": "Выполнение остановлено пользователем.",
                        "error": "cancelled_by_user",
                    },
                    error="cancelled_by_user",
                )
            }
            return
        _now = time.monotonic()
        if _now - _last >= _LLM_HEARTBEAT_EVERY:
            yield {"type": "heartbeat", "step": step}
            _last = _now
    if "e" in box:
        raise box["e"]
    yield {"__result__": box["r"]}


def run_tool(
    request: ToolExecutionRequest,
    *,
    dispatch_fn: Callable[..., Any],
    executor: Callable[..., ToolExecutionResult],
    step: int,
    cancel_event: threading.Event,
    pause_for_workflow_request: bool,
) -> Generator[dict[str, Any], None, ExecutionOutcome]:
    """Execute one prepared request; approved arguments remain unchanged."""
    name = request.tool_name
    parsed_args = request.args
    delay_tool_started = not (
        request.workflow_approved or permission_mode_auto_approves(request)
    )
    if not delay_tool_started:
        yield {
            "type": "tool_started", "step": step, "tool": name,
            "arguments": redact_secrets(parsed_args),
        }
    result = None
    for heartbeat in _exec_with_heartbeat(
        lambda: executor(request, dispatch_fn=dispatch_fn), step, cancel_event,
    ):
        if "__result__" in heartbeat:
            result = heartbeat["__result__"]
        else:
            yield heartbeat
    if result.status == "waiting_approval":
        raw_request = (result.output or {}).get("request")
        display_args = redact_secrets(parsed_args)
        workflow_request = (
            dict(raw_request) if isinstance(raw_request, dict) else {
                "kind": "approval",
                "message": (
                    f"Подтвердить действие {name} с аргументами: "
                    f"{json.dumps(display_args, ensure_ascii=False)}"
                ),
                "schema": {"x-elira-tool": {
                    "name": name, "arguments": display_args,
                    "args_sha256": tool_args_sha256(parsed_args),
                }},
                "sensitive": False,
            }
        )
        response_id = uuid.uuid4().hex
        if pause_for_workflow_request:
            yield {
                "type": "workflow_request", "step": step,
                "status": "waiting_approval", "response_id": response_id,
                "request": workflow_request,
            }
            return ExecutionOutcome(
                result,
                _paused_event(step, "waiting_approval", workflow_request, response_id),
            )
        begin_workflow_response(response_id)
        yield {
            "type": "workflow_request", "step": step,
            "status": "waiting_approval", "response_id": response_id,
            "request": workflow_request,
        }
        waited = yield from wait_workflow_response(
            response_id=response_id, cancel_event=cancel_event, step=step,
        )
        if waited.cancelled:
            return ExecutionOutcome(result, _cancelled_event(step))
        if str((waited.response or {}).get("action") or "") == "accept":
            request.workflow_approved = True
            if delay_tool_started:
                yield {
                    "type": "tool_started", "step": step, "tool": name,
                    "arguments": redact_secrets(parsed_args),
                }
            for heartbeat in _exec_with_heartbeat(
                lambda: executor(request, dispatch_fn=dispatch_fn), step, cancel_event,
            ):
                if "__result__" in heartbeat:
                    result = heartbeat["__result__"]
                else:
                    yield heartbeat
        else:
            result = ToolExecutionResult(
                status="rejected",
                output={
                    "ok": False,
                    "text": (
                        "Пользователь отклонил это действие. Не повторяй "
                        "вызов; скорректируй подход или заверши ход."
                    ),
                    "error": "workflow_request_declined",
                },
                error="workflow_request_declined",
            )
    return ExecutionOutcome(result)


def run_ask_user(
    parsed_args: dict[str, Any],
    *,
    step: int,
    cancel_event: threading.Event,
    pause_for_workflow_request: bool,
) -> Generator[dict[str, Any], None, WorkflowOutcome]:
    """Ask-user preserves accepted input before reporting a simultaneous Stop."""
    question = str(parsed_args.get("question") or "").strip()
    raw_opts = parsed_args.get("options")
    options = [str(o) for o in raw_opts][:8] if isinstance(raw_opts, list) else []
    input_rule: dict[str, Any] = {"type": "string", "title": question or "Ответ"}
    if options:
        input_rule["enum"] = options
    request = {
        "kind": "input", "message": question,
        "schema": {
            "type": "object", "properties": {"answer": input_rule},
            "required": ["answer"], "additionalProperties": False,
        },
        "sensitive": False,
    }
    if pause_for_workflow_request:
        yield {
            "type": "workflow_request", "step": step,
            "status": "needs_input", "request": request,
        }
        return WorkflowOutcome(terminal=_paused_event(step, "needs_input", request))
    response_id = uuid.uuid4().hex
    begin_workflow_response(response_id)
    yield {
        "type": "workflow_request", "step": step, "status": "needs_input",
        "response_id": response_id, "request": request,
    }
    waited = yield from wait_workflow_response(
        response_id=response_id, cancel_event=cancel_event, step=step,
        response_first=True,
    )
    if waited.cancelled and waited.response is None:
        return WorkflowOutcome(cancelled=True, terminal=_cancelled_event(step))
    text = "Workflow UI response: " + json.dumps(waited.response or {}, ensure_ascii=False)
    accepted = str((waited.response or {}).get("action") or "") == "accept"
    values = (waited.response or {}).get("values")
    answer = values.get("answer") if isinstance(values, dict) else None
    workflow_input = (
        redact_secrets({"request_id": response_id, "question": question, "answer": answer})
        if accepted and isinstance(answer, str) and answer.strip() else None
    )
    yield {
        "type": "tool_call", "step": step, "tool": "ask_user",
        "arguments": redact_secrets(parsed_args), "result": text, "ok": accepted,
        **({"workflow_input": workflow_input} if workflow_input is not None else {}),
    }
    if cancel_event.is_set():
        return WorkflowOutcome(text, waited.response, True, _cancelled_event(step))
    return WorkflowOutcome(text, waited.response)


def run_workflow_request(
    parsed_args: dict[str, Any],
    *,
    step: int,
    cancel_event: threading.Event,
    pause_for_workflow_request: bool,
) -> Generator[dict[str, Any], None, WorkflowOutcome]:
    """The model-selected input/secret/elevation branch keeps Stop priority."""
    kind = str(parsed_args.get("kind") or "").strip().lower()
    message = str(parsed_args.get("message") or "").strip()
    request_error = ""
    if kind not in {"input", "secret", "elevation"}:
        request_error = "workflow_request kind must be input, secret or elevation"
    request_schema = parsed_args.get("schema")
    if not isinstance(request_schema, dict):
        request_schema = {}
    if kind == "elevation":
        program = str(parsed_args.get("program") or "").strip()
        raw_args = parsed_args.get("args")
        elevation_args = [str(value) for value in raw_args] if isinstance(raw_args, list) else []
        if not program:
            request_error = "elevation request requires program"
        native_spec: dict[str, Any] = {"program": program, "args": elevation_args}
        cwd = str(parsed_args.get("cwd") or "").strip()
        if cwd:
            native_spec["cwd"] = cwd
        request_schema = {"x-elira-elevation": native_spec}
    if request_error:
        yield {
            "type": "tool_call", "step": step, "tool": "workflow_request",
            "arguments": redact_secrets(parsed_args), "result": request_error, "ok": False,
        }
        return WorkflowOutcome(tool_text=request_error)
    request = {
        "kind": kind, "message": message, "schema": request_schema,
        "sensitive": kind == "secret",
    }
    status = f"needs_{kind}"
    if pause_for_workflow_request:
        yield {
            "type": "workflow_request", "step": step, "status": status, "request": request,
        }
        return WorkflowOutcome(terminal=_paused_event(step, status, request))
    response_id = uuid.uuid4().hex
    begin_workflow_response(response_id)
    yield {
        "type": "workflow_request", "step": step, "status": status,
        "response_id": response_id, "request": request,
    }
    waited = yield from wait_workflow_response(
        response_id=response_id, cancel_event=cancel_event, step=step,
    )
    if waited.cancelled:
        return WorkflowOutcome(response=waited.response, cancelled=True, terminal=_cancelled_event(step))
    text = "Workflow UI response: " + json.dumps(waited.response or {}, ensure_ascii=False)
    yield {
        "type": "tool_call", "step": step, "tool": "workflow_request",
        "arguments": redact_secrets(parsed_args), "result": text,
        "ok": str((waited.response or {}).get("action") or "") == "accept",
    }
    return WorkflowOutcome(text, waited.response)


def run_runtime_workflow(
    parsed_args: dict[str, Any],
    tool_meta: dict[str, Any],
    *,
    step: int,
    cancel_event: threading.Event,
    pause_for_workflow_request: bool,
) -> Generator[dict[str, Any], None, WorkflowOutcome]:
    """Runtime requests expose the initial receipt before their Workflow wait."""
    status = str(tool_meta.get("status") or "").strip()
    raw_request = tool_meta.get("request")
    request = dict(raw_request) if isinstance(raw_request, dict) else {}
    kind = str(request.get("kind") or {
        "needs_input": "input", "needs_secret": "secret",
        "needs_elevation": "elevation", "waiting_approval": "approval",
    }[status]).strip()
    request.update({
        "kind": kind,
        "message": str(request.get("message") or "Runtime требует данные для продолжения."),
        "schema": dict(request.get("schema")) if isinstance(request.get("schema"), dict) else {},
        "sensitive": kind == "secret" or bool(request.get("sensitive", False)),
    })
    response_id = uuid.uuid4().hex
    yield {
        "type": "tool_call", "step": step, "tool": "runtime_control",
        "arguments": redact_secrets(parsed_args),
        "result": _truncate(str(tool_meta.get("text") or "")),
        "ok": False, "state_changed": False,
    }
    if pause_for_workflow_request:
        yield {
            "type": "workflow_request", "step": step, "status": status,
            "response_id": response_id, "request": request,
        }
        return WorkflowOutcome(terminal=_paused_event(step, status, request, response_id))
    begin_workflow_response(response_id)
    yield {
        "type": "workflow_request", "step": step, "status": status,
        "response_id": response_id, "request": request,
    }
    waited = yield from wait_workflow_response(
        response_id=response_id, cancel_event=cancel_event, step=step,
    )
    if waited.cancelled:
        return WorkflowOutcome(response=waited.response, cancelled=True, terminal=_cancelled_event(step))
    text = "Workflow UI response for runtime_control: " + json.dumps(waited.response or {}, ensure_ascii=False)
    return WorkflowOutcome(text, waited.response)
