from __future__ import annotations

from pathlib import Path
from typing import Any

DELEGATE_TASK_ROLES = {"explore", "plan", "verify", "review"}


# ─── tool registry exposed to the local LLM provider ───────────────────────


def _format_checklist_text(result: dict[str, Any]) -> str:
    if not result.get("ok"):
        error = str(result.get("error", "todo_update failed"))
        if error == "item_not_found":
            # Name the missing id AND the real ids, so the model can correct the
            # call instead of guessing (the Mini CRM run got a bare
            # `ERROR: item_not_found` for updates=[…, css] and stalled). The
            # atomic no-partial-write semantics are unchanged (task_planner
            # validates the whole payload before writing).
            missing = str(result.get("item_id") or "?")
            existing = [str(it.get("id")) for it in (result.get("items") or [])][:50]
            listing = ", ".join(existing) if existing else "(пусто)"
            return (
                f"ERROR: item_not_found: {missing}. Ни одно обновление не применено "
                f"(атомарно). Существующие id: {listing}"
            )
        return f"ERROR: {error}"
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
        f"You are a {role} subagent.\n"
        f"{guidance}\n"
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
    num_ctx: int = 0,
    permission_mode: str = "ask",
) -> dict[str, Any]:
    """Delegate a subtask to a child code-agent run."""
    parent_run_id = str(run_id or "").strip()
    if not parent_run_id:
        return {"ok": False, "text": "ERROR: delegate_task requires a run_id.", "error": "run_id_required"}
    normalized_role = str(role or "explore").strip().lower()
    if normalized_role not in DELEGATE_TASK_ROLES:
        return {"ok": False, "text": f"ERROR: unsupported delegate role: {normalized_role}", "error": "unsupported_role"}
    cleaned_task = str(task or "").strip()
    if not cleaned_task:
        return {"ok": False, "text": "ERROR: delegate_task requires a task.", "error": "task_required"}

    safe_ctx = max(0, _safe_int(num_ctx, 0))
    child_tools = None

    try:
        from app.application.task_planner import service as task_service

        started = task_service.start_subagent_run(
            parent_run_id=parent_run_id,
            role=normalized_role,
            task=cleaned_task,
            depth=1,
            max_context_tokens=safe_ctx,
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
            run_id=subagent_run_id,
            num_ctx=safe_ctx or None,
            base_tools=child_tools,
            auto_remember=False,
            permission_mode=permission_mode,
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
