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
import queue
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Iterator

from app.application.tool_providers import (
    BuiltinToolProvider,
    SshToolProvider,
    ToolRegistry,
    build_mcp_providers,
)
from app.application.projects.scope import legacy_project_key, project_scope_id
from app.application.agent_kernel.executor import (
    ToolExecutionRequest,
    ToolExecutionResult,
    execute_tool as _kernel_exec,
)
from app.application.monitoring.inference import extract_llm_usage, record_inference_telemetry
from app.infrastructure.llm.openai_compatible import (
    chat_completion_event_stream,
    is_local_llm_model,
    local_llm_config,
)
# Project indexing/RAG was extracted to .indexing; re-exported here so existing
# importers (file_watcher, code_agent_routes, tests) keep importing these names
# from agent_loop unchanged.
from app.application.code_agent.indexing import (  # noqa: F401
    DEFAULT_INDEX_PATTERNS,
    INDEX_CHUNK_LINES,
    INDEX_CHUNK_OVERLAP,
    INDEX_MAX_FILE_BYTES,
    INDEX_MAX_TOTAL_CHUNKS,
    INDEX_SKIP_DIRS,
    index_project,
    recall_from_rag,
    reindex_file,
    unindex_file,
)
# Inline tool-call recovery extracted to .inline_tool_calls (used by the loop).
from app.application.code_agent.inline_tool_calls import _contains_tool_trace, _extract_inline_tool_calls
# System-prompt construction extracted to .prompts; re-exported so the loop and
# tests keep importing these from agent_loop unchanged.
from app.application.code_agent.prompts import (  # noqa: F401
    BASE_SYSTEM_PROMPT,
    _CODE_AGENT_BASE_TOOLS,
    _CODE_AGENT_READONLY_TOOLS,
    _build_base_system_prompt,
    _build_system_prompt,
)
# History coercion + rolling summarization extracted to .history; it imports
# nothing from agent_loop (a leaf), so re-exporting here keeps existing importers
# (code_agent_routes, tests) and the loop's `summarize_fn=summarize_history`
# resolving with no import cycle. DEFAULT_MODEL/DEFAULT_NUM_CTX live there because
# summarize_history binds them as default-arg values.
from app.application.code_agent.history import (  # noqa: F401
    DEFAULT_MODEL,
    DEFAULT_NUM_CTX,
    SUMMARIZE_SYSTEM_PROMPT,
    _SUMMARY_PREFIX,
    _coerce_history,
    _local_chat,
    _resolve_code_route,
    summarize_history,
)

logger = logging.getLogger(__name__)

# A long-thinking local model must not be cut off mid-task. The hard ceiling is
# a runaway-loop guard, not a "stop the agent" budget — real stopping is the
# user's Stop button plus the execution-time deadline, and hitting the ceiling
# yields a resumable partial ("Продолжить"), never an error.
DEFAULT_MAX_STEPS = 100
DEFAULT_MAX_EXECUTION_SECONDS = 600  # 10 min — big tasks on a slow local model
MAX_CODE_AGENT_STEPS = 200
PROJECT_PROMPT_FILENAME = ".elira/agent.md"
_LLM_HEARTBEAT_EVERY = 10.0
_REPEATED_TOOL_CALL_LIMIT = 4


def _chat_events(
    *,
    chat_fn: Callable[..., dict[str, Any]],
    chat_stream_fn: Callable[..., Any] | None,
    kwargs: dict[str, Any],
) -> Iterator[dict[str, Any]]:
    """Run a blocking provider call without leaving the SSE stream silent."""
    events: queue.Queue[tuple[str, Any]] = queue.Queue()

    def worker() -> None:
        try:
            if chat_stream_fn is None:
                events.put(("response", chat_fn(**kwargs)))
                return
            final_response: dict[str, Any] | None = None
            collected: list[str] = []
            for item in chat_stream_fn(**kwargs):
                if isinstance(item, str):
                    collected.append(item)
                    events.put(("delta", item))
                    continue
                if not isinstance(item, dict):
                    continue
                item_type = str(item.get("type") or "")
                if item_type == "delta":
                    text = str(item.get("content") or item.get("text") or "")
                    if text:
                        collected.append(text)
                        events.put(("delta", text))
                elif item_type == "message":
                    response = item.get("response")
                    if isinstance(response, dict):
                        final_response = response
            if final_response is None:
                final_response = {
                    "message": {"content": "".join(collected), "tool_calls": []},
                }
            events.put(("response", final_response))
        except Exception as exc:  # propagated in the caller thread
            events.put(("error", exc))
        finally:
            events.put(("done", None))

    threading.Thread(target=worker, name="elira-code-agent-llm", daemon=True).start()
    done = False
    while not done:
        try:
            kind, value = events.get(timeout=max(0.001, _LLM_HEARTBEAT_EVERY))
        except queue.Empty:
            yield {"type": "heartbeat"}
            continue
        if kind == "done":
            done = True
        elif kind == "error":
            raise value
        else:
            yield {"type": kind, "value": value}


def _local_chat_stream(**kwargs: Any) -> Iterator[dict[str, Any]]:
    model = str(kwargs.get("model") or "")
    if not is_local_llm_model(model):
        expected = local_llm_config().model
        raise RuntimeError(f"Local llama-server provider expects model '{expected}', got '{model}'.")
    yield from chat_completion_event_stream(
        model=model,
        messages=list(kwargs.get("messages") or []),
        tools=kwargs.get("tools"),
        options=kwargs.get("options"),
    )


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
    text-answer turn'. Some local tool-calling models occasionally drift
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


