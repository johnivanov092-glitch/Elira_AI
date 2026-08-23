"""
Workflow step execution: input resolution, template rendering, and individual step dispatch.

Extracted from workflow_engine.py -- handles the inner loop of workflow execution:
resolving input expressions (JSONPath-like), rendering prompt templates,
and dispatching agent or tool steps.
"""
from __future__ import annotations

import json
import hashlib
from string import Formatter
from typing import Any, Callable

from app.application.workflows.events import emit_workflow_event
from app.application.monitoring.runtime import WORKFLOW_ENGINE_AGENT_ID
from app.application.agent_kernel.executor import workflow_approval_matches

STEP_SUCCESS = "on_success"
STEP_FAILURE = "on_failure"


def _resolve_path(data: Any, path: str) -> Any:
    current = data
    for part in path.split("."):
        if not part:
            continue
        if isinstance(current, dict):
            current = current.get(part)
        else:
            return None
    return current


def _resolve_input_expression(
    expression: Any,
    *,
    workflow_input: dict[str, Any],
    context: dict[str, Any],
    step_results: dict[str, Any],
) -> Any:
    if not isinstance(expression, str):
        return expression

    if expression.startswith("$.input."):
        return _resolve_path(workflow_input, expression[len("$.input."):])
    if expression == "$.input":
        return workflow_input
    if expression.startswith("$.context."):
        return _resolve_path(context, expression[len("$.context."):])
    if expression == "$.context":
        return context
    if expression.startswith("$.steps."):
        return _resolve_path(step_results, expression[len("$.steps."):])
    if expression == "$.steps":
        return step_results
    return expression


def _map_step_inputs(
    step: dict[str, Any],
    *,
    workflow_input: dict[str, Any],
    context: dict[str, Any],
    step_results: dict[str, Any],
) -> dict[str, Any]:
    mapped: dict[str, Any] = {}
    for key, expr in (step.get("input_map") or {}).items():
        mapped[key] = _resolve_input_expression(
            expr,
            workflow_input=workflow_input,
            context=context,
            step_results=step_results,
        )
    return mapped


def _stringify_template_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, indent=2)
    return str(value)


class _SafeFormatDict(dict):
    def __missing__(self, key: str) -> str:
        return ""


def _render_prompt_template(template: str, values: dict[str, Any]) -> str:
    prepared = {key: _stringify_template_value(value) for key, value in values.items()}
    required = {field_name for _, field_name, _, _ in Formatter().parse(template) if field_name}
    for field in required:
        prepared.setdefault(field, "")
    return template.format_map(_SafeFormatDict(prepared))


def _determine_profile_name(agent_id: str, config: dict[str, Any]) -> str:
    profile_name = str(config.get("profile_name", "")).strip()
    if profile_name:
        return profile_name

    fallback_map = {
        "builtin-universal": "Универсальный",
        "builtin-researcher": "Исследователь",
        "builtin-programmer": "Программист",
        "builtin-analyst": "Аналитик",
        "builtin-socrat": "Сократ",
        "builtin-orchestrator": "Универсальный",
        "builtin-reviewer": "Аналитик",
    }
    return fallback_map.get(agent_id, "Универсальный")


