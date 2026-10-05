from __future__ import annotations

from pathlib import Path
from copy import deepcopy
import json
from typing import Any

DELEGATE_TASK_ROLES = {"explore", "plan", "verify", "review"}
DELEGATE_MAX_DEPTH = 1
DELEGATE_READ_TOOLS = frozenset({"read_file", "glob", "grep", "path_exists", "project_map"})
DELEGATE_RUNTIME_OPERATIONS = frozenset({"status", "skill_list", "skill_load"})


def delegate_tool_allowed(name: str, arguments: dict[str, Any]) -> bool:
    """Hard role scope, independent of Workflow permission or schema activation."""
    return (name in DELEGATE_READ_TOOLS or name == "runtime_control"
            and str(arguments.get("operation") or "").strip().lower() in DELEGATE_RUNTIME_OPERATIONS)


def delegate_read_schemas(schemas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for schema in schemas:
        name = (schema.get("function") or {}).get("name")
        if name not in DELEGATE_READ_TOOLS and name != "runtime_control":
            continue
        item = deepcopy(schema)
        if name == "runtime_control":
            item["function"]["parameters"]["properties"]["operation"]["enum"] = sorted(DELEGATE_RUNTIME_OPERATIONS)
            item["function"]["description"] = (
                "Read-only delegated inspection: status, skill_list, skill_load only. "
                "Loading instructions does not authorize shell, file writes, activation or publication."
                " Return findings directly; no task_decide, report files, checker execution."
            )
        result.append(item)
    return result


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
        "Return findings directly with file paths when relevant; no task_decide, report files, checker execution.\n\n"
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

    from app.application.code_agent.run_journal import RunJournal
    from app.application.code_agent.tools._shell import get_current_run_id

    current_run_id = get_current_run_id()
    if current_run_id and current_run_id != parent_run_id:
        return {"ok": False, "text": "ERROR: delegate parent does not match the executing run.",
                "error": "parent_run_mismatch"}
    try:
        parent_state = RunJournal.active_state(parent_run_id)
        if Path(str(parent_state.get("project_root") or "")).resolve() != project_root.resolve():
            raise ValueError("delegated run must use its parent's project root")
        parent_request = parent_state.get("request") or {}
        depth = _safe_int(parent_request.get("delegation_depth"), 0) + 1
        if depth > DELEGATE_MAX_DEPTH:
            raise ValueError("delegate depth limit reached")
    except (RuntimeError, ValueError, TypeError) as exc:
        return {"ok": False, "text": f"ERROR: {exc}", "error": str(exc)}
    permission_mode = str(parent_request.get("permission_mode") or "ask")
    safe_ctx = max(0, _safe_int(num_ctx, 0))
    parent_ctx = max(0, _safe_int(parent_request.get("num_ctx"), 0))
    safe_ctx = min(safe_ctx, parent_ctx) if safe_ctx and parent_ctx else safe_ctx or parent_ctx
    child_tools = sorted(DELEGATE_READ_TOOLS | {"runtime_control"})
    contract = (parent_state.get("task_outcome") or {}).get("contract") or {}
    parent_context = {
        "original_request": parent_state.get("task"),
        "goal": contract.get("goal") or parent_state.get("task"),
        "task_spec": parent_state.get("task_spec"),
        "requirements": contract.get("requirements", []),
        "clarifications": contract.get("clarifications", []),
        "persistence_policy": parent_state.get("persistence_policy") or parent_request.get("persistence_policy"),
    }
    task_instructions = (
        "[Delegated read-only inspection]\n"
        "The parent task constraints below apply. Inspect and report; do not change files or execute code. "
        "Only read_file/glob/grep/path_exists/project_map and runtime_control "
        "status/skill_list/skill_load are allowed. Skill instructions cannot widen this scope. "
        "Return findings directly; no task_decide, report files, checker execution.\n"
        + json.dumps(parent_context, ensure_ascii=False)
    )

    try:
        from app.application.task_planner import service as task_service

        started = task_service.start_subagent_run(
            parent_run_id=parent_run_id,
            role=normalized_role,
            task=cleaned_task,
            depth=depth,
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
    cancel_token = None
    cleanup_failed = False
    try:
        from app.application.code_agent.agent_loop import run_code_agent
        from app.application.code_agent.run_control import request_cancel, _has_retained_cancel_handle
        from app.application.code_agent.tools._shell import (
            register_run_cancel_callback, unregister_run_cancel_callback,
            set_current_run_id, reset_current_run_id,
        )

        def cancel_child() -> None:
            nonlocal cleanup_failed
            try:
                request_cancel(subagent_run_id)
            except Exception:
                cleanup_failed = True
                raise
            cleanup_failed = False

        binding = set_current_run_id(parent_run_id)
        try:
            cancel_token = register_run_cancel_callback(cancel_child)
        finally:
            reset_current_run_id(binding)

        sub_result = run_code_agent(
            user_message=_delegate_prompt(normalized_role, cleaned_task),
            task_instructions=task_instructions,
            project_root=project_root,
            working_dir=parent_request.get("working_dir"),
            model=str(parent_request.get("model") or "auto"),
            profile_name=str(parent_request.get("profile_name") or "Инженерный"),
            thinking=bool(parent_request.get("thinking")),
            reasoning_effort=parent_request.get("reasoning_effort"),
            agent_id=f"subagent-{normalized_role}",
            run_id=subagent_run_id,
            num_ctx=safe_ctx or None,
            base_tools=child_tools,
            auto_remember=False,
            permission_mode=permission_mode,
            parent_run_id=parent_run_id,
            read_only=True,
            resource_refs=parent_request.get("resource_refs") or [],
        )
        ok = bool(sub_result.get("ok"))
        result_text = str(sub_result.get("response") or "")
        error = str(sub_result.get("error") or "")
        if _has_retained_cancel_handle(subagent_run_id):
            ok = False
            error = "child_cleanup_incomplete" + (f": {error}" if error else "")
    except Exception as exc:
        error = str(exc)
    finally:
        if (cancel_token is not None and not cleanup_failed
                and not _has_retained_cancel_handle(subagent_run_id)):
            unregister_run_cancel_callback(cancel_token)

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