def _messages_char_count(messages: list[dict[str, Any]]) -> int:
    total = 0
    for item in messages:
        content = item.get("content")
        if isinstance(content, str):
            total += len(content)
    return total


WRAP_UP_PROMPT = (
    "[Лимит исчерпан: {reason}.] Больше НЕ вызывай инструменты. Кратко подведи "
    "итог для пользователя: что уже сделано (файлы, команды, результаты), что "
    "не доделано, какой следующий шаг."
)


def _short_arg_hint(args: dict[str, Any]) -> str:
    """Most identifying argument of a tool call, for the run's call log."""
    for key in ("path", "command", "pattern", "query"):
        val = args.get(key)
        if isinstance(val, str) and val:
            return val if len(val) <= 60 else val[:60] + "…"
    return ""


def _tool_started_requires_approval_delay(tool_name: str, args: dict[str, Any]) -> bool:
    """Delay live "started" UI until human approval has been granted."""
    try:
        from app.application.tool_registry.runtime import get_tool
        spec = get_tool(tool_name)
    except Exception:
        return False
    if not spec or spec.get("permission") != "require_approval":
        return False
    if tool_name == "run_bash":
        command = str(args.get("command", "")).strip()
        if command:
            try:
                from app.application.code_agent.tools import is_shell_safe
                return not is_shell_safe(command)
            except Exception:
                return True
    return True


# F1: while a tool call waits for human approval the loop pauses and polls
# the approval status. Module-level so tests can shrink the tick.
_APPROVAL_POLL_INTERVAL = 1.5
_APPROVAL_KEEPALIVE_EVERY = 10.0


def _approval_status(approval_id: str) -> str:
    """Current status of an approval row; 'pending' on any lookup problem."""
    try:
        from app.application.monitoring import runtime as _mon
        _mon.expire_old_approvals()
        row = _mon.get_approval(approval_id) or {}
        return str(row.get("status") or "pending")
    except Exception:
        return "pending"


