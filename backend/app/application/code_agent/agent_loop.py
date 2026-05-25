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
DEFAULT_NUM_CTX = 16384  # Ollama's default is 2048 — way too small for tool-using agents.
PROJECT_PROMPT_FILENAME = ".elira/agent.md"

BASE_SYSTEM_PROMPT = (
    "Ты — code-агент Elira. Работаешь в локальной директории проекта пользователя.\n"
    "У тебя есть инструменты: read_file, write_file, edit_file, glob, grep, recall, run_bash.\n"
    "Все пути — относительно корня проекта. Действуй пошагово: смотри, правь, проверяй.\n"
    "Перед записью файла читай его, чтобы не затереть существующий код. "
    "После изменений запускай `run_bash` чтобы проверить что всё работает.\n"
    "Используй `recall(query)` чтобы семантически искать в RAG-памяти: "
    "проиндексированные куски кода и summary прошлых задач. Полезно когда "
    "нужно понять «где это реализовано» или «что я делал в прошлый раз».\n"
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
        add_to_rag(text=summary, category="agent_turn", importance=3)
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

    # Build a compact transcript to summarize. Truncate any single
    # message to keep the prompt itself sane.
    lines: list[str] = []
    for m in cleaned:
        role = m["role"]
        content = m["content"]
        if len(content) > 4000:
            content = content[:4000] + " [...]"
        prefix = "USER:" if role == "user" else "AGENT:"
        lines.append(f"{prefix} {content}")
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

    if replace:
        try:
            conn = _conn()
            try:
                conn.execute("DELETE FROM rag_items WHERE category = ?", ("code_index",))
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
                result = add_to_rag(text=chunk_text, category="code_index", importance=4)
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
