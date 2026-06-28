from __future__ import annotations

from pathlib import Path
from typing import Any


# ─── P10.1: deferred tool search meta-tool (foundation) ─────────────────────

TOOL_SEARCH_RESULT_LIMIT = 20
TOOL_SEARCH_ACTIVATION_CAP = 5
DELEGATE_TASK_MAX_STEPS = 6
DELEGATE_TASK_MAX_CTX = 8192
DELEGATE_TASK_TIMEOUT_SECONDS = 60
DELEGATE_TASK_READONLY_TOOLS = ("read_file", "glob", "grep", "recall")
DELEGATE_TASK_ROLES = {"explore", "plan", "verify", "review"}


def _clamp_to_max(value: Any, maximum: int) -> int:
    """Coerce a caller-controlled int and clamp to [0, maximum]; non-int / bad
    values fall back to `maximum`. Guarantees the model can never exceed the cap."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = maximum
    return min(max(0, n), maximum)


def _record_tool_search_metrics(
    run_id: str, query: str, match_count: int, activated: list[str], agent_id: str
) -> None:
    """Best-effort run metrics for tool.search / tool.activated (no schema change)."""
    try:
        from app.application.monitoring.runtime import record_metric

        record_metric(
            metric_type="tool.search",
            agent_id=agent_id,
            run_id=run_id,
            ok=True,
            details={
                "query": str(query),
                "match_count": int(match_count),
                "activated_count": len(activated),
            },
        )
        if activated:
            record_metric(
                metric_type="tool.activated",
                agent_id=agent_id,
                run_id=run_id,
                ok=True,
                details={"tools": list(activated), "query": str(query)},
            )
    except Exception:
        pass


def tool_search(
    *,
    run_id: str,
    query: str,
    agent_id: str = "code-agent",
    limit: int = TOOL_SEARCH_RESULT_LIMIT,
    activation_cap: int = TOOL_SEARCH_ACTIVATION_CAP,
) -> dict[str, Any]:
    """Read-only meta-tool (P10.1 foundation): search the ToolSpec registry and
    activate eligible, non-side-effect tools for THIS run only.

    - Requires a run_id (activation is run-scoped).
    - Activation grants VISIBILITY only — the unified executor still enforces
      policy / scope / approval at dispatch. This function executes nothing.
    - Disabled / unclassified / forbidden tools are surfaced but never activated.
    - Side-effect tools are surfaced but NOT auto-activated in this slice.
    - Activates at most ``activation_cap`` tools per call.
    - Uses the existing run-scoped deferred_tools store; ``activate_tools`` is a
      no-op unless the run already opted into deferred mode, so a non-deferred
      run is unchanged. No hidden global state.
    """
    rid = str(run_id or "").strip()
    if not rid:
        return {
            "ok": False,
            "text": "tool_search requires a run_id.",
            "error": "run_id_required",
            "matches": [],
            "activated": [],
        }

    from app.application.tool_registry.runtime import search_tool_specs
    from app.application.agent_kernel.deferred_tools import activate_tools

    safe_limit = _clamp_to_max(limit, TOOL_SEARCH_RESULT_LIMIT)
    matches = search_tool_specs(query, limit=safe_limit)

    cap = _clamp_to_max(activation_cap, TOOL_SEARCH_ACTIVATION_CAP)
    eligible: list[str] = []
    for match in matches:
        if len(eligible) >= cap:
            break
        if match["activatable"] and not match["side_effect"]:
            eligible.append(match["name"])

    activated: list[str] = []
    if eligible:
        active_set = activate_tools(rid, eligible)  # no-op unless run is deferred
        activated = [name for name in eligible if name in active_set]

    _record_tool_search_metrics(rid, query, len(matches), activated, agent_id)

    lines = [f"tool_search({query!r}): {len(matches)} match(es), {len(activated)} activated."]
    for match in matches:
        if match["name"] in activated:
            mark = "[activated]"
        elif not match["activatable"]:
            mark = f"[blocked: {match['reason']}]"
        elif match["side_effect"]:
            mark = "[side_effect: not auto-activated]"
        else:
            mark = "[eligible]"
        lines.append(f"  {match['name']} ({match['category']}/{match['source']}) {mark}")

    return {
        "ok": True,
        "text": "\n".join(lines),
        "matches": matches,
        "activated": activated,
    }


# ─── tool registry exposed to the local LLM provider ───────────────────────


def _format_checklist_text(result: dict[str, Any]) -> str:
    if not result.get("ok"):
        return f"ERROR: {result.get('error', 'todo_update failed')}"
    items = result.get("items") or []
    changed = result.get("changed") or []
    header = f"Checklist for run {result.get('run_id', '')}: {len(items)} item(s)"
    if changed:
        header += f", {len(changed)} changed"
    lines = [header]
    for item in items[:50]:
        blocker = str(item.get("blocker") or "").strip()
        suffix = f" blocker={blocker}" if blocker else ""
        lines.append(
            f"- {item.get('id')}: [{item.get('status')}] "
            f"{item.get('text')} (pos={item.get('position')}){suffix}"
        )
    if len(items) > 50:
        lines.append(f"[... truncated at 50 of {len(items)} items ...]")
    return "\n".join(lines)


def tool_todo_update(
    *,
    run_id: str,
    items: list[dict[str, Any]] | None = None,
    updates: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Read or update the durable checklist for the current agent run."""
    rid = str(run_id or "").strip()
    if not rid:
        return {"ok": False, "text": "ERROR: todo_update requires a run_id.", "error": "run_id_required"}
    if items is not None and not isinstance(items, list):
        return {"ok": False, "text": "ERROR: items must be a list.", "error": "invalid_items"}
    if updates is not None and not isinstance(updates, list):
        return {"ok": False, "text": "ERROR: updates must be a list.", "error": "invalid_updates"}

    try:
        from app.application.task_planner.service import todo_update
    except Exception as exc:
        return {"ok": False, "text": f"ERROR: task planner unavailable: {exc}", "error": str(exc)}

    try:
        result = todo_update(run_id=rid, items=items, updates=updates)
    except Exception as exc:
        return {"ok": False, "text": f"ERROR: {exc}", "error": str(exc)}
    result["text"] = _format_checklist_text(result)
    return result