def _execute_agent_step(
    step: dict[str, Any],
    mapped_inputs: dict[str, Any],
    run_context: dict[str, Any],
    run_id: str,
    permission_mode: str = "ask",
) -> dict[str, Any]:
    from app.application.chat.runtime import run_agent

    config = step.get("config", {}) or {}
    prompt_template = str(config.get("prompt_template", "")).strip()
    prompt = _render_prompt_template(prompt_template, mapped_inputs) if prompt_template else _stringify_template_value(mapped_inputs)
    resolutions = run_context.get("_workflow_request_resolutions", {})
    resolution = (
        resolutions.get(str(step.get("id", "")))
        if isinstance(resolutions, dict)
        else None
    )
    if isinstance(resolution, dict):
        safe_resolution = {
            "action": str(resolution.get("action") or "accept"),
            "values": (
                resolution.get("values")
                if isinstance(resolution.get("values"), dict)
                else {}
            ),
        }
        prompt = (
            f"{prompt}\n\n"
            "----- WORKFLOW UI RESPONSE -----\n"
            "The Workflow UI resolved your previous request. Continue the same "
            "step. For an approved tool action, repeat the intended tool call with "
            "the same arguments; the Workflow resolution authorizes that exact "
            "call once. For "
            "input use the returned values. For a secret use only secret_ref. An "
            "elevated command has already been executed by the native UI bridge; "
            "use its result and do not repeat it without elevation.\n"
            f"{json.dumps(safe_resolution, ensure_ascii=False, indent=2)}"
        )
    workflow_approval = (
        resolution.get("approved_tool")
        if isinstance(resolution, dict)
        and isinstance(resolution.get("approved_tool"), dict)
        else None
    )
    # P9.3: fall back to the "auto" sentinel (not a hardcoded model) so run_agent's
    # shared profile routing engages; an explicit step/context model is preserved.
    model_name = str(config.get("model_name") or run_context.get("model_name") or "auto")
    profile_name = _determine_profile_name(str(step.get("agent_id", "")), config)
    # Thread the selected project folder + context window from the run context so the
    # agent's file tools scope to the user's project (not the default workspace) and
    # the full production context window is used. Only forward when present/positive
    # so run_agent's defaults (default project root, DEFAULT_NUM_CTX) stay intact.
    extra_kwargs: dict[str, Any] = {}
    project_root = run_context.get("project_root")
    if project_root:
        extra_kwargs["project_root"] = str(project_root)
    num_ctx = run_context.get("num_ctx")
    if isinstance(num_ctx, int) and num_ctx > 0:
        extra_kwargs["num_ctx"] = num_ctx
    reasoning_effort = str(run_context.get("reasoning_effort") or "none")
    stable_run_key = f"{run_id}:{step.get('id', '')}".encode("utf-8")
    code_agent_run_id = f"wf-{hashlib.sha256(stable_run_key).hexdigest()[:40]}"
    result = run_agent(
        model_name=model_name,
        profile_name=profile_name,
        user_input=prompt,
        session_id=f"{run_id}:{step['id']}",
        agent_id=str(step.get("agent_id", "")).strip() or None,
        use_memory=bool(config.get("use_memory", False)),
        use_library=bool(config.get("use_library", False)),
        use_reflection=bool(config.get("use_reflection", False)),
        use_web_search=bool(config.get("use_web_search", False)),
        use_python_exec=bool(config.get("use_python_exec", False)),
        use_image_gen=bool(config.get("use_image_gen", False)),
        use_file_gen=bool(config.get("use_file_gen", False)),
        use_http_api=bool(config.get("use_http_api", False)),
        use_sql=bool(config.get("use_sql", False)),
        use_screenshot=bool(config.get("use_screenshot", False)),
        use_encrypt=bool(config.get("use_encrypt", False)),
        use_archiver=bool(config.get("use_archiver", False)),
        use_converter=bool(config.get("use_converter", False)),
        use_regex=bool(config.get("use_regex", False)),
        use_translator=bool(config.get("use_translator", False)),
        use_csv=bool(config.get("use_csv", False)),
        use_webhook=bool(config.get("use_webhook", False)),
        use_plugins=bool(config.get("use_plugins", False)),
        permission_mode=permission_mode,
        reasoning_effort=reasoning_effort,
        code_agent_run_id=code_agent_run_id,
        pause_for_workflow_request=True,
        resume=isinstance(resolution, dict),
        workflow_approval=workflow_approval,
        **extra_kwargs,
    )

    answer = str(result.get("answer", ""))
    step_payload = {
        "ok": bool(result.get("ok")),
        "answer": answer,
        "agent_id": str(step.get("agent_id", "")),
        "profile_name": profile_name,
        "prompt": prompt,
        "meta": result.get("meta", {}),
        "timeline": result.get("timeline", []),
        "tool_results": result.get("tool_results", []),
        "raw": result,
        "error": result.get("meta", {}).get("error", "") if not result.get("ok") else "",
    }
    for key in ("status", "request", "response_id"):
        if key in result:
            step_payload[key] = result[key]
    return step_payload