def _flatten_for_summary(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """F3.1: convert in-loop messages (assistant with tool_calls, role="tool"
    results) into plain text turns the summarizer keeps. Without this,
    mid-run compaction summarized almost nothing: tool messages and
    empty-content assistant turns (typical while tool-calling) were dropped
    from the summarizer input by `_coerce_history`, so the agent forgot
    everything it had read in the run.
    """
    out: list[dict[str, Any]] = []
    for m in messages:
        role = m.get("role")
        content = m.get("content")
        text = content if isinstance(content, str) else ""
        if role == "tool":
            name = str(m.get("name") or "tool")
            excerpt = text[:400]
            out.append({"role": "assistant", "content": f"[tool result {name}] {excerpt}"})
            continue
        if role == "assistant":
            parts: list[str] = []
            if text:
                parts.append(text)
            calls = m.get("tool_calls") or []
            if calls:
                names: list[str] = []
                for c in calls[:12]:
                    fn = (c or {}).get("function") or {}
                    cname = str(fn.get("name") or "?")
                    args = fn.get("arguments")
                    hint = _short_arg_hint(args) if isinstance(args, dict) else ""
                    names.append(f"{cname}({hint})")
                parts.append("[tools called] " + "; ".join(names))
            if parts:
                out.append({"role": "assistant", "content": "\n".join(parts)})
            continue
        out.append(m)
    return out


class ContextBudgetError(RuntimeError):
    """Protected/recent context cannot fit the active server window."""


def _prepare_messages_for_llm(
    messages: list[dict[str, Any]],
    *,
    num_ctx: int,
    model: str,
    chat_fn: Callable[..., dict[str, Any]],
    context_profile: dict[str, Any],
    audit_sink: Callable[[dict[str, Any]], None] | None = None,
) -> tuple[list[dict[str, Any]], bool, dict[str, Any]]:
    """Compact at policy thresholds and enforce the effective request budget."""
    from app.application.context.compaction import maybe_compact
    from app.application.context.usage import get_context_usage

    usage_kwargs = {
        "ctx_size": num_ctx,
        "reserved_output_tokens": int(context_profile["reserved_output_tokens"]),
        "reserved_system_tokens": int(context_profile.get("reserved_system_tokens") or 4096),
        "safety_margin_tokens": int(context_profile.get("safety_margin_tokens") or 2048),
    }
    usage = get_context_usage(messages, **usage_kwargs)
    compacted = False
    compact_threshold = 60.0 if num_ctx < 16_384 else 75.0
    strong_threshold = 80.0 if num_ctx < 16_384 else 90.0
    should_compact = float(usage["percent"]) >= compact_threshold
    if not should_compact and num_ctx < 16_384 and len(messages) > 2:
        should_compact = True

    if should_compact:
        strong = float(usage["percent"]) >= strong_threshold
        messages, changed = maybe_compact(
            messages,
            num_ctx,
            model,
            chat_fn,
            summarize_fn=summarize_history,
            threshold=0.0,
            keep_pairs=1 if strong else 4,
            fallback_keep=2 if strong else 8,
            prepare_messages=_flatten_for_summary,
            audit_sink=audit_sink,
            trigger_reason="strong_compression" if strong else "auto_compression",
        )
        compacted = compacted or changed
        usage = get_context_usage(messages, **usage_kwargs)

    if float(usage["percent"]) >= strong_threshold:
        messages, changed = maybe_compact(
            messages,
            num_ctx,
            model,
            chat_fn,
            summarize_fn=summarize_history,
            threshold=0.0,
            keep_pairs=1,
            fallback_keep=2,
            prepare_messages=_flatten_for_summary,
            audit_sink=audit_sink,
            trigger_reason="strong_compression",
        )
        compacted = compacted or changed
        usage = get_context_usage(messages, **usage_kwargs)

    safe_input_budget = int(context_profile.get("safe_input_budget") or 0)
    if float(usage["percent"]) >= 95.0 or (
        safe_input_budget > 0 and int(usage["current_tokens"]) > safe_input_budget
    ):
        raise ContextBudgetError(
            "Контекст остаётся критически заполненным после сжатия: "
            f"{usage['current_tokens']} входных токенов, окно {usage['ctx_size']}. "
            "Начните новый чат или оставьте только необходимые материалы."
        )
    return messages, compacted, usage


def _wrap_up_text(
    chat: Callable[..., dict[str, Any]],
    model: str,
    num_ctx: int,
    messages: list[dict[str, Any]],
    call_log: list[str],
    reason: str,
) -> str:
    """F2: one best-effort no-tools LLM call to summarize an interrupted run
    (max_steps / deadline) — files on disk are already changed, the user must
    get «что сделано / что осталось». Falls back to a deterministic summary
    built from the run's call log. Deliberately outside inference telemetry:
    it is a single bounded closing call, not part of the tool loop.
    """
    try:
        response = chat(
            model=model,
            messages=messages + [{
                "role": "user",
                "content": WRAP_UP_PROMPT.format(reason=reason),
            }],
            options={"num_ctx": int(num_ctx)},
        )
        text = (((response or {}).get("message") or {}).get("content") or "").strip()
        if text:
            return text
    except Exception as exc:
        logger.warning("wrap-up summary call failed: %s", exc)
    if call_log:
        shown = "; ".join(call_log[:20])
        more = f" (+{len(call_log) - 20})" if len(call_log) > 20 else ""
        return (
            f"Прогон остановлен: {reason}. Выполнено вызовов: {len(call_log)} — "
            f"{shown}{more}. Изменения уже на диске; продолжи следующим сообщением."
        )
    return f"Прогон остановлен: {reason} — до первого вызова инструмента."


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
    scope_id = project_scope_id(project_root)
    summary = f"[agent_turn project={project_name}] task: {user} | outcome: {answer}"
    try:
        # Pass project= so the entry is scoped to this project and
        # recall() from a different project doesn't pull it up.
        add_to_rag(text=summary, category="agent_turn", importance=3, project=scope_id)
    except Exception as exc:
        logger.debug("auto-remember failed: %s", exc)


def _record_code_route_metric(run_id: str, decision: Any, effective_num_ctx: int, *, agent_id: str = "code-agent") -> None:
    """Best-effort routing provenance for code-agent (no schema change)."""
    try:
        from app.application.monitoring.runtime import record_metric

        record_metric(
            metric_type="model.routed",
            agent_id=agent_id or "code-agent",
            run_id=run_id,
            ok=True,
            details={
                "model": decision.model,
                "provider": decision.provider,
                "profile_id": decision.profile_id,
                "route": decision.route,
                "role": decision.role,
                "routing_source": decision.source,
                "requested_model": decision.requested_model,
                "effective_num_ctx": int(effective_num_ctx),
                "fallback_reason": decision.fallback_reason,
                "cloud_skipped": decision.cloud_skipped,
            },
        )
    except Exception as exc:
        logger.debug("model route metric recording failed", exc_info=exc)


_TOOL_SEARCH_SCHEMA = {
    "type": "function",
    "function": {
        "name": "tool_search",
        "description": (
            "Search for more tools by keyword and activate the relevant ones for "
            "THIS task. Use it whenever you need a capability you don't currently "
            "have (e.g. web search, http, sql, run a command). Eligible matches "
            "become callable on your next step."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Keywords describing the capability you need.",
                }
            },
            "required": ["query"],
        },
    },
}


def _schema_tool_name(schema: dict) -> str:
    return str((schema.get("function") or {}).get("name") or "")