def _delegate_prompt(role: str, task: str) -> str:
    role_guidance = {
        "explore": "Find relevant files, symbols, facts, and constraints. Do not propose edits unless asked.",
        "plan": "Produce a concise implementation plan and risks from read-only inspection.",
        "verify": "Inspect evidence and report whether the requested condition appears satisfied.",
        "review": (
            "Critically review the described work/changes for correctness, completeness, "
            "missed edge cases, and risks. Report concrete issues with file paths and "
            "what is wrong — do NOT fix them. If it looks good, say so explicitly."
        ),
    }
    guidance = role_guidance.get(role, role_guidance["explore"])
    return (
        f"You are a bounded read-only {role} subagent.\n"
        f"{guidance}\n"
        "Hard limits: do not write files, do not run shell, do not delegate further. "
        "Use only read_file/glob/grep/recall and non-side-effect tools activated by tool_search. "
        "Return concise findings with file paths when relevant.\n\n"
        f"Task:\n{task}"
    )


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _format_delegate_text(result: dict[str, Any]) -> str:
    sub = result.get("subagent") or {}
    status = sub.get("status") or ("completed" if result.get("ok") else "failed")
    lines = [
        f"delegate_task role={result.get('role')} status={status}",
        f"subagent_run_id={result.get('subagent_run_id')}",
    ]
    if result.get("error"):
        lines.append(f"error={result['error']}")
    output = str(result.get("result_text") or "").strip()
    if output:
        lines.append("\nResult:\n" + output)
    return "\n".join(lines)


def tool_delegate_task(
    project_root: Path,
    *,
    run_id: str,
    role: str = "explore",
    task: str,
    max_steps: int = DELEGATE_TASK_MAX_STEPS,
    num_ctx: int = DELEGATE_TASK_MAX_CTX,
) -> dict[str, Any]:
    """Delegate a bounded read-only subtask to a child code-agent run."""
    parent_run_id = str(run_id or "").strip()
    if not parent_run_id:
        return {"ok": False, "text": "ERROR: delegate_task requires a run_id.", "error": "run_id_required"}
    normalized_role = str(role or "explore").strip().lower()
    if normalized_role not in DELEGATE_TASK_ROLES:
        return {"ok": False, "text": f"ERROR: unsupported delegate role: {normalized_role}", "error": "unsupported_role"}
    cleaned_task = str(task or "").strip()
    if not cleaned_task:
        return {"ok": False, "text": "ERROR: delegate_task requires a task.", "error": "task_required"}

    safe_steps = max(1, min(_safe_int(max_steps, DELEGATE_TASK_MAX_STEPS), DELEGATE_TASK_MAX_STEPS))
    safe_ctx = max(1024, min(_safe_int(num_ctx, DELEGATE_TASK_MAX_CTX), DELEGATE_TASK_MAX_CTX))

    try:
        from app.application.task_planner import service as task_service

        started = task_service.start_subagent_run(
            parent_run_id=parent_run_id,
            role=normalized_role,
            task=cleaned_task,
            depth=1,
            max_steps=safe_steps,
            max_context_tokens=safe_ctx,
            tool_allowlist=list(DELEGATE_TASK_READONLY_TOOLS),
        )
    except Exception as exc:
        return {"ok": False, "text": f"ERROR: failed to create subagent run: {exc}", "error": str(exc)}
    if not started.get("ok"):
        return {
            "ok": False,
            "text": f"ERROR: failed to create subagent run: {started.get('error')}",
            "error": str(started.get("error") or "start_failed"),
        }

    subagent_run_id = str(started.get("subagent_run_id") or "").strip()
    result_text = ""
    error = ""
    ok = False
    finished: dict[str, Any] = {}
    try:
        from app.application.code_agent.agent_loop import run_code_agent

        sub_result = run_code_agent(
            user_message=_delegate_prompt(normalized_role, cleaned_task),
            project_root=project_root,
            model="auto",
            agent_id=f"subagent-{normalized_role}",
            max_steps=safe_steps,
            run_id=subagent_run_id,
            num_ctx=safe_ctx,
            base_tools=DELEGATE_TASK_READONLY_TOOLS,
            execution_timeout_seconds=DELEGATE_TASK_TIMEOUT_SECONDS,
            auto_remember=False,
        )
        ok = bool(sub_result.get("ok"))
        result_text = str(sub_result.get("response") or "")
        error = str(sub_result.get("error") or "")
    except Exception as exc:
        error = str(exc)

    status = "completed" if ok else "failed"
    try:
        finished = task_service.finish_subagent_run(
            subagent_run_id=subagent_run_id,
            status=status,
            result_text=result_text,
            error=error,
        )
    except Exception as exc:
        error = f"{error}; finish_failed={exc}" if error else f"finish_failed={exc}"
        finished = {"ok": False, "error": str(exc), "subagent_run_id": subagent_run_id, "status": status}

    output = {
        "ok": ok,
        "role": normalized_role,
        "subagent_run_id": subagent_run_id,
        "result_text": result_text,
        "error": error,
        "subagent": finished if finished.get("ok") else {**started, "status": status},
    }
    output["text"] = _format_delegate_text(output)
    return output
