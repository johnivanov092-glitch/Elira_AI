"""Code-agent runtime — single-shot run plus a streaming generator with
cancellation and multi-turn conversation support.

The streaming variant `stream_code_agent` yields events shaped for
Server-Sent Events on the API side; the legacy `run_code_agent` keeps
the synchronous dict-returning behaviour expected by existing tests.

Tools now return structured dicts (see app.application.code_agent.tools).
We extract ``text`` to feed back to the LLM and pass the rest through
to event consumers (the frontend uses ``touched_path`` / ``old_content``
/ ``new_content`` / ``diff_action`` for live IDE updates and diff
preview).
"""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Iterator

from app.application.code_agent.tools import (
    SandboxError,
    build_tool_dispatch,
    build_tool_schemas,
)

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "qwen2.5-coder:7b"
DEFAULT_MAX_STEPS = 20
PROJECT_PROMPT_FILENAME = ".elira/agent.md"

BASE_SYSTEM_PROMPT = (
    "Ты — code-агент Elira. Работаешь в локальной директории проекта пользователя.\n"
    "У тебя есть инструменты: read_file, write_file, edit_file, glob, grep, run_bash.\n"
    "Все пути — относительно корня проекта. Действуй пошагово: смотри, правь, проверяй.\n"
    "Перед записью файла читай его, чтобы не затереть существующий код. "
    "После изменений запускай `run_bash` чтобы проверить что всё работает.\n"
    "Когда задача выполнена — отвечай обычным текстом без вызова инструментов."
)


def _ollama_chat(**kwargs: Any) -> dict[str, Any]:
    """Wrapper so tests can monkeypatch one symbol."""
    import ollama  # lazy — keeps import cheap when this module is loaded
    return ollama.chat(**kwargs)


def _read_project_prompt(project_root: Path) -> str:
    """Return the contents of .elira/agent.md (project-specific system
    prompt) if it exists, else empty string. Project prompt is appended
    to the base system prompt so per-project conventions / rules /
    forbidden paths are always loaded.
    """
    target = project_root / PROJECT_PROMPT_FILENAME
    if not target.is_file():
        return ""
    try:
        text = target.read_text(encoding="utf-8").strip()
    except Exception:
        return ""
    return text


def _build_system_prompt(project_root: Path) -> str:
    extra = _read_project_prompt(project_root)
    if not extra:
        return BASE_SYSTEM_PROMPT
    return BASE_SYSTEM_PROMPT + "\n\n--- Project-specific instructions (.elira/agent.md) ---\n" + extra


# Global registry of active cancel events so an external HTTP route can flip
# the flag mid-stream. Keys are run_ids handed back to the client.
_CANCEL_REGISTRY: dict[str, threading.Event] = {}
_REGISTRY_LOCK = threading.Lock()


def request_cancel(run_id: str) -> bool:
    """Flip the cancel event for `run_id`. Returns True if the run was
    known, False otherwise.
    """
    with _REGISTRY_LOCK:
        ev = _CANCEL_REGISTRY.get(run_id)
    if ev is None:
        return False
    ev.set()
    return True


def _register_run(run_id: str) -> threading.Event:
    ev = threading.Event()
    with _REGISTRY_LOCK:
        _CANCEL_REGISTRY[run_id] = ev
    return ev


def _unregister_run(run_id: str) -> None:
    with _REGISTRY_LOCK:
        _CANCEL_REGISTRY.pop(run_id, None)


