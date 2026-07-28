"""Compatibility entry point backed by the code-agent core.

The old keyword-planner chat runtime was removed during UNIFY_CORE_PLAN Stage 4.
Internal callers that still invoke ``run_agent`` now drain the same universal
code-agent loop used by ``/api/code-agent/*``.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from app.application.code_agent.agent_loop import DEFAULT_MAX_STEPS, run_code_agent
from app.application.library.runtime import build_library_context
from app.core.data_files import data_subdir


def _default_project_root() -> str:
    return str(data_subdir("agent_workspace"))


def _normalise_history(history: list[Any] | None) -> list[dict[str, str]]:
    normalised: list[dict[str, str]] = []
    for item in history or []:
        if isinstance(item, dict):
            role = str(item.get("role") or "")
            content = str(item.get("content") or "")
        else:
            role = str(getattr(item, "role", "") or "")
            content = str(getattr(item, "content", "") or "")
        if role in {"user", "assistant"} and content:
            normalised.append({"role": role, "content": content})
    return normalised


def _with_library_context(message: str, enabled: bool) -> str:
    if not enabled:
        return message
    try:
        ctx = build_library_context()
    except Exception:
        return message
    block = str(ctx.get("context") or "").strip()
    if not block:
        return message
    used = ", ".join(str(name) for name in (ctx.get("used_files") or [])) or "attachments"
    return (
        f"Context from attached library files ({used}). Use it only if relevant:\n\n"
        f"{block}\n\n----- USER REQUEST -----\n{message}"
    )


def _timeline_from_code_agent(result: dict[str, Any]) -> list[dict[str, Any]]:
    timeline: list[dict[str, Any]] = []
    for call in result.get("tool_calls") or []:
        if not isinstance(call, dict):
            continue
        tool = str(call.get("tool") or "tool")
        result_text = str(call.get("result") or "")
        ok = call.get("ok")
        if ok is None:
            ok = not result_text.lower().startswith("error")
        timeline.append({
            "step": tool,
            "title": tool,
            "status": "done" if ok else "error",
            "detail": result_text[:500],
        })
    timeline.append({
        "step": "code_agent",
        "title": "Code Agent",
        "status": "done" if result.get("ok") else "error",
        "detail": str(result.get("error") or result.get("stop_reason") or ""),
    })
    return timeline


def _tool_results_from_code_agent(result: dict[str, Any]) -> list[dict[str, Any]]:
    tool_results: list[dict[str, Any]] = []
    for call in result.get("tool_calls") or []:
        if not isinstance(call, dict):
            continue
        result_text = str(call.get("result") or "")
        ok = call.get("ok")
        if ok is None:
            ok = not result_text.lower().startswith("error")
        tool_results.append({
            "tool": str(call.get("tool") or "tool"),
            "ok": bool(ok),
            "result": result_text,
            "arguments": call.get("arguments") or {},
        })
    return tool_results


def run_agent(
    *,
    model_name: str,
    profile_name: str,
    user_input: str,
    session_id: str | None = None,
    agent_id: str | None = None,
    use_memory: bool = True,
    use_library: bool = True,
    history: list[Any] | None = None,
    num_ctx: int | None = None,  # None = Auto (live /props window via the ONE resolver)
    project_root: str | Path | None = None,
    max_steps: int = DEFAULT_MAX_STEPS,
    **_ignored_legacy_options: Any,
) -> dict[str, Any]:
    """Run the universal code-agent core and return the historical dict shape.

    ``session_id``, ``profile_name`` and legacy tool-selection booleans are kept
    as accepted inputs for older internal callers, but routing/tool selection is
    now entirely handled by the code-agent prompt, tool registry and deferred
    tool search.
    """
    root = str(project_root or _default_project_root())
    message = _with_library_context(str(user_input or ""), bool(use_library))
    result = run_code_agent(
        user_message=message,
        project_root=root,
        model=str(model_name or "auto"),
        agent_id=str(agent_id or "code-agent"),
        max_steps=max_steps,
        conversation_history=_normalise_history(history),
        num_ctx=num_ctx or None,  # Auto stays Auto — never silently 131072
        auto_remember=bool(use_memory),
        approval_wait_seconds=0,
    )
    answer = str(result.get("response") or "")
    error = str(result.get("error") or "")
    meta = {
        "route": "code_agent",
        "model_name": str(model_name or "auto"),
        "profile_name": profile_name,
        "session_id": session_id,
        "project_root": root,
        "steps": int(result.get("steps") or 0),
        "stop_reason": str(result.get("stop_reason") or ""),
        "partial": bool(result.get("partial")),
    }
    if error:
        meta["error"] = error
    return {
        "ok": bool(result.get("ok")),
        "answer": answer,
        "content": answer,
        "timeline": _timeline_from_code_agent(result),
        "tool_results": _tool_results_from_code_agent(result),
        "meta": meta,
    }
