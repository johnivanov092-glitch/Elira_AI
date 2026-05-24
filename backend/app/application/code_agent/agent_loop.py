"""Code-agent runtime loop using Ollama function calling.

Real agent loop (Claude Code / Codex style): the LLM emits tool calls,
we execute them, feed results back, and continue until the model returns
a plain message (no tool calls) or `max_steps` is hit.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Callable

from app.application.code_agent.tools import (
    SandboxError,
    build_tool_dispatch,
    build_tool_schemas,
)

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "qwen2.5-coder:7b"
DEFAULT_MAX_STEPS = 20

SYSTEM_PROMPT = (
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


def run_code_agent(
    *,
    user_message: str,
    project_root: Path | str,
    model: str = DEFAULT_MODEL,
    max_steps: int = DEFAULT_MAX_STEPS,
    chat_fn: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Run a single user message through the agent loop.

    Returns:
        {
            "ok": bool,
            "response": str,           # final text from the model
            "steps": int,              # number of LLM round-trips
            "tool_calls": [...],       # transcript of every tool execution
            "stop_reason": "answer" | "max_steps" | "error",
            "error": str | None,
        }
    """
    root = Path(project_root).resolve()
    if not root.exists() or not root.is_dir():
        return {
            "ok": False,
            "response": "",
            "steps": 0,
            "tool_calls": [],
            "stop_reason": "error",
            "error": f"project_root does not exist or is not a directory: {project_root}",
        }

    dispatch = build_tool_dispatch(root)
    tool_schemas = build_tool_schemas()
    chat = chat_fn or _ollama_chat

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_message},
    ]

    tool_calls_log: list[dict[str, Any]] = []
    last_text = ""

    for step in range(1, max_steps + 1):
        try:
            response = chat(model=model, messages=messages, tools=tool_schemas)
        except Exception as exc:
            logger.exception("Ollama chat failed at step %d", step)
            return {
                "ok": False,
                "response": last_text,
                "steps": step - 1,
                "tool_calls": tool_calls_log,
                "stop_reason": "error",
                "error": str(exc),
            }

        message = (response or {}).get("message") or {}
        content = (message.get("content") or "").strip()
        tool_calls = message.get("tool_calls") or []

        if content:
            last_text = content

        if not tool_calls:
            return {
                "ok": True,
                "response": content or last_text,
                "steps": step,
                "tool_calls": tool_calls_log,
                "stop_reason": "answer",
                "error": None,
            }

        # Append the assistant turn (with tool_calls) before tool results.
        messages.append({
            "role": "assistant",
            "content": content,
            "tool_calls": tool_calls,
        })

        for call in tool_calls:
            fn = (call.get("function") or {})
            name = fn.get("name") or ""
            raw_args = fn.get("arguments") or {}
            if isinstance(raw_args, str):
                try:
                    args = json.loads(raw_args)
                except json.JSONDecodeError:
                    args = {}
            else:
                args = dict(raw_args)

            handler = dispatch.get(name)
            if handler is None:
                result = f"ERROR: unknown tool '{name}'"
            else:
                try:
                    result = handler(**args)
                except SandboxError as exc:
                    result = f"ERROR: sandbox violation: {exc}"
                except TypeError as exc:
                    result = f"ERROR: bad arguments to {name}: {exc}"
                except Exception as exc:
                    logger.exception("Tool %s crashed", name)
                    result = f"ERROR: {exc}"

            tool_calls_log.append({
                "step": step,
                "tool": name,
                "arguments": args,
                "result": result[:4000] + ("\n[... truncated]" if len(result) > 4000 else ""),
            })
            messages.append({
                "role": "tool",
                "content": str(result),
                "name": name,
            })

    return {
        "ok": False,
        "response": last_text,
        "steps": max_steps,
        "tool_calls": tool_calls_log,
        "stop_reason": "max_steps",
        "error": f"reached max_steps={max_steps} without final answer",
    }