def _execute_tool_step(
    step: dict[str, Any],
    mapped_inputs: dict[str, Any],
    run_context: dict[str, Any],
    workflow_id: str,
    run_id: str,
    permission_mode: str,
) -> dict[str, Any]:
    from app.application.tool_registry.service import run_tool

    tool_name = str(step.get("tool_name", "")).strip()
    args = mapped_inputs if isinstance(mapped_inputs, dict) else {"input": mapped_inputs}
    resolutions = run_context.get("_workflow_request_resolutions", {})
    resolution = (
        resolutions.get(str(step.get("id", "")))
        if isinstance(resolutions, dict)
        else None
    )
    approved_tool = (
        resolution.get("approved_tool")
        if isinstance(resolution, dict)
        and isinstance(resolution.get("approved_tool"), dict)
        else {}
    )
    workflow_approved = workflow_approval_matches(approved_tool, tool_name, args)
    result = run_tool(
        tool_name,
        args,
        agent_id=WORKFLOW_ENGINE_AGENT_ID,
        run_id=run_id,
        source="workflow",
        workflow_id=workflow_id,
        step_id=str(step.get("id", "")),
        permission_mode=permission_mode,
        workflow_approved=workflow_approved,
        project_root=run_context.get("project_root"),
    )
    ok = bool(result.get("ok"))
    emit_workflow_event(
        "tool.executed",
        workflow_id,
        run_id,
        payload={"step_id": step["id"], "tool_name": tool_name, "ok": ok},
    )
    return {
        "ok": ok,
        "tool_name": tool_name,
        "output": result,
        "raw": result,
        "error": result.get("error", "") if not ok else "",
    }


def _execute_step(
    step: dict[str, Any],
    *,
    workflow_id: str,
    workflow_input: dict[str, Any],
    context: dict[str, Any],
    step_results: dict[str, Any],
    run_id: str,
    permission_mode: str = "ask",
    request_executor: Callable[
        [dict[str, Any], dict[str, Any], str],
        dict[str, Any],
    ] | None = None,
) -> dict[str, Any]:
    mapped_inputs = _map_step_inputs(
        step,
        workflow_input=workflow_input,
        context=context,
        step_results=step_results,
    )
    if step["type"] == "tool":
        resolutions = context.get("_workflow_request_resolutions", {})
        resolution = resolutions.get(str(step.get("id", ""))) if isinstance(resolutions, dict) else None
        if isinstance(resolution, dict) and isinstance(resolution.get("values"), dict):
            mapped_inputs = {**mapped_inputs, **resolution["values"]}
    if step["type"] == "agent":
        return _execute_agent_step(
            step,
            mapped_inputs,
            context,
            run_id,
            permission_mode,
        )
    if step["type"] == "request":
        if request_executor is None:
            raise RuntimeError("workflow request executor is not configured")
        return request_executor(step, context, permission_mode)
    return _execute_tool_step(
        step,
        mapped_inputs,
        context,
        workflow_id,
        run_id,
        permission_mode,
    )


def _resolve_next_step(step: dict[str, Any], *, success: bool) -> str:
    transitions = step.get("next")
    desired = STEP_SUCCESS if success else STEP_FAILURE
    if isinstance(transitions, str):
        return transitions.strip()
    if isinstance(transitions, list):
        for transition in transitions:
            when = str(transition.get("when", "always")).strip()
            if when == "always" or when == desired:
                return str(transition.get("to", "")).strip()
    if not success:
        return str(step.get("on_error", "")).strip()
    return ""


def _step_label(step: dict[str, Any]) -> str:
    config = step.get("config", {}) or {}
    return str(config.get("label") or step.get("save_as") or step.get("id"))
