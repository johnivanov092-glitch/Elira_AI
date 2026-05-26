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
import re
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

# NOTE on model choice: not every Ollama-installed model emits the
# structured `tool_calls` field — some (qwen2.5-coder:7b, granite-code:8b,
# codegeex4) dump tool calls as plain JSON in `content` instead. The
# agent loop tolerates that via _extract_inline_tool_calls, but a model
# that natively supports tools is faster and more reliable. Models
# verified to emit native tool_calls on Ollama 0.24:
#   - sorc/qwen3.5-instruct-uncensored:4b (fast, ~12s/call)
#   - sorc/qwen3.5-instruct:4b
#   - gemma4:e2b
#   - llama3.2, llama3.1, mistral-nemo (not installed here)
# qwen2.5-coder:7b also works thanks to the fallback but is slower.
DEFAULT_MODEL = "qwen2.5-coder:7b"
DEFAULT_MAX_STEPS = 20
DEFAULT_NUM_CTX = 16384  # Ollama's default is 2048 — way too small for tool-using agents.
PROJECT_PROMPT_FILENAME = ".elira/agent.md"

BASE_SYSTEM_PROMPT_TEMPLATE = """Ты — Elira code-агент с ПРЯМЫМ ДОСТУПОМ к файловой системе и shell.

## Текущая директория проекта
{project_root}

## Твои инструменты (используй их, а не объясняй пользователю как делать руками)
- read_file(path) — читать файл
- write_file(path, content) — создать или перезаписать файл
- edit_file(path, old_string, new_string) — точечная правка существующего файла
- glob(pattern) — найти файлы по маске (например `**/*.py`)
- grep(pattern, path) — искать текст в файлах
- run_bash(command, timeout=60) — выполнить shell-команду в директории проекта
- recall(query) — семантический поиск в RAG-памяти проекта

## ЖЕЛЕЗНЫЕ ПРАВИЛА

1. У тебя ЕСТЬ доступ к файловой системе. Никогда не говори «я не могу запустить», «не имею доступа», «склонируйте проект», «установите зависимости». Это ложь. Ты можешь запускать `run_bash` прямо сейчас.

2. Когда пользователь просит ЗАПУСТИТЬ / ПРОВЕРИТЬ / ВЫПОЛНИТЬ что-то — ты вызываешь `run_bash`. Не выводишь команду в чат с просьбой её выполнить. ТЫ её выполняешь сам.

3. Когда пользователь просит СОЗДАТЬ / НАПИСАТЬ файл — ты вызываешь `write_file`. Не выводишь содержимое в чат с просьбой сохранить. ТЫ его сохраняешь сам.

4. Когда пользователь спрашивает «что в файле X» / «как устроено Y» — ты вызываешь `read_file` или `grep`. Не отговариваешься «нужно посмотреть».

5. Никаких подтверждений. Никаких «можно я это сделаю?». Просто делай.

6. Все пути относительно корня проекта (см. выше). `src/calc.py` — это {project_root}/src/calc.py. Не нужно полных путей.

7. Действуй пошагово: посмотрел → правишь → проверил через `run_bash`. После каждого write_file проверь что код реально работает.

8. Используй `recall(query)` когда нужно найти «где у меня реализовано X» или «что я делал по теме Y» — RAG помнит прошлые задачи и проиндексированный код.

9. Когда задача РЕАЛЬНО выполнена (файлы созданы, тесты прошли) — только тогда отвечай обычным текстом без вызова инструментов. Текст — это финал, не план.

## Антипаттерны (НИКОГДА так не делай)

ПЛОХО: «Извините, я не могу взаимодействовать с вашей локальной файловой системой».
ХОРОШО: вызвать `read_file` / `run_bash` / `write_file`.

ПЛОХО: «Вот команда, запустите её сами: `pytest test_foo.py`».
ХОРОШО: вызвать `run_bash(command="pytest test_foo.py")`.

ПЛОХО: «Создайте файл foo.py с таким содержимым: ...».
ХОРОШО: вызвать `write_file(path="foo.py", content="...")`.

ПЛОХО: «Какая у вас локальная директория?».
ХОРОШО: ты её знаешь, она указана выше в этом промпте."""