def _stream_code_agent_core(
    *,
    user_message: str,
    project_root: Path | str,
    working_dir: Path | str | None = None,
    model: str = "auto",
    agent_id: str = "code-agent",
    max_steps: int = DEFAULT_MAX_STEPS,
    conversation_history: list[dict[str, Any]] | None = None,
    run_id: str | None = None,
    num_ctx: int = DEFAULT_NUM_CTX,
    base_tools: tuple[str, ...] | list[str] | None = None,
    execution_timeout_seconds: int | None = None,
    auto_remember: bool = True,
    chat_fn: Callable[..., dict[str, Any]] | None = None,
    chat_stream_fn: Callable[..., Any] | None = None,
    approval_wait_seconds: int = 300,
    compaction_audit_sink: Callable[[dict[str, Any]], None] | None = None,
) -> Iterator[dict[str, Any]]:
    """Stream the agent loop as events.

    Yields dicts with a `type` discriminator:
      - {"type": "run_started", "run_id": ...}
      - {"type": "step_started", "step": N}
      - {"type": "tool_started", "step": N, "tool": str, "arguments": dict}
      - {"type": "tool_call", "step": N, "tool": str, "arguments": dict,
         "result": str, "touched_path"?: str,
         "old_content"?: str, "new_content"?: str, "diff_action"?: str}
      - {"type": "final_response", "step": N, "text": str}
      - {"type": "done", "ok": bool, "steps": int, "stop_reason": str,
         "error": str | None}
    """
    root = Path(project_root).resolve()
    scope_id = project_scope_id(root)
    rid = run_id or uuid.uuid4().hex
    effective_agent_id = str(agent_id or "code-agent").strip() or "code-agent"
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

        safe_max_steps = max(1, min(int(max_steps), MAX_CODE_AGENT_STEPS))
        # P9.3: shared model routing (route='code') + effective num_ctx cap.
        # MODEL_SAFE_CTX is intentionally skipped here (see _resolve_code_route)
        # so code-agent keeps its large DEFAULT_NUM_CTX window.
        model, _effective_num_ctx, _route_decision = _resolve_code_route(model, num_ctx, agent_id=effective_agent_id)
        safe_num_ctx = max(1024, _effective_num_ctx)
        from app.application.context.profile import get_active_context_profile

        if chat_fn is None:
            discovered_profile = get_active_context_profile(model)
            safe_num_ctx = min(safe_num_ctx, int(discovered_profile["ctx_size"]))
        context_profile = get_active_context_profile(model, ctx_size=safe_num_ctx)
        _record_code_route_metric(rid, _route_decision, safe_num_ctx, agent_id=effective_agent_id)
        try:
            from app.application.agent_registry.sandbox import preflight_or_raise

            preflight = preflight_or_raise(
                agent_id=effective_agent_id,
                num_ctx=safe_num_ctx,
                run_id=rid,
                route=effective_agent_id,
                streaming=True,
            )
            execution_seconds = int(
                (preflight.get("limit") or {}).get(
                    "max_execution_seconds",
                    DEFAULT_MAX_EXECUTION_SECONDS,
                )
                or DEFAULT_MAX_EXECUTION_SECONDS
            )
            if execution_timeout_seconds is not None:
                execution_seconds = min(
                    execution_seconds,
                    max(1, int(execution_timeout_seconds)),
                )
        except Exception as exc:
            yield {"type": "run_started", "run_id": rid}
            yield {
                "type": "done",
                "ok": False,
                "steps": 0,
                "stop_reason": "error",
                "error": f"code-agent preflight blocked run: {exc}",
            }
            return
        deadline = time.monotonic() + max(1, execution_seconds)

        # Aggregate every tool source into one registry. The agent
        # loop only talks to the registry from here on.
        #   - BuiltinToolProvider is always on.
        #   - SshToolProvider auto-disables when the allowlist is
        #     empty, so adding it unconditionally costs nothing.
        #   - build_mcp_providers() returns one provider per RUNNING
        #     MCP server; servers that aren't started are skipped
        #     entirely (the user manages them through the MCP API
        #     routes / dialog).
        # The HTTP app seeds these at startup, but the runtime is also called
        # directly by tests and CLI integrations. Reuse the same idempotent
        # seeder so ToolExecutor never sees an unregistered built-in spec.
        from app.application.tool_registry.runtime import seed_builtin_tools

        seed_builtin_tools()
        registry = ToolRegistry([
            BuiltinToolProvider(root),
            SshToolProvider(),
            *build_mcp_providers(),
        ])
        all_schemas = registry.collect_schemas()
        # P10.1: enable deferred tool mode for THIS code-agent run — only the
        # base set is active initially; tool_search activates more (run-scoped).
        from app.application.agent_kernel.deferred_tools import (
            enable_deferred_tools,
            get_active_tools,
        )

        initial_tools = tuple(base_tools) if base_tools is not None else _CODE_AGENT_BASE_TOOLS
        enable_deferred_tools(rid, initial_tools)
        chat = chat_fn or _local_chat
        stream_chat = chat_stream_fn
        if chat_fn is None and stream_chat is None:
            stream_chat = _local_chat_stream

        system_prompt = _build_system_prompt(
            root, working_dir=working_dir, active_tools=initial_tools,
        )
        messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
        messages.extend(_coerce_history(conversation_history))
        # Anti-refusal nudge: if user clearly asks to execute, remind the model.
        effective_user_message = _maybe_inject_execution_reminder(user_message)
        messages.append({"role": "user", "content": effective_user_message})

        yield {"type": "run_started", "run_id": rid}

        last_text = ""
        tool_round_trips = 0
        compaction_count = 0
        call_log: list[str] = []
        repeated_tool_calls: dict[str, int] = {}
        for step in range(1, safe_max_steps + 1):
            if cancel_event.is_set():
                yield {
                    "type": "done",
                    "ok": False,
                    "steps": step - 1,
                    "stop_reason": "cancelled",
                    "error": "Cancelled by user",
                }
                return

            if time.monotonic() >= deadline:
                final_text = _wrap_up_text(
                    chat, model, safe_num_ctx, messages, call_log,
                    f"таймаут {execution_seconds}s",
                )
                yield {"type": "final_response", "step": step, "text": final_text}
                yield {
                    "type": "done",
                    "ok": False,
                    "steps": step - 1,
                    "stop_reason": "timeout",
                    "error": f"code-agent execution timed out after {execution_seconds}s",
                }
                return

            yield {"type": "step_started", "step": step}

            try:
                messages, _compacted, context_usage = _prepare_messages_for_llm(
                    messages,
                    num_ctx=safe_num_ctx,
                    model=model,
                    chat_fn=chat,
                    context_profile=context_profile,
                    audit_sink=compaction_audit_sink,
                )
            except ContextBudgetError as exc:
                yield {"type": "final_response", "step": step, "text": str(exc)}
                yield {
                    "type": "done",
                    "ok": False,
                    "steps": step - 1,
                    "stop_reason": "context_limit",
                    "error": str(exc),
                }
                return
            if _compacted:
                compaction_count += 1
                yield {
                    "type": "context_compacted",
                    "step": step,
                    "context": context_usage,
                }

            # P10.1: expose only this run's active tools + tool_search. Tools
            # activated by tool_search on a prior step become visible here.
            _active = get_active_tools(rid)
            step_schemas = [s for s in all_schemas if _schema_tool_name(s) in _active]
            step_schemas.append(_TOOL_SEARCH_SCHEMA)
            llm_prompt_chars = _messages_char_count(messages)
            llm_start = time.monotonic()
            try:
                response: dict[str, Any] = {}
                pending_delta = ""
                suppress_deltas = False
                llm_kwargs = {
                    "model": model,
                    "messages": messages,
                    "tools": step_schemas,
                    "options": {"num_ctx": safe_num_ctx},
                }
                for llm_event in _chat_events(
                    chat_fn=chat,
                    chat_stream_fn=stream_chat,
                    kwargs=llm_kwargs,
                ):
                    if llm_event["type"] == "heartbeat":
                        yield {"type": "heartbeat", "step": step}
                        continue
                    if llm_event["type"] == "response":
                        response = dict(llm_event["value"] or {})
                        continue
                    if llm_event["type"] != "delta":
                        continue
                    pending_delta += str(llm_event["value"] or "")
                    marker_text = pending_delta.lower()
                    if "<tool" in marker_text or "<function=" in marker_text:
                        suppress_deltas = True
                        pending_delta = ""
                        continue
                    if not suppress_deltas and len(pending_delta) > 32:
                        visible = pending_delta[:-32]
                        pending_delta = pending_delta[-32:]
                        if visible:
                            yield {"type": "delta", "step": step, "text": visible}
                response_content = str(((response.get("message") or {}).get("content") or ""))
                if not suppress_deltas and not _contains_tool_trace(response_content) and pending_delta:
                    yield {"type": "delta", "step": step, "text": pending_delta}
            except Exception as exc:
                llm_duration_ms = int((time.monotonic() - llm_start) * 1000)
                record_inference_telemetry(
                    agent_id=effective_agent_id,
                    run_id=rid,
                    route=str(getattr(_route_decision, "route", "") or "code"),
                    model=model,
                    provider=str(getattr(_route_decision, "provider", "") or ""),
                    profile_id=str(getattr(_route_decision, "profile_id", "") or ""),
                    role=str(getattr(_route_decision, "role", "") or ""),
                    routing_source=str(getattr(_route_decision, "source", "") or ""),
                    requested_model=str(getattr(_route_decision, "requested_model", "") or ""),
                    num_ctx=safe_num_ctx,
                    ok=False,
                    duration_ms=llm_duration_ms,
                    streaming=False,
                    prompt_chars=llm_prompt_chars,
                    tool_round_trips=tool_round_trips,
                    compaction_count=compaction_count,
                    fallback_count=1 if getattr(_route_decision, "fallback_reason", None) else 0,
                    error_category="llm_error",
                )
                logger.exception("LLM chat failed at step %d", step)
                yield {
                    "type": "done",
                    "ok": False,
                    "steps": step - 1,
                    "stop_reason": "error",
                    "error": str(exc),
                }
                return

            llm_duration_ms = int((time.monotonic() - llm_start) * 1000)
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
            step_usage = extract_llm_usage(response)
            record_inference_telemetry(
                agent_id=effective_agent_id,
                run_id=rid,
                route=str(getattr(_route_decision, "route", "") or "code"),
                model=model,
                provider=str(getattr(_route_decision, "provider", "") or ""),
                profile_id=str(getattr(_route_decision, "profile_id", "") or ""),
                role=str(getattr(_route_decision, "role", "") or ""),
                routing_source=str(getattr(_route_decision, "source", "") or ""),
                requested_model=str(getattr(_route_decision, "requested_model", "") or ""),
                num_ctx=safe_num_ctx,
                ok=True,
                duration_ms=llm_duration_ms,
                streaming=False,
                usage=step_usage,
                prompt_chars=llm_prompt_chars,
                completion_chars=len(content),
                tool_round_trips=tool_round_trips,
                compaction_count=compaction_count,
                fallback_count=1 if getattr(_route_decision, "fallback_reason", None) else 0,
            )
            # Live token usage for the frontend context meter / tok-s readout.
            # Reuses extract_llm_usage (the telemetry source); not a second path.
            yield {
                "type": "usage",
                "step": step,
                "prompt_tokens": int(step_usage.get("prompt_tokens") or 0),
                "completion_tokens": int(step_usage.get("completion_tokens") or 0),
                "total_tokens": int(step_usage.get("total_tokens") or 0),
                "tokens_per_second": round(float(step_usage.get("tokens_per_second") or 0.0), 1),
                "context": context_usage,
                "profile": context_profile,
            }

            # Some local tool-calling models emit tool calls as JSON in
            # content instead of structured tool_calls. Recover them so the
            # loop still works.
            inline_calls: list[dict[str, Any]] = []
            if not tool_calls and content:
                inline_calls = _extract_inline_tool_calls(content, registry.known_tools())
                if inline_calls:
                    tool_calls = inline_calls
                    content = ""  # JSON was the tool call, not a text reply

            if not tool_calls and _contains_tool_trace(content):
                messages.append({
                    "role": "user",
                    "content": (
                        "[internal correction] Tool trace was malformed or unavailable. "
                        "Do not expose internal tool markup. Use a currently available "
                        "structured tool call or answer plainly."
                    ),
                })
                call_log.append("blocked malformed internal tool trace")
                continue

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
                if time.monotonic() >= deadline:
                    final_text = _wrap_up_text(
                        chat, model, safe_num_ctx, messages, call_log,
                        f"таймаут {execution_seconds}s",
                    )
                    yield {"type": "final_response", "step": step, "text": final_text}
                    yield {
                        "type": "done",
                        "ok": False,
                        "steps": step,
                        "stop_reason": "timeout",
                        "error": f"code-agent execution timed out after {execution_seconds}s",
                    }
                    return
                fn = call.get("function") or {}
                name = fn.get("name") or ""
                raw_args = fn.get("arguments") or {}
                parsed_args = ToolRegistry._coerce_args(raw_args)
                fingerprint = json.dumps(
                    {"tool": name, "arguments": parsed_args},
                    ensure_ascii=False,
                    sort_keys=True,
                    default=str,
                )
                repeated_tool_calls[fingerprint] = repeated_tool_calls.get(fingerprint, 0) + 1
                if repeated_tool_calls[fingerprint] >= _REPEATED_TOOL_CALL_LIMIT:
                    final_text = _wrap_up_text(
                        chat,
                        model,
                        safe_num_ctx,
                        messages,
                        call_log,
                        f"repeated tool call: {name}",
                    )
                    yield {"type": "final_response", "step": step, "text": final_text}
                    yield {
                        "type": "done",
                        "ok": False,
                        "steps": step,
                        "stop_reason": "loop_guard",
                        "error": f"repeated identical tool call: {name}",
                    }
                    return
                if name == "tool_search":
                    # P10.1 meta-tool: inject the current run_id (the model never
                    # supplies it), search + activate eligible tools for this run.
                    # Read-only; not routed through the provider/executor path.
                    from app.application.code_agent.tools import tool_search as _tool_search

                    yield {
                        "type": "tool_started",
                        "step": step,
                        "tool": name,
                        "arguments": parsed_args,
                    }
                    _ts = _tool_search(run_id=rid, query=str(parsed_args.get("query", "")))
                    _ts_text = str(_ts.get("text", ""))
                    yield {
                        "type": "tool_call",
                        "step": step,
                        "tool": name,
                        "arguments": parsed_args,
                        "result": _truncate(_ts_text),
                    }
                    messages.append({
                        "role": "tool",
                        "content": _truncate_for_llm(_ts_text),
                        "name": name,
                    })
                    tool_round_trips += 1
                    call_log.append(f"tool_search({_short_arg_hint(parsed_args)})")
                    continue
                if name == "todo_update":
                    # P12.1: checklist mutations are bound to the current run.
                    # The model never chooses the run_id; executor policy/audit
                    # still applies below because todo_update is a normal tool.
                    parsed_args["run_id"] = rid
                if name == "delegate_task":
                    # P12.2: subagents are children of the current run. The
                    # model chooses role/task, not parent_run_id.
                    parsed_args["run_id"] = rid
                _request = ToolExecutionRequest(
                    run_id=rid,
                    agent_id=effective_agent_id,
                    project_scope_id=scope_id,
                    tool_name=name,
                    args=parsed_args,
                    source="code_agent",
                )
                _delay_tool_started = _tool_started_requires_approval_delay(name, parsed_args)
                if not _delay_tool_started:
                    yield {
                        "type": "tool_started",
                        "step": step,
                        "tool": name,
                        "arguments": parsed_args,
                    }
                _exec_result = _kernel_exec(_request, dispatch_fn=registry.dispatch_raw)
                # F1: pause the loop while a human decides, instead of telling
                # the model "waiting approval" and burning steps. The approval
                # is consumed in the SAME run (binding incl. run_id intact).
                _approval_id = str((_exec_result.output or {}).get("approval_id") or "")
                if (
                    _exec_result.status == "waiting_approval"
                    and approval_wait_seconds > 0
                    and _approval_id
                ):
                    yield {
                        "type": "approval_pending",
                        "step": step,
                        "tool": name,
                        "arguments": parsed_args,
                        "approval_id": _approval_id,
                    }
                    _wait_started = time.monotonic()
                    _last_keepalive = _wait_started
                    _decision = "timeout"
                    while time.monotonic() - _wait_started < approval_wait_seconds:
                        if cancel_event.is_set():
                            _decision = "cancelled"
                            break
                        _status = _approval_status(_approval_id)
                        if _status == "approved":
                            _decision = "approved"
                            break
                        if _status in {"rejected", "expired"}:
                            _decision = _status
                            break
                        _now = time.monotonic()
                        if _now - _last_keepalive >= _APPROVAL_KEEPALIVE_EVERY:
                            yield {
                                "type": "approval_wait",
                                "step": step,
                                "approval_id": _approval_id,
                                "waited_s": int(_now - _wait_started),
                            }
                            _last_keepalive = _now
                        time.sleep(_APPROVAL_POLL_INTERVAL)
                    # Human deliberation must not consume the agent's budget.
                    deadline += time.monotonic() - _wait_started
                    if _decision == "cancelled":
                        yield {
                            "type": "done",
                            "ok": False,
                            "steps": step,
                            "stop_reason": "cancelled",
                            "error": "Cancelled by user",
                        }
                        return
                    if _decision == "approved":
                        # Re-execute the same request: the executor finds the
                        # approved record, marks it used and dispatches.
                        if _delay_tool_started:
                            yield {
                                "type": "tool_started",
                                "step": step,
                                "tool": name,
                                "arguments": parsed_args,
                            }
                        _exec_result = _kernel_exec(_request, dispatch_fn=registry.dispatch_raw)
                    elif _decision == "rejected":
                        _exec_result = ToolExecutionResult(
                            status="blocked",
                            output={
                                "ok": False,
                                "text": (
                                    "Пользователь отклонил это действие. Не повторяй "
                                    "вызов; скорректируй подход или заверши ход."
                                ),
                                "error": "approval_rejected",
                            },
                            error="approval_rejected",
                        )
                    else:  # timeout / expired
                        _exec_result = ToolExecutionResult(
                            status="blocked",
                            output={
                                "ok": False,
                                "text": (
                                    "Подтверждение не получено вовремя. Не повторяй "
                                    "вызов; сообщи пользователю и заверши ход."
                                ),
                                "error": "approval_timeout",
                            },
                            error="approval_timeout",
                        )
                tool_meta = _exec_result.output
                text_result = str(tool_meta.get("text", ""))
                event: dict[str, Any] = {
                    "type": "tool_call",
                    "step": step,
                    "tool": name,
                    "arguments": parsed_args,
                    "result": _truncate(text_result),
                    "ok": bool(tool_meta.get("ok", _exec_result.status == "ok")),
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
                tool_round_trips += 1
                _hint = _short_arg_hint(parsed_args)
                call_log.append(
                    f"{name}({_hint}) {'ok' if tool_meta.get('ok', True) else 'error'}"
                )
                # Smart-truncate tool output before feeding it back to the
                # LLM. Without this, a single huge `run_bash` or `read_file`
                # could blow out `num_ctx` and start eating the system
                # prompt off the front of the context.
                messages.append({
                    "role": "tool",
                    "content": _truncate_for_llm(text_result),
                    "name": name,
                })

        final_text = _wrap_up_text(
            chat, model, safe_num_ctx, messages, call_log,
            f"достигнут max_steps={safe_max_steps}",
        )
        yield {"type": "final_response", "step": safe_max_steps, "text": final_text}
        yield {
            "type": "done",
            "ok": True,
            "partial": True,
            "steps": safe_max_steps,
            "stop_reason": "max_steps",
            "error": None,
        }
    finally:
        # P10.1: drop the run's deferred allowlist on EVERY terminal exit
        # (success, max_steps, timeout, cancel, error).
        from app.application.agent_kernel.deferred_tools import clear_run

        clear_run(rid)
        _unregister_run(rid)


def stream_code_agent(
    *,
    user_message: str,
    project_root: Path | str,
    working_dir: Path | str | None = None,
    model: str = "auto",
    agent_id: str = "code-agent",
    max_steps: int = DEFAULT_MAX_STEPS,
    conversation_history: list[dict[str, Any]] | None = None,
    run_id: str | None = None,
    num_ctx: int = DEFAULT_NUM_CTX,
    base_tools: tuple[str, ...] | list[str] | None = None,
    execution_timeout_seconds: int | None = None,
    auto_remember: bool = True,
    chat_fn: Callable[..., dict[str, Any]] | None = None,
    chat_stream_fn: Callable[..., Any] | None = None,
    approval_wait_seconds: int = 300,
    resume: bool = False,
    access_mode: str = "project-workspace",
) -> Iterator[dict[str, Any]]:
    """Journalled public stream around the existing model/tool runtime."""
    from app.application.code_agent.run_journal import RunJournal, discover_capabilities

    rid = run_id or uuid.uuid4().hex
    initial_tools = tuple(base_tools) if base_tools is not None else _CODE_AGENT_BASE_TOOLS
    journal = RunJournal.load(rid) if resume else RunJournal(rid)
    request = {
        "user_message": user_message,
        "project_root": str(project_root),
        "working_dir": str(working_dir) if working_dir is not None else None,
        "model": model,
        "agent_id": agent_id,
        "max_steps": int(max_steps),
        "conversation_history": conversation_history or [],
        "num_ctx": int(num_ctx),
        "base_tools": list(initial_tools),
        "execution_timeout_seconds": execution_timeout_seconds,
        "auto_remember": bool(auto_remember),
        "access_mode": access_mode,
    }
    terminal = False
    try:
        if resume:
            journal.resume()
            resume_event = {
                "type": "run_resumed",
                "run_id": rid,
                "from_step": int(journal.state.get("last_successful_step") or 0),
            }
            journal.append_event(resume_event)
            yield resume_event
        else:
            capabilities = discover_capabilities(model=model, tools=list(initial_tools))
            journal.start(request, capabilities)
            for missing in capabilities.get("missing", []):
                journal.append_event({
                    "type": "missing_capability",
                    "capability": missing,
                })

        def audit_sink(payload: dict[str, Any]) -> None:
            journal.append_event({"type": "compaction_audit", **payload})

        for raw_event in _stream_code_agent_core(
            user_message=user_message,
            project_root=project_root,
            working_dir=working_dir,
            model=model,
            agent_id=agent_id,
            max_steps=max_steps,
            conversation_history=conversation_history,
            run_id=rid,
            num_ctx=num_ctx,
            base_tools=base_tools,
            execution_timeout_seconds=execution_timeout_seconds,
            auto_remember=auto_remember,
            chat_fn=chat_fn,
            chat_stream_fn=chat_stream_fn,
            approval_wait_seconds=approval_wait_seconds,
            compaction_audit_sink=audit_sink,
        ):
            event = dict(raw_event)
            event.setdefault("run_id", rid)
            if resume and event.get("type") == "run_started":
                continue
            if event.get("type") == "done":
                event["resumable"] = bool(
                    event.get("partial")
                    or event.get("stop_reason") in {"timeout", "error", "context_limit"}
                )
                terminal = True
            if event.get("type") == "tool_started":
                journal.append_event({
                    "type": "tool_decision",
                    "step": event.get("step"),
                    "tool": event.get("tool"),
                    "reason": "selected by routed code model",
                })
                if event.get("tool") == "run_bash":
                    journal.append_event({
                        "type": "command_started",
                        "step": event.get("step"),
                        "command": (event.get("arguments") or {}).get("command"),
                    })
            if event.get("type") in {"tool_started", "tool_call"}:
                journal.append_command(event)
            journal.append_event(event)
            if event.get("type") == "tool_call":
                result_text = str(event.get("result") or "")
                tool_ok = bool(event.get("ok", not result_text.lower().startswith("error")))
                journal.append_event({
                    "type": "tool_completed" if tool_ok else "tool_failed",
                    "step": event.get("step"),
                    "tool": event.get("tool"),
                    "ok": tool_ok,
                })
                if event.get("tool") == "run_bash":
                    journal.append_event({
                        "type": "command_output_tail",
                        "step": event.get("step"),
                        "output": result_text[-4000:],
                        "ok": tool_ok,
                    })
                if event.get("touched_path"):
                    journal.append_event({
                        "type": "file_changed",
                        "step": event.get("step"),
                        "path": event.get("touched_path"),
                        "action": event.get("diff_action"),
                    })
                if event.get("tool") in {"web_search", "web_fetch"}:
                    journal.append_event({
                        "type": "web_source",
                        "step": event.get("step"),
                        "tool": event.get("tool"),
                        "query": (event.get("arguments") or {}).get("query"),
                        "url": (event.get("arguments") or {}).get("url"),
                        "checked_at": time.time(),
                    })
            elif event.get("type") == "usage":
                journal.append_event({
                    "type": "model_call",
                    "step": event.get("step"),
                    "model": model,
                    "prompt_tokens": event.get("prompt_tokens"),
                    "completion_tokens": event.get("completion_tokens"),
                    "tokens_per_second": event.get("tokens_per_second"),
                })
            elif event.get("type") == "done":
                journal.append_event({
                    "type": "run_completed" if event.get("ok") else "run_failed",
                    "steps": event.get("steps"),
                    "stop_reason": event.get("stop_reason"),
                    "resumable": event.get("resumable"),
                })
            yield event
    except Exception as exc:
        logger.exception("code-agent run journal failed for %s", rid)
        event = {
            "type": "done",
            "run_id": rid,
            "ok": False,
            "steps": int(journal.state.get("last_successful_step") or 0),
            "stop_reason": "error",
            "error": str(exc),
            "resumable": True,
        }
        try:
            journal.append_event(event)
        except Exception:
            logger.exception("failed to persist terminal event for %s", rid)
        terminal = True
        yield event
    finally:
        journal.finish(interrupted=not terminal)


def run_code_agent(
    *,
    user_message: str,
    project_root: Path | str,
    working_dir: Path | str | None = None,
    model: str = "auto",
    agent_id: str = "code-agent",
    max_steps: int = DEFAULT_MAX_STEPS,
    conversation_history: list[dict[str, Any]] | None = None,
    run_id: str | None = None,
    num_ctx: int = DEFAULT_NUM_CTX,
    base_tools: tuple[str, ...] | list[str] | None = None,
    execution_timeout_seconds: int | None = None,
    auto_remember: bool = True,
    chat_fn: Callable[..., dict[str, Any]] | None = None,
    chat_stream_fn: Callable[..., Any] | None = None,
    approval_wait_seconds: int = 0,
    access_mode: str = "project-workspace",
) -> dict[str, Any]:
    """Synchronous single-shot wrapper around stream_code_agent. Drains
    the generator and aggregates the result into the legacy dict shape.
    Legacy default: no approval pause (approval_wait_seconds=0).
    """
    tool_calls_log: list[dict[str, Any]] = []
    response_text = ""
    ok = False
    stop_reason = "error"
    error: str | None = None
    partial = False
    steps = 0

    for event in stream_code_agent(
        user_message=user_message,
        project_root=project_root,
        working_dir=working_dir,
        model=model,
        agent_id=agent_id,
        max_steps=max_steps,
        conversation_history=conversation_history,
        run_id=run_id,
        num_ctx=num_ctx,
        base_tools=base_tools,
        execution_timeout_seconds=execution_timeout_seconds,
        auto_remember=auto_remember,
        chat_fn=chat_fn,
        chat_stream_fn=chat_stream_fn,
        approval_wait_seconds=approval_wait_seconds,
        access_mode=access_mode,
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
            partial = bool(event.get("partial"))
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
        "partial": partial,
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


def init_project_prompt(project_root: Path | str, content: str | None = None) -> dict[str, Any]:
    from app.application.instructions.loader import init_project_instructions

    return init_project_instructions(project_root, content=content)


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