def _coerce_history(history: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Validate / normalize prior conversation messages from the client.
    Only role + content fields are kept; tool_calls and tool results from
    past turns are dropped (we feed the agent fresh tools each turn).
    """
    if not history:
        return []
    out: list[dict[str, Any]] = []
    for item in history:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        content = item.get("content")
        if role not in {"user", "assistant"}:
            continue
        if not isinstance(content, str) or not content:
            continue
        out.append({"role": role, "content": content})
    return out


def _execute_tool_call(
    dispatch: dict[str, Callable[..., dict[str, Any]]],
    name: str,
    raw_args: Any,
) -> tuple[dict[str, Any], Any]:
    """Run a single tool call. Returns (tool_meta, parsed_args)."""
    if isinstance(raw_args, str):
        try:
            args = json.loads(raw_args)
        except json.JSONDecodeError:
            args = {}
    else:
        args = dict(raw_args) if isinstance(raw_args, dict) else {}

    handler = dispatch.get(name)
    if handler is None:
        return ({"text": f"ERROR: unknown tool '{name}'"}, args)
    try:
        result = handler(**args)
    except SandboxError as exc:
        return ({"text": f"ERROR: sandbox violation: {exc}"}, args)
    except TypeError as exc:
        return ({"text": f"ERROR: bad arguments to {name}: {exc}"}, args)
    except Exception as exc:
        logger.exception("Tool %s crashed", name)
        return ({"text": f"ERROR: {exc}"}, args)

    if not isinstance(result, dict):
        return ({"text": str(result)}, args)
    return (result, args)


def _truncate(text: str, limit: int = 4000) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n[... truncated]"


def stream_code_agent(
    *,
    user_message: str,
    project_root: Path | str,
    model: str = DEFAULT_MODEL,
    max_steps: int = DEFAULT_MAX_STEPS,
    conversation_history: list[dict[str, Any]] | None = None,
    run_id: str | None = None,
    chat_fn: Callable[..., dict[str, Any]] | None = None,
) -> Iterator[dict[str, Any]]:
    """Stream the agent loop as events.

    Yields dicts with a `type` discriminator:
      - {"type": "run_started", "run_id": ...}
      - {"type": "step_started", "step": N}
      - {"type": "tool_call", "step": N, "tool": str, "arguments": dict,
         "result": str, "touched_path"?: str,
         "old_content"?: str, "new_content"?: str, "diff_action"?: str}
      - {"type": "final_response", "step": N, "text": str}
      - {"type": "done", "ok": bool, "steps": int, "stop_reason": str,
         "error": str | None}
    """
    root = Path(project_root).resolve()
    rid = run_id or uuid.uuid4().hex
    cancel_event = _register_run(rid)

    try:
        if not root.exists() or not root.is_dir():
            yield {"type": "run_started", "run_id": rid}
            yield {
                "type": "done",
                "ok": False,
                "steps": 0,
                "stop_reason": "error",
                "error": f"project_root does not exist or is not a directory: {project_root}",
            }
            return

        dispatch = build_tool_dispatch(root)
        tool_schemas = build_tool_schemas()
        chat = chat_fn or _ollama_chat

        system_prompt = _build_system_prompt(root)
        messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
        messages.extend(_coerce_history(conversation_history))
        messages.append({"role": "user", "content": user_message})

        yield {"type": "run_started", "run_id": rid}

        last_text = ""
        for step in range(1, max_steps + 1):
            if cancel_event.is_set():
                yield {
                    "type": "done",
                    "ok": False,
                    "steps": step - 1,
                    "stop_reason": "cancelled",
                    "error": "Cancelled by user",
                }
                return

            yield {"type": "step_started", "step": step}

            try:
                response = chat(model=model, messages=messages, tools=tool_schemas)
            except Exception as exc:
                logger.exception("Ollama chat failed at step %d", step)
                yield {
                    "type": "done",
                    "ok": False,
                    "steps": step - 1,
                    "stop_reason": "error",
                    "error": str(exc),
                }
                return

            if cancel_event.is_set():
                yield {
                    "type": "done",
                    "ok": False,
                    "steps": step,
                    "stop_reason": "cancelled",
                    "error": "Cancelled by user",
                }
                return

            message = (response or {}).get("message") or {}
            content = (message.get("content") or "").strip()
            tool_calls = message.get("tool_calls") or []

            if content:
                last_text = content

            if not tool_calls:
                yield {"type": "final_response", "step": step, "text": content or last_text}
                yield {
                    "type": "done",
                    "ok": True,
                    "steps": step,
                    "stop_reason": "answer",
                    "error": None,
                }
                return

            messages.append({
                "role": "assistant",
                "content": content,
                "tool_calls": tool_calls,
            })

            for call in tool_calls:
                fn = call.get("function") or {}
                name = fn.get("name") or ""
                raw_args = fn.get("arguments") or {}
                tool_meta, parsed_args = _execute_tool_call(dispatch, name, raw_args)
                text_result = str(tool_meta.get("text", ""))
                event: dict[str, Any] = {
                    "type": "tool_call",
                    "step": step,
                    "tool": name,
                    "arguments": parsed_args,
                    "result": _truncate(text_result),
                }
                for opt in ("touched_path", "old_content", "new_content", "diff_action"):
                    if opt in tool_meta:
                        # Keep diff payloads truncated too to keep events small.
                        val = tool_meta[opt]
                        if isinstance(val, str) and opt in {"old_content", "new_content"} and len(val) > 40000:
                            event[opt] = val[:40000] + "\n[... truncated]"
                        else:
                            event[opt] = val
                yield event
                messages.append({"role": "tool", "content": text_result, "name": name})

        yield {
            "type": "done",
            "ok": False,
            "steps": max_steps,
            "stop_reason": "max_steps",
            "error": f"reached max_steps={max_steps} without final answer",
        }
    finally:
        _unregister_run(rid)


def run_code_agent(
    *,
    user_message: str,
    project_root: Path | str,
    model: str = DEFAULT_MODEL,
    max_steps: int = DEFAULT_MAX_STEPS,
    conversation_history: list[dict[str, Any]] | None = None,
    chat_fn: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Synchronous single-shot wrapper around stream_code_agent. Drains
    the generator and aggregates the result into the legacy dict shape.
    """
    tool_calls_log: list[dict[str, Any]] = []
    response_text = ""
    ok = False
    stop_reason = "error"
    error: str | None = None
    steps = 0

    for event in stream_code_agent(
        user_message=user_message,
        project_root=project_root,
        model=model,
        max_steps=max_steps,
        conversation_history=conversation_history,
        chat_fn=chat_fn,
    ):
        et = event.get("type")
        if et == "tool_call":
            tool_calls_log.append({
                "step": event["step"],
                "tool": event["tool"],
                "arguments": event["arguments"],
                "result": event["result"],
                **{k: event[k] for k in ("touched_path", "old_content", "new_content", "diff_action") if k in event},
            })
        elif et == "final_response":
            response_text = event.get("text", "")
        elif et == "done":
            ok = bool(event.get("ok"))
            stop_reason = str(event.get("stop_reason", "error"))
            error = event.get("error")
            steps = int(event.get("steps", 0))

    return {
        "ok": ok,
        "response": response_text,
        "steps": steps,
        "tool_calls": tool_calls_log,
        "stop_reason": stop_reason,
        "error": error,
    }


# ─── project-prompt CRUD ────────────────────────────────────────────────────


def get_project_prompt(project_root: Path | str) -> dict[str, Any]:
    root = Path(project_root).resolve()
    target = root / PROJECT_PROMPT_FILENAME
    exists = target.is_file()
    content = ""
    if exists:
        try:
            content = target.read_text(encoding="utf-8")
        except Exception as exc:
            return {"ok": False, "exists": True, "content": "", "error": str(exc), "path": str(target)}
    return {"ok": True, "exists": exists, "content": content, "path": str(target)}


def set_project_prompt(project_root: Path | str, content: str) -> dict[str, Any]:
    root = Path(project_root).resolve()
    if not root.exists() or not root.is_dir():
        return {"ok": False, "error": f"project_root does not exist: {root}"}
    target = root / PROJECT_PROMPT_FILENAME
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        target.write_text(content, encoding="utf-8")
    except Exception as exc:
        return {"ok": False, "error": str(exc), "path": str(target)}
    return {"ok": True, "exists": True, "content": content, "path": str(target)}