def _build_base_system_prompt(project_root: Path) -> str:
    return BASE_SYSTEM_PROMPT_TEMPLATE.format(project_root=str(project_root))


# Kept for backwards-compat (tests / external imports). Generic, no project root.
BASE_SYSTEM_PROMPT = BASE_SYSTEM_PROMPT_TEMPLATE.format(project_root="<укажет runtime>")


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
    base = _build_base_system_prompt(project_root)
    extra = _read_project_prompt(project_root)
    if not extra:
        return base
    return base + "\n\n--- Project-specific instructions (.elira/agent.md) ---\n" + extra


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


# Patterns that say "user explicitly wants you to RUN something". When
# present, we suffix the user message with an inline reminder — small
# but effective at unsticking models that hallucinate "I have no access".
_EXECUTION_INTENT = re.compile(
    r"(?<!\w)(запусти|запустить|выполни|выполнить|проверь|проверить|"
    r"создай файл|создай тест|run|execute|run tests|run it|"
    r"сделай это|поправь и запусти)(?!\w)",
    re.IGNORECASE | re.UNICODE,
)


def _maybe_inject_execution_reminder(user_message: str) -> str:
    """If the user's wording clearly demands execution, append a short
    reminder telling the model 'this is a tool-use turn, not a
    text-answer turn'. Models like qwen2.5-coder occasionally drift
    into 'helpful explanation' mode otherwise.
    """
    if _EXECUTION_INTENT.search(user_message or ""):
        return (
            user_message
            + "\n\n[reminder] Это задача на выполнение. Используй инструменты "
            + "(run_bash / write_file / read_file и т.д.) и сделай это сам. "
            + "Не объясняй мне как запустить — запусти."
        )
    return user_message


_SUMMARY_PREFIX = "[CONTEXT SUMMARY]"


def _coerce_history(history: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Validate / normalize prior conversation messages from the client.
    Only role + content fields are kept; tool_calls and tool results from
    past turns are dropped (we feed the agent fresh tools each turn).

    Compressed summary turns (assistant messages with the
    `[CONTEXT SUMMARY]` prefix that the frontend produces after a
    `summarize_history` call) are re-tagged as system messages so the
    LLM treats them as out-of-band context rather than as its own prior
    reply — that prevents 'I never said that' confusion.
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
        if role == "assistant" and content.startswith(_SUMMARY_PREFIX):
            stripped = content[len(_SUMMARY_PREFIX):].lstrip("\n").lstrip()
            if not stripped:
                continue
            out.append({
                "role": "system",
                "content": (
                    "Earlier conversation summary (compressed from prior turns by the "
                    "user — treat as context, not as your own previous answer):\n"
                    + stripped
                ),
            })
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


# How much of a tool's text output to send back to the LLM as the
# 'tool' message on the next turn. Locally we don't pay for tokens, but
# `num_ctx` is a hard limit — a single 80K-char `pytest -v` dump would
# eat the entire context and start truncating the system prompt + user
# task. 12000 chars is ~3000 tokens ≈ 18% of the default 16K context per
# call, leaving room for several tool calls per turn plus the model's
# own reasoning.
TOOL_RESULT_LLM_LIMIT = 12000


def _truncate_for_llm(text: str, limit: int = TOOL_RESULT_LLM_LIMIT) -> str:
    """Truncate a tool output before feeding it back to the LLM.

    Strategy: if the output fits, return it unchanged. Otherwise, keep
    the start (typical context of what happened) *and* the end (final
    lines / exit code / last error) — drop the middle. This matters for
    `run_bash` long outputs where the exit code + stack trace at the
    bottom is the most useful part, and for `read_file` of large files
    where the start has imports / docstring and the end has main code.
    """
    if len(text) <= limit:
        return text
    # Reserve ~100 chars for the marker; split remainder 65/35 head/tail
    # so we lean towards the start (where filenames, paths, signatures
    # tend to live) but keep enough of the end for run_bash results.
    budget = max(400, limit - 100)
    head_size = int(budget * 0.65)
    tail_size = budget - head_size
    cut = len(text) - head_size - tail_size
    return (
        text[:head_size]
        + f"\n[... truncated {cut} chars from middle to stay under context limit ...]\n"
        + text[-tail_size:]
    )


_INLINE_TOOL_NAMES_PATTERN = None  # built lazily once dispatch is known


def _iter_json_object_substrings(text: str) -> Iterator[str]:
    """Yield every balanced top-level JSON-object substring inside `text`.
    Used to recover from models that emit multiple ```json blocks back to
    back, or just multiple JSON objects with prose around them.
    """
    n = len(text)
    i = 0
    while i < n:
        if text[i] != "{":
            i += 1
            continue
        depth = 0
        in_str = False
        esc = False
        for j in range(i, n):
            ch = text[j]
            if esc:
                esc = False
                continue
            if ch == "\\":
                esc = True
                continue
            if ch == '"':
                in_str = not in_str
                continue
            if in_str:
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    yield text[i : j + 1]
                    i = j + 1
                    break
        else:
            # Unbalanced; stop scanning
            break


def _extract_inline_tool_calls(content: str, known_tools: set[str]) -> list[dict[str, Any]]:
    """Some Ollama models (notably qwen2.5-coder, granite-code, codegeex)
    emit tool calls as plain JSON in `message.content` instead of populating
    the structured `message.tool_calls` field. This helper recovers those
    so the agent loop still progresses.

    Recognized formats (any may appear in code fences, multiple times,
    with prose around them):
      {"name": "tool", "arguments": {...}}
      {"name": "tool", "parameters": {...}}
      [{"name": ...}, ...]
      {"tool_calls": [{...}]}
      {"function": {"name": ..., "arguments": {...}}}

    Tool names not present in `known_tools` are dropped (the model
    hallucinated). Returns a list shaped like Ollama's native
    `tool_calls`: [{"function": {"name": ..., "arguments": {...}}}].

    Multiple JSON objects in one content string are all returned — the
    agent loop will execute them in order.
    """
    if not content:
        return []

    def _normalize(item: Any) -> dict[str, Any] | None:
        if not isinstance(item, dict):
            return None
        # Unwrap {"function": {...}}
        if "function" in item and isinstance(item["function"], dict):
            fn = item["function"]
            name = fn.get("name")
            args = fn.get("arguments") or fn.get("parameters") or {}
        else:
            name = item.get("name")
            args = item.get("arguments") or item.get("parameters") or {}
        if not isinstance(name, str) or name not in known_tools:
            return None
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {}
        if not isinstance(args, dict):
            args = {}
        return {"function": {"name": name, "arguments": args}}

    out: list[dict[str, Any]] = []
    # Scan ALL JSON-object substrings (handles multiple ```json blocks,
    # arrays of tool calls embedded in prose, etc.)
    for raw in _iter_json_object_substrings(content):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            if "tool_calls" in parsed and isinstance(parsed["tool_calls"], list):
                for it in parsed["tool_calls"]:
                    norm = _normalize(it)
                    if norm:
                        out.append(norm)
            else:
                norm = _normalize(parsed)
                if norm:
                    out.append(norm)

    # Also try top-level array (rare but seen): "[{...},{...}]"
    if not out:
        stripped = content.strip()
        if stripped.startswith("```"):
            stripped = stripped.split("\n", 1)[1] if "\n" in stripped else stripped[3:]
            if stripped.endswith("```"):
                stripped = stripped[:-3]
            stripped = stripped.strip()
        if stripped.startswith("["):
            try:
                arr = json.loads(stripped)
                if isinstance(arr, list):
                    for it in arr:
                        norm = _normalize(it)
                        if norm:
                            out.append(norm)
            except json.JSONDecodeError:
                pass

    return out


def _try_remember_turn(*, user_message: str, response_text: str, project_root: Path) -> None:
    """Fire-and-forget: write a short summary of a successful agent turn
    to RAG so future `recall(query)` can surface it. Failures are logged
    but never raised.
    """
    try:
        from app.application.rag_memory.service import add_to_rag
    except Exception:
        return
    user = (user_message or "").strip()
    answer = (response_text or "").strip()
    if not user or not answer:
        return
    if len(user) > 300:
        user = user[:300] + " [...]"
    if len(answer) > 600:
        answer = answer[:600] + " [...]"
    project_name = project_root.name or str(project_root)
    summary = f"[agent_turn project={project_name}] task: {user} | outcome: {answer}"
    try:
        # Pass project= so the entry is scoped to this project and
        # recall() from a different project doesn't pull it up.
        add_to_rag(text=summary, category="agent_turn", importance=3, project=project_name)
    except Exception as exc:
        logger.debug("auto-remember failed: %s", exc)


def stream_code_agent(
    *,
    user_message: str,
    project_root: Path | str,
    model: str = DEFAULT_MODEL,
    max_steps: int = DEFAULT_MAX_STEPS,
    conversation_history: list[dict[str, Any]] | None = None,
    run_id: str | None = None,
    num_ctx: int = DEFAULT_NUM_CTX,
    auto_remember: bool = True,
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
        # Anti-refusal nudge: if user clearly asks to execute, remind the model.
        effective_user_message = _maybe_inject_execution_reminder(user_message)
        messages.append({"role": "user", "content": effective_user_message})

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
                response = chat(
                    model=model,
                    messages=messages,
                    tools=tool_schemas,
                    options={"num_ctx": int(num_ctx)},
                )
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

            # Some models (qwen2.5-coder etc.) emit tool calls as JSON in
            # content instead of structured tool_calls. Recover them so the
            # loop still works.
            inline_calls: list[dict[str, Any]] = []
            if not tool_calls and content:
                inline_calls = _extract_inline_tool_calls(content, set(dispatch.keys()))
                if inline_calls:
                    tool_calls = inline_calls
                    content = ""  # JSON was the tool call, not a text reply

            if content:
                last_text = content

            if not tool_calls:
                final_text = content or last_text
                yield {"type": "final_response", "step": step, "text": final_text}
                if auto_remember:
                    _try_remember_turn(
                        user_message=user_message,
                        response_text=final_text,
                        project_root=root,
                    )
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
                # Smart-truncate tool output before feeding it back to the
                # LLM. Without this, a single huge `run_bash` or `read_file`
                # could blow out `num_ctx` and start eating the system
                # prompt off the front of the context.
                messages.append({
                    "role": "tool",
                    "content": _truncate_for_llm(text_result),
                    "name": name,
                })

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
    num_ctx: int = DEFAULT_NUM_CTX,
    auto_remember: bool = True,
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
        num_ctx=num_ctx,
        auto_remember=auto_remember,
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


SUMMARIZE_SYSTEM_PROMPT = (
    "Ты сжимаешь предыдущий диалог пользователя с code-агентом в краткое summary "
    "которое заменит исходные сообщения в контексте, чтобы освободить токены.\n"
    "Сохрани:\n"
    "- пути к файлам и модули которые обсуждались\n"
    "- архитектурные решения и договорённости\n"
    "- состояние задач (что сделано, что не доделано)\n"
    "- конвенции/стиль/правила которые пользователь упоминал\n"
    "- найденные баги и их статус\n"
    "Не пиши:\n"
    "- полное содержимое файлов\n"
    "- результаты tool calls\n"
    "- общие фразы и вежливость\n"
    "Формат: маркированный список 5-15 строк, плотный, без воды. На русском."
)


def summarize_history(
    messages: list[dict[str, Any]],
    model: str = DEFAULT_MODEL,
    num_ctx: int = DEFAULT_NUM_CTX,
    chat_fn: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Ask the LLM to summarize the given user/assistant messages into a
    compact bulleted summary. Returns {'ok': bool, 'summary': str,
    'error': str | None, 'turn_count': int}.

    The summary is one assistant-shaped text block intended to replace
    the original messages in conversation_history on the next agent
    invocation.
    """
    cleaned = _coerce_history(messages)
    if not cleaned:
        return {"ok": True, "summary": "", "error": None, "turn_count": 0}

    chat = chat_fn or _ollama_chat

    # Build a compact transcript to summarize. Two layers of protection:
    #
    #   1. Per-message cap (4000 chars) so a single long agent answer
    #      doesn't dominate the input.
    #   2. Total transcript cap (TRANSCRIPT_CAP, ~30K chars ≈ 7.5K
    #      tokens) so the whole prompt + SUMMARIZE_SYSTEM_PROMPT fits
    #      inside `num_ctx` with room to spare. Without this, a long
    #      session (50+ turns) would silently produce a garbage summary
    #      because Ollama would truncate our instructions off the front.
    #
    # When the cap kicks in we keep the MOST RECENT turns (oldest are
    # least relevant) and emit a marker so the LLM knows context is
    # incomplete.
    TRANSCRIPT_CAP = 30000
    PER_MESSAGE_CAP = 4000

    # First pass: walk newest -> oldest, take as much as fits.
    rev_lines: list[str] = []
    total = 0
    dropped_any = False
    for m in reversed(cleaned):
        role = m["role"]
        content = m["content"]
        if role == "system":
            # System messages (re-tagged summary turns from earlier
            # compressions) should appear with a distinctive prefix
            # so the summarizer treats them as prior summary context.
            prefix = "PRIOR_SUMMARY:"
        elif role == "user":
            prefix = "USER:"
        else:
            prefix = "AGENT:"
        if len(content) > PER_MESSAGE_CAP:
            content = content[:PER_MESSAGE_CAP] + " [...]"
        line = f"{prefix} {content}"
        # +2 for the "\n\n" separator we'll add when joining
        if rev_lines and total + len(line) + 2 > TRANSCRIPT_CAP:
            dropped_any = True
            break
        rev_lines.append(line)
        total += len(line) + 2

    lines = list(reversed(rev_lines))
    if dropped_any:
        lines.insert(
            0,
            "[... earlier messages dropped to stay under transcript cap; "
            "only most recent shown ...]",
        )
    transcript = "\n\n".join(lines)

    try:
        response = chat(
            model=model,
            messages=[
                {"role": "system", "content": SUMMARIZE_SYSTEM_PROMPT},
                {"role": "user", "content": "Диалог для сжатия:\n\n" + transcript},
            ],
            options={"num_ctx": int(num_ctx)},
        )
    except Exception as exc:
        logger.exception("Summarize history failed")
        return {"ok": False, "summary": "", "error": str(exc), "turn_count": len(cleaned)}

    msg = (response or {}).get("message") or {}
    text = (msg.get("content") or "").strip()
    return {"ok": True, "summary": text, "error": None, "turn_count": len(cleaned)}


DEFAULT_INDEX_PATTERNS = [
    "**/*.py", "**/*.ts", "**/*.tsx", "**/*.js", "**/*.jsx",
    "**/*.md", "**/*.rs", "**/*.go", "**/*.java", "**/*.cpp", "**/*.c", "**/*.h",
]
INDEX_SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", "target", ".pytest_cache", ".mypy_cache", "data"}
INDEX_CHUNK_LINES = 80
INDEX_CHUNK_OVERLAP = 10
INDEX_MAX_FILE_BYTES = 200_000  # skip files larger than 200 KB
INDEX_MAX_TOTAL_CHUNKS = 5000


def _chunk_file(file_path: Path, project_root: Path) -> Iterator[tuple[str, int, int]]:
    """Yield (text, start_line, end_line) chunks for one file. start_line is 1-based."""
    try:
        size = file_path.stat().st_size
    except OSError:
        return
    if size > INDEX_MAX_FILE_BYTES:
        return
    try:
        with file_path.open("r", encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except Exception:
        return
    if not lines:
        return
    rel = str(file_path.relative_to(project_root)).replace("\\", "/")
    step = max(1, INDEX_CHUNK_LINES - INDEX_CHUNK_OVERLAP)
    for start in range(0, len(lines), step):
        end = min(start + INDEX_CHUNK_LINES, len(lines))
        if end <= start:
            break
        chunk_text = "".join(lines[start:end])
        if not chunk_text.strip():
            continue
        header = f"[file:{rel}:{start + 1}-{end}]\n"
        yield header + chunk_text, start + 1, end
        if end >= len(lines):
            break


def _iter_project_files(project_root: Path, patterns: list[str]) -> Iterator[Path]:
    root = project_root.resolve()
    for pattern in patterns:
        for match in root.glob(pattern):
            if not match.is_file():
                continue
            if any(part in INDEX_SKIP_DIRS for part in match.parts):
                continue
            yield match


def index_project(
    project_root: Path | str,
    *,
    patterns: list[str] | None = None,
    replace: bool = True,
) -> dict[str, Any]:
    """Walk the project, chunk source files, write each chunk into RAG.
    If replace=True, prior code_index entries are nuked first.

    Returns counts of files / chunks processed and any per-file errors.
    """
    root = Path(project_root).resolve()
    if not root.exists() or not root.is_dir():
        return {"ok": False, "error": f"project_root does not exist: {root}"}

    try:
        from app.application.rag_memory.service import add_to_rag, _conn
    except Exception as exc:
        return {"ok": False, "error": f"RAG service unavailable: {exc}"}

    project_name = root.name or str(root)

    if replace:
        try:
            conn = _conn()
            try:
                # Only clear THIS project's code_index entries, not all
                # projects globally. Older rows without a project tag
                # are also cleared so a fresh re-index gets a clean slate.
                conn.execute(
                    """
                    DELETE FROM rag_items
                    WHERE category = ?
                      AND (project = ? OR COALESCE(project, '') = '')
                    """,
                    ("code_index", project_name),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception as exc:
            logger.warning("Failed to nuke prior code_index entries: %s", exc)

    pats = patterns or DEFAULT_INDEX_PATTERNS
    seen_files: set[Path] = set()
    files_processed = 0
    chunks_indexed = 0
    failed_chunks = 0
    errors: list[str] = []

    for file_path in _iter_project_files(root, pats):
        if file_path in seen_files:
            continue
        seen_files.add(file_path)
        files_processed += 1
        for chunk_text, _start, _end in _chunk_file(file_path, root):
            if chunks_indexed >= INDEX_MAX_TOTAL_CHUNKS:
                errors.append(f"Stopped at INDEX_MAX_TOTAL_CHUNKS={INDEX_MAX_TOTAL_CHUNKS}")
                break
            try:
                result = add_to_rag(
                    text=chunk_text,
                    category="code_index",
                    importance=4,
                    project=project_name,
                )
                if result.get("ok"):
                    chunks_indexed += 1
                else:
                    failed_chunks += 1
            except Exception as exc:
                failed_chunks += 1
                logger.debug("indexing chunk failed for %s: %s", file_path, exc)
        if chunks_indexed >= INDEX_MAX_TOTAL_CHUNKS:
            break

    return {
        "ok": True,
        "files_processed": files_processed,
        "chunks_indexed": chunks_indexed,
        "failed_chunks": failed_chunks,
        "patterns": pats,
        "errors": errors,
    }


def recall_from_rag(query: str, top_k: int = 10, min_score: float = 0.3) -> dict[str, Any]:
    """Thin wrapper for the UI to query RAG without going through the agent."""
    try:
        from app.application.rag_memory.service import search_rag
    except Exception as exc:
        return {"ok": False, "items": [], "error": f"RAG service unavailable: {exc}"}
    return search_rag(query=query, limit=max(1, int(top_k)), min_score=float(min_score))


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
