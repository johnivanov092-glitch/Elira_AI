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
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Iterator

from app.application.tool_providers import (
    BuiltinToolProvider,
    SshToolProvider,
    ToolRegistry,
    build_lsp_providers,
    build_mcp_providers,
)
from app.application.code_agent.progress import ProgressEvaluator
from app.application.projects.scope import project_scope_id
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
from app.application.code_agent.inline_tool_calls import (
    _contains_tool_trace,
    _extract_inline_tool_calls,
    _strip_tool_call_markup,
)
# D3 — structured action envelopes (opt-in, gated behind ELIRA_ACTION_ENVELOPES).
from app.application.code_agent.action_envelopes import (
    REPAIR_INSTRUCTION,
    envelopes_enabled,
    validate_tool_request,
)
# System-prompt construction extracted to .prompts; re-exported so the loop and
# tests keep importing these from agent_loop unchanged.
from app.application.code_agent.prompts import (  # noqa: F401
    BASE_SYSTEM_PROMPT,
    _CODE_AGENT_BASE_TOOLS,
    _CODE_AGENT_READONLY_TOOLS,
    _build_base_system_prompt,
    _build_system_prompt,
)
from app.application.persona.service import mode_temperature, mode_tool_posture
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
# Layer C (deterministic "no changes" cross-check) extracted to .layer_c; a leaf
# importing nothing from agent_loop. Re-exported so the loop and tests keep
# importing these from agent_loop unchanged.
from app.application.code_agent.layer_c import (  # noqa: F401
    _NO_CHANGE_CLAIM_MARKERS,
    _claims_no_changes,
    _layer_c_correction,
)
# Project-prompt CRUD extracted to .project_prompt; a leaf. Re-exported (with
# PROJECT_PROMPT_FILENAME) so code_agent_routes and tests keep importing these
# from agent_loop unchanged.
from app.application.code_agent.project_prompt import (  # noqa: F401
    PROJECT_PROMPT_FILENAME,
    get_project_prompt,
    get_verify_command,
    init_project_prompt,
    set_project_prompt,
    set_verify_command,
    suggest_verify_command,
)

logger = logging.getLogger(__name__)

# A long-thinking local model must not be cut off mid-task. The hard ceiling is
# a runaway-loop guard, not a "stop the agent" budget — real stopping is the
# user's Stop button plus the execution-time deadline, and hitting the ceiling
# yields a resumable partial ("Продолжить"), never an error.
DEFAULT_MAX_STEPS = 200
DEFAULT_MAX_EXECUTION_SECONDS = 600  # 10 min — big tasks on a slow local model
MAX_CODE_AGENT_STEPS = 200
# Per-request DRY sampler params (llama.cpp accepts them in the request body —
# verified against the live server). DRY penalises repeated token sequences at
# sampling time, so the model can't lock into a degenerate "same paragraph
# forever" loop. Originally think-only; extended to ALL runs after a live
# non-think run produced a paragraph repeated ×20 in the ANSWER channel (the
# content path had no anti-repeat protection at all). Safe for codegen: DRY's
# default sequence breakers ("\n" etc.) reset matching at line boundaries, so
# legitimate repeated code structure isn't penalised the way run-on prose is.
# DRY must look back only over roughly the CURRENT answer, NOT the whole chat.
# dry_penalty_last_n=-1 scanned the ENTIRE context (system prompt + every prior
# turn + tool output); as the conversation grew, DRY penalised ordinary repeated
# tokens (common words, code idioms, Cyrillic particles) carried over from
# earlier turns, so the model degenerated after a few turns — the real "breaks
# after 3-5 answers" bug, a harness fault, not the 35B model. Bound the window so
# DRY still kills a within-answer runaway loop (the ×20-paragraph case) but never
# reaches back into conversation history.
_DRY_WINDOW_TOKENS = 1024
_ANTI_REPEAT_SAMPLING = {
    "dry_multiplier": 0.8,
    "dry_base": 1.75,
    "dry_allowed_length": 2,
    "dry_penalty_last_n": _DRY_WINDOW_TOKENS,
    # Reset DRY matching at these separators so LEGITIMATELY-repeated IPs / MACs /
    # numbers / versions / paths (192.168.88.1, 2C-C8-1B, /24, v1.0) are not seen as
    # a penalizable repeat. Without "." DRY penalised the repeated octets of an IP,
    # and the model MUTATED them to dodge the penalty (live: 192.168→192.169→192.166
    # →192.170… during a network scan, plus a walk through 8.8.8.8/9.9.9.9/6.6.6.6).
    # Keeps the llama.cpp defaults (\n : " *) so repeated PROSE — the ×20-paragraph
    # runaway — is still caught (a repeated sentence resets only at its own period).
    # Digits are breakers too: a repeated number/model-code (RTX 5090 in every table
    # row, a price repeated down a column) was DRY-penalised and the model dropped a
    # digit to dodge it (live: "5090"→"509"/"090"). A digit resets the match, so
    # numbers survive verbatim; word-based degeneration (no digits) is still caught.
    "dry_sequence_breakers": [
        "\n", ":", "\"", "*", ".", "-", "/", ",", ";", "=",
        "0", "1", "2", "3", "4", "5", "6", "7", "8", "9",
    ],
}
# How many reasoning-runaway generations (provider cut the chain-of-thought at
# its per-generation ceiling) a single run tolerates before being force-
# finalized — the cross-step budget missing from the per-generation guard.
_REASONING_RUNAWAY_LIMIT = 2
# Malformed inline tool-trace recoveries are nudge-and-retry; bound them so a
# model stuck emitting broken tool markup can't burn all 200 steps.
_MALFORMED_TRACE_LIMIT = 3
# Opt-in verify gate (#2б): max times the loop re-runs `.elira/verify` and feeds
# a red result back before giving up and letting the run finalize.
_VERIFY_GATE_MAX = 3
_VERIFY_GATE_TIMEOUT_S = 300
# ask_user: max clarifying questions per run, so a lazy model asks instead of
# thinking only a bounded number of times.
_ASK_USER_MAX = 3
# ask_user owns its own termination (it is exempt from the generic loop-guard
# below). Past the budget the model is told to decide for itself; if it keeps
# asking anyway, finalize cleanly once it has exceeded the budget by this grace
# margin, instead of spinning to max_steps. Aligned so the hard cut lands at the
# same total (_ASK_USER_MAX + grace + 1 == _REPEATED_TOOL_CALL_LIMIT).
_ASK_USER_OVER_CAP_GRACE = 2
_LLM_HEARTBEAT_EVERY = 10.0
_REPEATED_TOOL_CALL_LIMIT = 6
# Repeats at or above this count (but below the hard limit) get a loud nudge
# appended to the tool result — a chance to change course before the run is
# stopped, instead of a silent hard cut at the first few repeats.
_REPEATED_TOOL_CALL_NUDGE_AT = 2
# Idempotent meta-tools whose repeats are harmless — re-sending them does not
# advance the run but also does not corrupt state, so they must NOT trip the
# loop-guard. `todo_update` in particular: local models routinely re-emit the
# full checklist (now upserted by position, so no duplicate rows), and a benign
# repeat used to kill the whole run. Read-only/idempotent by construction.
# `ask_user` is exempt too: it has its OWN dedicated per-run budget + clean
# terminal below (see _ASK_USER_MAX / _ASK_USER_OVER_CAP_GRACE), so the generic
# fingerprint guard must not double-govern it and end the run with a confusing
# loop_guard/error instead of a graceful finalize — especially in no_questions
# mode, where the canned reply gives a weak model nothing new to diverge on.
_LOOP_GUARD_EXEMPT_TOOLS = frozenset({"todo_update", "tool_search", "ask_user", "ssh_request_host"})
# Progress control lives in progress.ProgressEvaluator (the strategy router): a
# "doing" tool that moves no state burns its strategy_key's budget, exhaustion
# redirects to another family, and only when families/budget are spent does the
# run stop honestly. See docs/AGENT_RUNTIME_PLAN.md.
# SSH provider tools promoted into a run's OFFERED set on SSH-shaped tasks, so the
# model reaches ssh_run/ssh_write/ssh_run_ps directly instead of drowning in raw
# `ssh host "…"` through run_bash. Activated by intent (task mentions ssh / an
# allowlisted host) so non-SSH runs — and the prompt canaries — pay zero tokens.
_SSH_ACTIVATABLE_TOOLS = ("ssh_run", "ssh_read", "ssh_write", "ssh_run_ps", "ssh_list_hosts")
# Answers that count as approval for an ssh_request_host prompt (the "Одобрить"
# button, plus common free-text yes-words). Anything else = deny.
_SSH_APPROVE_WORDS = frozenset({"одобрить", "approve", "yes", "да", "allow", "ok", "разрешить"})

# Role-based sampling for a single served model (one large LLM plays every
# role — see resolve_model_for_route/route_to_role). The role does not switch
# the model (single-GPU server, one text LLM loaded at a time); it only tunes
# how deterministic the sampling is. Strict profile: code edits should be as
# reproducible as possible ("don't break the project"), planning/review may
# vary a little, casual replies a little more.
_ROLE_TEMPERATURE = {
    "code": 0.1,
    "strong": 0.3,
    "fast": 0.4,
}
_DEFAULT_TEMPERATURE = 0.2


def _temperature_for_role(role: str | None) -> float:
    """Sampling temperature for a routing role (strict profile, default 0.2)."""
    return _ROLE_TEMPERATURE.get((role or "").strip().lower(), _DEFAULT_TEMPERATURE)


def _effective_temperature(profile_name: str, role: str | None) -> float:
    """Persona-mode temperature wins when set (Личный/Баланс raise warmth);
    Инженерный (mode temperature=None) keeps the per-role sampling that protects
    code-edit reproducibility."""
    mode_temp = mode_temperature(profile_name)
    if mode_temp is not None:
        return float(mode_temp)
    return _temperature_for_role(role)


def _chat_events(
    *,
    chat_fn: Callable[..., dict[str, Any]],
    chat_stream_fn: Callable[..., Any] | None,
    kwargs: dict[str, Any],
    cancel_event: "threading.Event | None" = None,
) -> Iterator[dict[str, Any]]:
    """Run a blocking provider call without leaving the SSE stream silent.

    When ``cancel_event`` is set mid-stream the worker stops pulling tokens
    and closes the underlying generator (which closes the upstream HTTP
    response), so pressing Stop actually frees the server instead of letting
    it generate the full answer into a queue nobody reads.
    """
    events: queue.Queue[tuple[str, Any]] = queue.Queue()

    def worker() -> None:
        stream = None
        try:
            if chat_stream_fn is None:
                events.put(("response", chat_fn(**kwargs)))
                return
            final_response: dict[str, Any] | None = None
            collected: list[str] = []
            stream = chat_stream_fn(**kwargs)
            for item in stream:
                if cancel_event is not None and cancel_event.is_set():
                    break
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
                elif item_type == "reasoning":
                    # Thinking tokens — relayed on their own channel, never
                    # folded into `collected` (which becomes the answer).
                    rtext = str(item.get("content") or item.get("text") or "")
                    if rtext:
                        events.put(("reasoning", rtext))
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
            # Closing the generator triggers its `finally`, which calls
            # response.close() and frees the upstream llama.cpp connection.
            close = getattr(stream, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass
            events.put(("done", None))

    threading.Thread(target=worker, name="elira-code-agent-llm", daemon=True).start()
    done = False
    while not done:
        try:
            kind, value = events.get(timeout=max(0.001, _LLM_HEARTBEAT_EVERY))
        except queue.Empty:
            if cancel_event is not None and cancel_event.is_set():
                # Stop pumping the SSE stream immediately; the worker will
                # observe the same flag and close the upstream connection.
                return
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


def _exec_with_heartbeat(thunk, step):
    """Run a blocking tool call (thunk) in a daemon thread, yielding `heartbeat`
    events every _LLM_HEARTBEAT_EVERY seconds while it runs. A long tool (network
    scan, build, long test) otherwise goes silent, and the client's 90s SSE
    inactivity watchdog cuts the stream before the tool even returns
    («Соединение с агентом прервалось — нет ответа»). The FINAL yielded item is
    {'__result__': <ToolExecutionResult>}; the caller passes heartbeats through
    and unwraps the result."""
    box: dict[str, Any] = {}
    finished = threading.Event()

    def _run() -> None:
        try:
            box["r"] = thunk()
        except BaseException as exc:  # noqa: BLE001 — re-raised in the generator
            box["e"] = exc
        finally:
            finished.set()

    threading.Thread(target=_run, daemon=True).start()
    # Poll finer than the heartbeat interval, else a tool that finishes between
    # polls (or a small interval) never triggers the elapsed-time check.
    _poll = min(1.0, max(0.02, _LLM_HEARTBEAT_EVERY / 2.0))
    _last = time.monotonic()
    while not finished.wait(timeout=_poll):
        _now = time.monotonic()
        if _now - _last >= _LLM_HEARTBEAT_EVERY:
            yield {"type": "heartbeat", "step": step}
            _last = _now
    if "e" in box:
        raise box["e"]
    yield {"__result__": box["r"]}

# Pending ask_user questions: question_id -> answer (None = registered/awaiting,
# str = answered). The HTTP answer route writes here; the paused loop polls it.
# In-memory (like the cancel registry) — a restart drops the question and the
# answer route 404s, which the UI handles by clearing the stale card.
_QUESTION_ANSWERS: dict[str, str | None] = {}
_QUESTION_LOCK = threading.Lock()


def submit_answer(question_id: str, answer: str) -> bool:
    """Record a human answer to a paused ask_user question. Returns True if the
    question was known/awaiting, False otherwise (stale/unknown id)."""
    with _QUESTION_LOCK:
        if question_id not in _QUESTION_ANSWERS:
            return False
        _QUESTION_ANSWERS[question_id] = str(answer)
    return True


def request_cancel(run_id: str) -> bool:
    """Flip the cancel event for `run_id`. Returns True if the run was
    known, False otherwise.

    Beyond setting the flag (read between steps), this also KILLS any live
    shell process the run launched. A blocking tool runs in a daemon worker
    thread that never reads the event until it returns, so killing the OS
    process is what makes Stop abort a hung command immediately instead of
    waiting out the shell timeout.
    """
    # Kill live shell processes regardless of whether the event is registered,
    # so Stop works even on a run whose event was already cleaned up.
    try:
        from app.application.code_agent.tools import kill_run_processes
        kill_run_processes(run_id)
    except Exception:
        logger.warning("kill_run_processes failed for run %s", run_id, exc_info=True)
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


# Text/format, approval, context-window, RAG, telemetry and tool-search helpers
# were extracted to .loop_helpers (a leaf — imports nothing from agent_loop), and
# re-exported here so existing importers (tests, file_watcher) and the core loop
# below keep resolving these names from agent_loop unchanged. Because the core
# loop calls every one of them through this module's namespace, tests that
# `patch("...agent_loop.<name>")` still take effect.
from app.application.code_agent.loop_helpers import (  # noqa: F401
    ContextBudgetError,
    TOOL_RESULT_LLM_LIMIT,
    WRAP_UP_PROMPT,
    _APPROVAL_KEEPALIVE_EVERY,
    _APPROVAL_POLL_INTERVAL,
    _EXECUTION_INTENT,
    _ASK_USER_SCHEMA,
    _SSH_REQUEST_HOST_SCHEMA,
    _TOOL_SEARCH_SCHEMA,
    FACTS_PREFIX,
    _GROUNDING_NUDGE_MAX,
    _NEAR_DUP_LIMIT,
    _NEAR_DUP_NUDGE_AT,
    _approval_status,
    _arg_tokens,
    _deterministic_stop_summary,
    _fact_from_tool,
    _facts_digest,
    _recent_tool_snippet,
    _recent_tools_digest,
    RECENT_TOOLS_PREFIX,
    _is_near_dup,
    _ungrounded_files,
    _flatten_for_summary,
    _is_critical_call,
    _looks_like_intent_without_action,
    _looks_like_repeat_request,
    _mark_approval_approved,
    _maybe_inject_execution_reminder,
    _messages_char_count,
    _mode_auto_approves,
    _norm_answer,
    _normalized_fingerprint,
    _strip_think_blocks,
    _prepare_messages_for_llm,
    _record_code_route_metric,
    _schema_tool_name,
    _short_arg_hint,
    _tool_started_requires_approval_delay,
    _truncate,
    _truncate_for_llm,
    _try_remember_turn,
    _wrap_up_text,
)


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
    profile_name: str = "Инженерный",
    permission_mode: str = "ask",
    thinking: bool = False,
    no_questions: bool = False,
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
        # thinking=on reserves more output room (reasoning + answer share the
        # budget) so a long chain-of-thought never truncates the answer.
        context_profile = get_active_context_profile(model, ctx_size=safe_num_ctx, thinking=thinking)
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
            *build_lsp_providers(),
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
        # Persona mode posture: Личный narrows the OFFERED tools to read-only
        # (she does not reach for write/edit/run without being asked). This only
        # restricts what the model is offered — the fail-closed kernel still
        # gates every call independently, so a mode can never widen access.
        if mode_tool_posture(profile_name) == "readonly":
            narrowed = tuple(t for t in initial_tools if t in _CODE_AGENT_READONLY_TOOLS)
            initial_tools = narrowed or _CODE_AGENT_READONLY_TOOLS
        else:
            # SSH-shaped run → offer ssh_run/ssh_read/ssh_write/ssh_run_ps from step
            # one, so the model uses the clean remote tools instead of drowning in
            # raw `ssh host "…"` through run_bash. Intent-gated (task names ssh or an
            # allowlisted host) so normal runs and the prompt canaries pay nothing.
            try:
                from app.application.tool_providers.ssh_acl import (
                    get_allowed_hosts as _ssh_hosts,
                    is_ssh_enabled as _ssh_on,
                )

                if _ssh_on():
                    _low = (user_message or "").lower()
                    _hosts = [h.lower() for h in _ssh_hosts()]
                    if "ssh" in _low or any(h and h in _low for h in _hosts):
                        initial_tools = tuple(dict.fromkeys((*initial_tools, *_SSH_ACTIVATABLE_TOOLS)))
            except Exception:
                pass
        enable_deferred_tools(rid, initial_tools)
        chat = chat_fn or _local_chat
        stream_chat = chat_stream_fn
        if chat_fn is None and stream_chat is None:
            stream_chat = _local_chat_stream

        system_prompt = _build_system_prompt(
            root, working_dir=working_dir, active_tools=initial_tools, model_name=model,
            profile_name=profile_name, task_text=user_message,
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
        # Near-duplicate loop detection (see loop_helpers): catches a model
        # spamming ONE tool with slightly-varying args (ping/recall churn) that
        # the exact-fingerprint guard below misses. Streak = consecutive near-dups.
        near_dup_recent: list[tuple[str, frozenset[str]]] = []
        near_dup_streak = 0
        # Grounding across turns: compact facts the discovery tools revealed this
        # run, handed back next turn as an authoritative context block so the
        # model grounds instead of confabulating (see loop_helpers._fact_from_tool
        # and the FACTS_PREFIX block in the frontend history builder).
        established_facts: list[str] = []
        # Verbatim buffer of recent grounding-tool outputs (last few, in full-ish),
        # carried into the next turn alongside the compact facts digest.
        recent_tool_outputs: list[str] = []
        # Soft verification gate (Variant 2): if the run edited files but never
        # ran tests/lint or started the app, nudge the model to verify once
        # before it closes. Reminder-injection, not a hard block — and it fires
        # at most once, never on a no-edit (conversational/read-only) run.
        edited_in_run = False
        ran_verification = False
        verify_gate_fired = False
        # Hard verify gate (#2б, opt-in): if the project set `.elira/verify`, the
        # loop RUNS that command on finalize-after-edits and refuses to close
        # until it exits 0 — no rubber-stamped "проверено". Bounded so a
        # persistently-red command can't loop forever.
        verify_cmd = get_verify_command(root)
        verify_passed = False
        verify_attempts = 0
        # Anti-repeat gate: fires at most once if the model is about to echo its
        # PREVIOUS turn's answer verbatim to a DIFFERENT question (local-model
        # loop). prev_assistant_text = the last assistant reply from history.
        repeat_gate_fired = False
        # Anti-"narrate instead of act" gate: fires when the model ends its turn
        # on a forward-looking intent ("давай посмотрим…", "сейчас прочитаю…")
        # with no tool call and nothing edited yet — a plan-without-execute stop
        # (amplified by thinking). Nudges it to actually call the tool. Bounded
        # (a stubborn local model can repeat filler) — push again up to the cap,
        # then let it finalize rather than loop forever.
        intent_gate_fires = 0
        _INTENT_GATE_MAX = 2
        # Ungrounded-file nudge: fires when the finalizing answer names a project
        # file that nothing in this run grounds (residual confabulation leak, e.g.
        # inventing test_main.py/setup.py). Bounded; only on an actual unverified
        # file claim, so normal answers never see it.
        grounding_nudge_fires = 0
        # Cross-step budget for degenerate generations: the provider's runaway
        # guard is per-generation, so a model that loops its reasoning EVERY step
        # could burn all 200 steps in cut-off generations. Two runaway events in
        # one run → force-finalize (loop_guard-style). Malformed inline tool
        # traces get the same bounding (the recovery nudge used to be unlimited).
        reasoning_runaway_count = 0
        malformed_trace_count = 0
        ask_user_count = 0
        prev_assistant_text = next(
            (str(m.get("content") or "") for m in reversed(messages)
             if isinstance(m, dict) and m.get("role") == "assistant"),
            "",
        )
        # D3 — at most ONE envelope repair-retry per run, then deterministic
        # fallback to the existing inline-recovery behaviour. Only consulted
        # when ELIRA_ACTION_ENVELOPES is on.
        envelope_repair_fired = False
        # Strategy router: classifies each "doing" call into a strategy_key
        # (family+target), throttles a repeated method, redirects to another family
        # on exhaustion, and stops honestly only when families/budget are spent.
        progress = ProgressEvaluator()
        touched_files: list[str] = []  # every file the run mutated (for the report)
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
                from app.application.context.compaction import extract_rolling_summary

                rolling_summary = extract_rolling_summary(messages)
                yield {
                    "type": "context_compacted",
                    "step": step,
                    "context": context_usage,
                    "rolling_summary": rolling_summary or None,
                }

            # P10.1: expose only this run's active tools + tool_search. Tools
            # activated by tool_search on a prior step become visible here.
            _active = get_active_tools(rid)
            step_schemas = [s for s in all_schemas if _schema_tool_name(s) in _active]
            step_schemas.append(_TOOL_SEARCH_SCHEMA)
            step_schemas.append(_ASK_USER_SCHEMA)
            step_schemas.append(_SSH_REQUEST_HOST_SCHEMA)
            llm_prompt_chars = _messages_char_count(messages)
            llm_start = time.monotonic()
            try:
                response: dict[str, Any] = {}
                pending_delta = ""
                suppress_deltas = False
                llm_options: dict[str, Any] = {
                    "num_ctx": safe_num_ctx,
                    "active_context_limit": safe_num_ctx,
                    "temperature": _effective_temperature(
                        profile_name, getattr(_route_decision, "role", None)
                    ),
                }
                # Per-request DRY anti-repetition on EVERY run: the answer channel
                # degenerated into a ×20-paragraph loop on a non-think run, so the
                # protection can no longer be think-only.
                llm_options["sampling"] = dict(_ANTI_REPEAT_SAMPLING)
                # Thinking toggle (per-request, --jinja server): opt the run into
                # model reasoning without a server restart. Reasoning streams on a
                # separate channel below; the server default stays off when unset.
                if thinking:
                    llm_options["chat_template_kwargs"] = {"enable_thinking": True}
                llm_kwargs = {
                    "model": model,
                    "messages": messages,
                    "tools": step_schemas,
                    "options": llm_options,
                }
                for llm_event in _chat_events(
                    chat_fn=chat,
                    chat_stream_fn=stream_chat,
                    kwargs=llm_kwargs,
                    cancel_event=cancel_event,
                ):
                    if cancel_event.is_set():
                        break
                    if llm_event["type"] == "heartbeat":
                        yield {"type": "heartbeat", "step": step}
                        continue
                    if llm_event["type"] == "response":
                        response = dict(llm_event["value"] or {})
                        continue
                    if llm_event["type"] == "reasoning":
                        # Surface thinking tokens on their own SSE event so the UI
                        # can show them in a separate, collapsible block. Kept out
                        # of the answer stream and out of message history.
                        rtext = str(llm_event["value"] or "")
                        if rtext:
                            yield {"type": "reasoning_delta", "step": step, "text": rtext}
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
            # Safety net: reasoning/content separation relies on the SERVER's
            # template parsing. If that ever breaks (model swap, llama.cpp
            # update), raw <think> blocks would flow into content → history →
            # the model keeps reasoning as content. Strip them client-side too.
            content = _strip_think_blocks(message.get("content") or "").strip()
            tool_calls = message.get("tool_calls") or []
            # Cross-step runaway budget: the provider cut this generation's
            # reasoning at its ceiling. One event is survivable; repeated events
            # mean the model is stuck in a degenerate loop — force-finalize
            # instead of burning the remaining steps on cut-off generations.
            if (response or {}).get("reasoning_runaway"):
                reasoning_runaway_count += 1
                if reasoning_runaway_count >= _REASONING_RUNAWAY_LIMIT:
                    final_text = _wrap_up_text(
                        chat, model, safe_num_ctx, messages, call_log,
                        "модель зацикливается в рассуждениях",
                    )
                    yield {"type": "final_response", "step": step, "text": final_text}
                    yield {
                        "type": "done",
                        "ok": False,
                        "steps": step,
                        "stop_reason": "loop_guard",
                        "error": "reasoning runaway repeated; run finalized early",
                    }
                    return
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

            # D3 — opt-in strict tool-request validation on top of recovery.
            # OFF by default: when the flag is unset this block is skipped
            # entirely and the loop runs exactly as before. When on, every
            # tool call (structured or recovered) must validate as a clean
            # tool-request envelope (known tool + dict args). On the first
            # malformed turn we ask the model to re-send once; after that we
            # fall back to the existing behaviour rather than loop forever.
            if tool_calls and envelopes_enabled():
                known = registry.known_tools()
                all_valid = all(
                    validate_tool_request(c, known) is not None for c in tool_calls
                )
                if not all_valid and not envelope_repair_fired:
                    envelope_repair_fired = True
                    messages.append({"role": "user", "content": REPAIR_INSTRUCTION})
                    call_log.append("envelope repair: malformed tool_request")
                    continue
                # If still malformed after the one retry, fall through with the
                # recovered calls as-is (deterministic fallback to today's path).

            if not tool_calls and _contains_tool_trace(content):
                malformed_trace_count += 1
                if malformed_trace_count >= _MALFORMED_TRACE_LIMIT:
                    # Bounded: a model stuck emitting broken tool markup used to
                    # retry indefinitely (up to the step cap). Finalize instead.
                    final_text = _wrap_up_text(
                        chat, model, safe_num_ctx, messages, call_log,
                        "модель повторяет некорректную разметку вызова инструмента",
                    )
                    yield {"type": "final_response", "step": step, "text": final_text}
                    yield {
                        "type": "done",
                        "ok": False,
                        "steps": step,
                        "stop_reason": "loop_guard",
                        "error": "malformed tool trace repeated; run finalized early",
                    }
                    return
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
                # Hard verify gate (#2б, opt-in): a project with `.elira/verify`
                # can't be closed after edits until that command exits 0. The
                # LOOP runs it (not the model), so "готово" can't be rubber-
                # stamped. Bounded by _VERIFY_GATE_MAX; after that we fall through
                # and let the run finalize with the failure visible in history.
                if (
                    verify_cmd
                    and edited_in_run
                    and not verify_passed
                    and verify_attempts < _VERIFY_GATE_MAX
                ):
                    verify_attempts += 1
                    from app.application.code_agent.tools import tool_run_bash as _verify_run

                    yield {
                        "type": "tool_started", "step": step, "tool": "run_bash",
                        "arguments": {"command": verify_cmd},
                    }
                    _vres = _verify_run(root, command=verify_cmd, timeout=_VERIFY_GATE_TIMEOUT_S)
                    _vtext = str(_vres.get("text") or "")
                    _passed = any(ln.strip() == "exit=0" for ln in _vtext.splitlines())
                    ran_verification = True  # also satisfies the soft nudge below
                    yield {
                        "type": "tool_call", "step": step, "tool": "run_bash",
                        "arguments": {"command": verify_cmd},
                        "result": _vtext, "ok": _passed,
                    }
                    if _passed:
                        verify_passed = True  # fall through to finalize
                    else:
                        if content:
                            messages.append({"role": "assistant", "content": content})
                        messages.append({
                            "role": "user",
                            "content": (
                                f"Проверка проекта `{verify_cmd}` НЕ прошла — "
                                f"исправь причину и не заявляй «готово», пока она "
                                f"не станет зелёной. Вывод:\n\n{_truncate_for_llm(_vtext)}"
                            ),
                        })
                        continue
                # Soft verification gate (Variant 2): the model edited files this
                # run but never ran tests/lint or started the app, and is now
                # trying to close. Nudge it once to verify before finishing —
                # reminder-injection, not a hard block, fires at most once, and
                # never on a no-edit (conversational/read-only) run.
                if edited_in_run and not ran_verification and not verify_gate_fired:
                    verify_gate_fired = True
                    if content:
                        messages.append({"role": "assistant", "content": content})
                    messages.append({
                        "role": "user",
                        "content": (
                            "Стоп: ты правил файлы, но ещё не проверил результат. "
                            "Прежде чем закрывать задачу — прогони тесты и линтер "
                            "проекта через `run_bash` (обязательно), а приложение "
                            "по возможности подними через `run_server` и убедись, "
                            "что оно стартует. Если проверять реально нечего "
                            "(тестов/линтера в проекте нет) — так и скажи. Не "
                            "заявляй «готово» по факту записи файла."
                        ),
                    })
                    continue
                # Anti-repeat gate: the model is about to emit an answer identical
                # (after whitespace-normalisation) to its PREVIOUS turn's reply,
                # but the user asked something different — a local-model echo loop.
                # Nudge it ONCE to answer the new question (or admit it has no new
                # info/source), instead of serving the duplicate. Skipped when the
                # user explicitly asked to repeat.
                _answer = content or last_text
                if (
                    _answer
                    and prev_assistant_text
                    and not repeat_gate_fired
                    and _norm_answer(_answer) == _norm_answer(prev_assistant_text)
                    and not _looks_like_repeat_request(user_message)
                ):
                    repeat_gate_fired = True
                    messages.append({"role": "assistant", "content": _answer})
                    messages.append({
                        "role": "user",
                        "content": (
                            "Ты слово в слово повторил свой прошлый ответ, хотя "
                            "вопрос другой. Ответь именно на НОВЫЙ вопрос. Если по "
                            "нему у тебя нет новой информации или источника — честно "
                            "так и скажи (например «источник не найден / не "
                            "подтверждено»), но НЕ копируй прошлый ответ."
                        ),
                    })
                    continue
                # Anti-"narrate instead of act" gate: the model returned prose
                # with NO tool call, but the prose is a forward-looking intent
                # ("давай посмотрим…", "сейчас прочитаю…") and nothing was edited
                # yet — i.e. it announced the next step and stopped instead of
                # doing it (a plan-without-execute failure, amplified by
                # thinking). Nudge it to emit the tool call and do the work.
                # Bounded by _INTENT_GATE_MAX and only on a no-edit run, so a
                # genuine short answer (or a summary after real edits) is never
                # cut, and a stubborn model can't loop forever.
                _answer_intent = content or last_text
                if (
                    _answer_intent
                    and intent_gate_fires < _INTENT_GATE_MAX
                    and not edited_in_run
                    and _looks_like_intent_without_action(_answer_intent)
                ):
                    intent_gate_fires += 1
                    messages.append({"role": "assistant", "content": _answer_intent})
                    messages.append({
                        "role": "user",
                        "content": (
                            "Не описывай, что собираешься сделать — СДЕЛАЙ это "
                            "сейчас. Вызови нужный инструмент "
                            "(read_file/edit_file/write_file/run_bash) в этом же "
                            "ходу и доведи задачу до конца. Заканчивать ход одним "
                            "намерением («сейчас прочитаю…», «начну с…») без "
                            "вызова инструмента нельзя — это не выполненная "
                            "работа. Дай итог только когда правки реально внесены "
                            "и проверены."
                        ),
                    })
                    continue
                # Ungrounded-file nudge: the answer names project file(s) that
                # nothing in this run grounds (no tool result / verified-facts /
                # user message) — the residual confabulation leak. Nudge it to
                # verify via a tool before stating them, ONCE-ish. Only fires on a
                # real unverified file claim, so normal answers never see it.
                _answer_ground = content or last_text
                if _answer_ground and grounding_nudge_fires < _GROUNDING_NUDGE_MAX:
                    _ungrounded = _ungrounded_files(_answer_ground, messages, established_facts)
                    if _ungrounded:
                        grounding_nudge_fires += 1
                        messages.append({"role": "assistant", "content": _answer_ground})
                        messages.append({
                            "role": "user",
                            "content": (
                                "Ты назвал файлы, которые НЕ проверял в этом прогоне "
                                "и которых нет в блоке «Проверенные факты»: "
                                f"{', '.join(_ungrounded[:6])}. Не называй файлы по "
                                "памяти — вызови `glob`/`project_map`/`read_file` и "
                                "убедись, что они реально существуют. Затем ответь по "
                                "факту; выдуманное убери."
                            ),
                        })
                        continue
                final_text = _strip_tool_call_markup(content or last_text)
                # Step C: proactivity (default OFF; opt-in master switch + per-
                # trigger first-fire gate). At most one item, appended as text to
                # Elira's reply. Fail-safe — never breaks the run.
                try:
                    from app.application.persona.proactive import consider_proactive

                    _pro = consider_proactive({
                        "edited": edited_in_run,
                        "verified": ran_verification,
                        "run_id": rid,
                        "project_scope_id": scope_id,
                        "project_root": str(root),
                    })
                    _extras = list(_pro.get("suggestions") or [])
                    for _ask in _pro.get("enable_asks") or []:
                        _extras.append(
                            f"Могу проявлять инициативу: «{_ask['title']}». "
                            "Если хочешь — одобри запрос в панели подтверждений."
                        )
                    if _extras:
                        final_text = final_text + "\n\n" + "\n".join(f"💡 {e}" for e in _extras)
                except Exception:
                    pass
                _facts = _facts_digest(established_facts)
                _recent_digest = _recent_tools_digest(recent_tool_outputs)
                yield {
                    "type": "final_response", "step": step, "text": final_text,
                    "established_facts": _facts,
                    "recent_tool_output": _recent_digest,
                }
                # Step B: drift Elira's mood from this exchange (auto, global,
                # decaying). Fire-and-forget — never breaks the run.
                try:
                    from app.application.persona.mood import nudge_mood

                    nudge_mood(user_message, final_text)
                except Exception:
                    pass
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
                    "established_facts": _facts,
                    "recent_tool_output": _recent_digest,
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
                # Track verification-gate signals from the tool stream itself,
                # before any dispatch branch, so it sees every call uniformly.
                if name in ("write_file", "edit_file"):
                    edited_in_run = True
                elif name in ("run_bash", "run_server"):
                    ran_verification = True
                raw_args = fn.get("arguments") or {}
                parsed_args = ToolRegistry._coerce_args(raw_args)
                # Whitespace-normalized fingerprint: a stray space/newline in a
                # retried argument no longer evades the repeat counter.
                fingerprint = _normalized_fingerprint(name, parsed_args)
                repeated_tool_calls[fingerprint] = repeated_tool_calls.get(fingerprint, 0) + 1
                if (
                    name not in _LOOP_GUARD_EXEMPT_TOOLS
                    and repeated_tool_calls[fingerprint] >= _REPEATED_TOOL_CALL_LIMIT
                ):
                    # Deterministic summary from the journal — NOT a wrap-up call to
                    # the model that just looped (it has nothing new to say and would
                    # only risk confabulating). Facts, not a retelling.
                    final_text = _deterministic_stop_summary(
                        f"повтор одного и того же вызова: {name}",
                        call_log, touched_files, established_facts,
                        exhausted_strategies=progress.exhausted_summary(),
                    )
                    yield {"type": "final_response", "step": step, "text": final_text}
                    yield {
                        "type": "done",
                        "ok": False,
                        "steps": step,
                        "stop_reason": "loop_guard",
                        "error": f"repeated identical tool call: {name}",
                        "established_facts": _facts_digest(established_facts),
                    }
                    return
                # Near-duplicate loop: same tool, slightly-varying args (ping/recall
                # churn). Collapses variants the exact guard above misses, so a spin
                # is cut in ~6 calls instead of ~50. Exempt tools (todo/tool_search/
                # ask_user) are skipped; legit different-file calls have low overlap.
                _nd_tokens = _arg_tokens(parsed_args)
                if name not in _LOOP_GUARD_EXEMPT_TOOLS and _is_near_dup(name, _nd_tokens, near_dup_recent):
                    near_dup_streak += 1
                else:
                    near_dup_streak = 0
                near_dup_recent.append((name, _nd_tokens))
                if len(near_dup_recent) > 8:
                    near_dup_recent = near_dup_recent[-8:]
                if near_dup_streak >= _NEAR_DUP_LIMIT:
                    final_text = _deterministic_stop_summary(
                        f"петля почти одинаковых вызовов {name} (меняются аргументы, прогресса нет)",
                        call_log, touched_files, established_facts,
                        exhausted_strategies=progress.exhausted_summary(),
                    )
                    yield {"type": "final_response", "step": step, "text": final_text}
                    yield {
                        "type": "done",
                        "ok": False,
                        "steps": step,
                        "stop_reason": "loop_guard",
                        "error": f"near-duplicate tool loop: {name}",
                        # Carry the verified facts forward even on an interrupted run,
                        # so the NEXT turn keeps its grounding instead of starting blind
                        # (the wrap-up summary alone used to be all that survived).
                        "established_facts": _facts_digest(established_facts),
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
                    # permission_mode flows in so bypass lifts the activation gate
                    # (not just the approval gate) — bypass = no friction on both.
                    _ts = _tool_search(
                        run_id=rid,
                        query=str(parsed_args.get("query", "")),
                        permission_mode=permission_mode,
                    )
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
                if name == "ask_user":
                    # ask_user is a special inline tool — the "result" comes from a
                    # human, not the executor. It OWNS its own termination: it is
                    # exempt from the generic loop-guard above, so this branch must
                    # bound the questions itself. Keepalive events keep the SSE
                    # stream alive so the client watchdog doesn't cut it while it
                    # waits for the human.
                    ask_user_count += 1
                    if ask_user_count > _ASK_USER_MAX + _ASK_USER_OVER_CAP_GRACE:
                        # The model kept asking past its budget (ignoring repeated
                        # "decide for yourself" nudges). Finalize cleanly with a
                        # real answer instead of spinning to max_steps or tripping
                        # a loop_guard error.
                        final_text = _wrap_up_text(
                            chat, model, safe_num_ctx, messages, call_log,
                            "the model kept asking clarifying questions past the per-run limit",
                        )
                        yield {"type": "final_response", "step": step, "text": final_text}
                        yield {
                            "type": "done", "ok": True, "steps": step,
                            "stop_reason": "answer",
                        }
                        return
                    _budget_spent = ask_user_count > _ASK_USER_MAX
                    if no_questions or _budget_spent:
                        # «Не спрашивать» mode, or the per-run question budget is
                        # spent: never pause — tell the model to decide for itself
                        # and continue the SAME run.
                        if no_questions:
                            _ans_text = (
                                "Режим «не задавать вопросы» включён — не спрашивай "
                                "пользователя. Прими наиболее разумное решение по "
                                "умолчанию и продолжай; если что-то допустил — отметь "
                                "это в финальном ответе."
                            )
                            _log = "ask_user(skipped:no_questions)"
                        else:
                            _ans_text = (
                                "Лимит уточняющих вопросов на этот прогон исчерпан — "
                                "действуй по имеющимся данным."
                            )
                            _log = "ask_user(limit)"
                        yield {
                            "type": "tool_call", "step": step, "tool": name,
                            "arguments": parsed_args, "result": _ans_text, "ok": False,
                        }
                        messages.append({"role": "tool", "content": _ans_text, "name": name})
                        tool_round_trips += 1
                        call_log.append(_log)
                        continue
                    _question = str(parsed_args.get("question") or "").strip()
                    _raw_opts = parsed_args.get("options")
                    _options = [str(o) for o in _raw_opts][:8] if isinstance(_raw_opts, list) else []
                    _qid = uuid.uuid4().hex
                    with _QUESTION_LOCK:
                        _QUESTION_ANSWERS[_qid] = None
                    yield {
                        "type": "question_pending", "step": step,
                        "question": _question, "options": _options, "question_id": _qid,
                    }
                    _wait_started = time.monotonic()
                    _last_keepalive = _wait_started
                    _answer: str | None = None
                    _q_decision = "timeout"
                    # No configured wait means "don't pause" (background/legacy) —
                    # use a sane default so ask_user still works there.
                    _q_budget = approval_wait_seconds if approval_wait_seconds > 0 else 300
                    while time.monotonic() - _wait_started < _q_budget:
                        if cancel_event.is_set():
                            _q_decision = "cancelled"
                            break
                        with _QUESTION_LOCK:
                            _stored = _QUESTION_ANSWERS.get(_qid)
                        if _stored is not None:
                            _answer = _stored
                            _q_decision = "answered"
                            break
                        _now = time.monotonic()
                        if _now - _last_keepalive >= _APPROVAL_KEEPALIVE_EVERY:
                            yield {
                                "type": "question_wait", "step": step,
                                "question_id": _qid, "waited_s": int(_now - _wait_started),
                            }
                            _last_keepalive = _now
                        time.sleep(_APPROVAL_POLL_INTERVAL)
                    with _QUESTION_LOCK:
                        _QUESTION_ANSWERS.pop(_qid, None)
                    # Human deliberation must not consume the agent's own budget.
                    deadline += time.monotonic() - _wait_started
                    if _q_decision == "cancelled":
                        yield {
                            "type": "done", "ok": False, "steps": step,
                            "stop_reason": "cancelled", "error": "Cancelled by user",
                        }
                        return
                    if _q_decision == "answered":
                        _ans_text = f"Ответ пользователя: {_answer}"
                    else:
                        _ans_text = (
                            "Пользователь не ответил на вопрос вовремя. Действуй по "
                            "имеющимся данным или заверши, повторив вопрос в финале."
                        )
                    yield {
                        "type": "tool_call", "step": step, "tool": name,
                        "arguments": parsed_args, "result": _ans_text,
                        "ok": _q_decision == "answered",
                    }
                    messages.append({"role": "tool", "content": _ans_text, "name": name})
                    tool_round_trips += 1
                    call_log.append(f"ask_user({(_question[:40] or '?')})")
                    continue
                if name == "ssh_request_host":
                    # The agent CANNOT edit the SSH allowlist itself (that IS the
                    # security boundary). It calls this to ask the user to approve
                    # ONE host; on approval the loop adds it and ssh_run works this
                    # same run. Always pauses for the human — even under bypass /
                    # «не спрашивать» — because opening the allowlist is the user's
                    # call, not the model's.
                    from app.application.tool_providers.ssh_acl import (
                        get_allowed_hosts as _get_hosts,
                        set_allowed_hosts as _set_hosts,
                    )

                    def _ssh_req_return(text: str, ok: bool, log: str):
                        yield {
                            "type": "tool_call", "step": step, "tool": name,
                            "arguments": parsed_args, "result": text, "ok": ok,
                        }
                        messages.append({"role": "tool", "content": text, "name": name})

                    _host = str(parsed_args.get("host") or "").strip()
                    _reason = str(parsed_args.get("reason") or "").strip()
                    if not _host:
                        yield from _ssh_req_return(
                            "ERROR: ssh_request_host требует непустой host (алиас из ~/.ssh/config).",
                            False, "ssh_request_host(empty)")
                        tool_round_trips += 1
                        call_log.append("ssh_request_host(empty)")
                        continue
                    if _host in _get_hosts():
                        yield from _ssh_req_return(
                            f"Хост '{_host}' уже в SSH-интеграции — ssh_run к нему уже работает.",
                            True, "already")
                        tool_round_trips += 1
                        call_log.append(f"ssh_request_host({_host}:already)")
                        continue
                    _q = (
                        f"Elira просит добавить хост «{_host}» в SSH-интеграцию, "
                        "чтобы ходить туда своим инструментом ssh_run."
                        + (f"\nПричина: {_reason}" if _reason else "")
                    )
                    _qid = uuid.uuid4().hex
                    with _QUESTION_LOCK:
                        _QUESTION_ANSWERS[_qid] = None
                    yield {
                        "type": "question_pending", "step": step,
                        "question": _q, "options": ["Одобрить", "Отклонить"],
                        "question_id": _qid,
                    }
                    _wait_started = time.monotonic()
                    _last_keepalive = _wait_started
                    _answer = None
                    _q_decision = "timeout"
                    _q_budget = approval_wait_seconds if approval_wait_seconds > 0 else 300
                    while time.monotonic() - _wait_started < _q_budget:
                        if cancel_event.is_set():
                            _q_decision = "cancelled"
                            break
                        with _QUESTION_LOCK:
                            _stored = _QUESTION_ANSWERS.get(_qid)
                        if _stored is not None:
                            _answer = _stored
                            _q_decision = "answered"
                            break
                        _now = time.monotonic()
                        if _now - _last_keepalive >= _APPROVAL_KEEPALIVE_EVERY:
                            yield {
                                "type": "question_wait", "step": step,
                                "question_id": _qid, "waited_s": int(_now - _wait_started),
                            }
                            _last_keepalive = _now
                        time.sleep(_APPROVAL_POLL_INTERVAL)
                    with _QUESTION_LOCK:
                        _QUESTION_ANSWERS.pop(_qid, None)
                    deadline += time.monotonic() - _wait_started
                    if _q_decision == "cancelled":
                        yield {
                            "type": "done", "ok": False, "steps": step,
                            "stop_reason": "cancelled", "error": "Cancelled by user",
                        }
                        return
                    _approved = (
                        _q_decision == "answered"
                        and str(_answer or "").strip().lower() in _SSH_APPROVE_WORDS
                    )
                    if _approved:
                        try:
                            _new_hosts = _set_hosts(list(_get_hosts()) + [_host])
                            _added = _host in _new_hosts
                        except Exception as _exc:
                            _added = False
                            logger.warning("ssh_request_host: add %s failed: %s", _host, _exc)
                        _res = (
                            f"✅ Пользователь одобрил — хост '{_host}' добавлен в SSH-интеграцию. "
                            f"Теперь вызывай ssh_run(host='{_host}', command=...) — он работает."
                            if _added else
                            f"Пользователь одобрил, но записать '{_host}' в список не удалось. "
                            "Сообщи пользователю добавить его вручную (Settings → SSH)."
                        )
                        _res_ok = _added
                    elif _q_decision == "answered":
                        _res = (
                            f"Пользователь ОТКЛОНИЛ добавление '{_host}'. Хост НЕ в интеграции, "
                            "ssh_run к нему работать не будет. Не пытайся обойти это другими средствами."
                        )
                        _res_ok = False
                    else:
                        _res = (
                            f"Пользователь не ответил вовремя — '{_host}' НЕ добавлен. "
                            "Заверши и попроси пользователя добавить его вручную (Settings → SSH)."
                        )
                        _res_ok = False
                    yield from _ssh_req_return(
                        _res, _res_ok,
                        f"ssh_request_host({_host}:{'approved' if _approved else _q_decision})")
                    tool_round_trips += 1
                    call_log.append(f"ssh_request_host({_host}:{'approved' if _approved else _q_decision})")
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
                _exec_result = None
                for _hb in _exec_with_heartbeat(
                    lambda: _kernel_exec(_request, dispatch_fn=registry.dispatch_raw), step):
                    if "__result__" in _hb:
                        _exec_result = _hb["__result__"]
                    else:
                        yield _hb
                # F1: pause the loop while a human decides, instead of telling
                # the model "waiting approval" and burning steps. The approval
                # is consumed in the SAME run (binding incl. run_id intact).
                _approval_id = str((_exec_result.output or {}).get("approval_id") or "")
                # Permission selector: «Принимать правки»/«Без ограничений» grant
                # the just-created approval and re-execute in the same run instead
                # of pausing for the user (binding incl. run_id stays intact).
                # Critical calls (destructive shell: rm/git reset/drop/kill…) are
                # NEVER auto-approved — the user confirms them even in bypass.
                if (
                    _exec_result.status == "waiting_approval"
                    and _approval_id
                    and _mode_auto_approves(permission_mode, name)
                    and not _is_critical_call(name, parsed_args)
                ):
                    _mark_approval_approved(_approval_id)
                    if _delay_tool_started:
                        yield {
                            "type": "tool_started",
                            "step": step,
                            "tool": name,
                            "arguments": parsed_args,
                        }
                        _delay_tool_started = False
                    for _hb in _exec_with_heartbeat(
                        lambda: _kernel_exec(_request, dispatch_fn=registry.dispatch_raw), step):
                        if "__result__" in _hb:
                            _exec_result = _hb["__result__"]
                        else:
                            yield _hb
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
                        for _hb in _exec_with_heartbeat(
                            lambda: _kernel_exec(_request, dispatch_fn=registry.dispatch_raw), step):
                            if "__result__" in _hb:
                                _exec_result = _hb["__result__"]
                            else:
                                yield _hb
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
                for opt in ("touched_path", "old_content", "new_content", "diff_action", "exit_code"):
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
                _tool_content = _truncate_for_llm(text_result)
                _tool_ok = bool(tool_meta.get("ok", _exec_result.status == "ok"))
                # Grounding fact from this call — computed HERE (before the tool
                # message is appended) so the progress controller can judge whether
                # the call revealed anything NEW.
                _fact = _fact_from_tool(name, _hint, text_result, ok=_tool_ok)
                if tool_meta.get("touched_path"):
                    touched_files.append(str(tool_meta.get("touched_path")))
                # ── Strategy router ──────────────────────────────────────────
                # Did the world move? A "doing" tool that changed nothing burns its
                # strategy_key's attempt budget; exhaustion → redirect to another
                # FAMILY (not a stop); families/budget spent → honest stop. Catches
                # the different-looking-but-going-nowhere spiral (raw-ssh escaping)
                # that the repetition guards miss.
                verdict = progress.evaluate(
                    name=name, args=parsed_args, tool_meta=tool_meta, fact=_fact,
                )
                # Repetition nudges (exact / near-dup) — orthogonal to the router.
                _rc = repeated_tool_calls.get(fingerprint, 0)
                if name not in _LOOP_GUARD_EXEMPT_TOOLS and _REPEATED_TOOL_CALL_NUDGE_AT <= _rc < _REPEATED_TOOL_CALL_LIMIT:
                    _left = _REPEATED_TOOL_CALL_LIMIT - _rc
                    _tool_content += (
                        f"\n\n[loop-guard] Ты вызвал {name} с теми же аргументами уже {_rc} раз — "
                        f"результат не изменится. Смени подход или дай финальный ответ. "
                        f"Ещё {_left} повтор(а/ов) до принудительной остановки."
                    )
                elif name not in _LOOP_GUARD_EXEMPT_TOOLS and _NEAR_DUP_NUDGE_AT <= near_dup_streak < _NEAR_DUP_LIMIT:
                    _left = _NEAR_DUP_LIMIT - near_dup_streak
                    _tool_content += (
                        f"\n\n[loop-guard] Ты повторяешь похожие вызовы {name} с чуть разными "
                        f"аргументами — это не двигает задачу. Смени ПОДХОД: другой инструмент/данные, "
                        f"прочитай реальные файлы или спроси пользователя. Ещё {_left} до остановки."
                    )
                # Strategy redirect (SOFT — not a stop): this method is exhausted,
                # switch families. Injected once per exhaustion; movement re-arms it.
                if verdict.redirect and not verdict.should_stop:
                    _tool_content += f"\n\n[strategy] {verdict.redirect}"
                messages.append({
                    "role": "tool",
                    "content": _tool_content,
                    "name": name,
                })
                if _fact:
                    established_facts.append(_fact)
                _recent = _recent_tool_snippet(name, _hint, text_result)
                if _recent:
                    recent_tool_outputs.append(_recent)
                # Honest stop: the families/budget for this target are spent.
                # Deterministic report from the journal (never a retelling by the
                # stuck model), with the exhausted strategies named.
                if verdict.should_stop:
                    _det = _deterministic_stop_summary(
                        f"нет прогресса — {verdict.stop_detail}",
                        call_log, touched_files, established_facts,
                        exhausted_strategies=progress.exhausted_summary(),
                    )
                    yield {"type": "final_response", "step": step, "text": _det}
                    yield {
                        "type": "done",
                        "ok": False,
                        "steps": step,
                        "stop_reason": "no_progress",
                        "error": f"no verified progress: {verdict.stop_detail}",
                        "established_facts": _facts_digest(established_facts),
                        "progress_events": progress.progress_events,
                        "no_progress_attempts": progress.no_progress_total,
                        "exhausted_strategies": progress.exhausted_summary(),
                    }
                    return

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
    profile_name: str = "Инженерный",
    permission_mode: str = "ask",
    thinking: bool = False,
    no_questions: bool = False,
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
        "profile_name": profile_name,
        "permission_mode": permission_mode,
        "thinking": bool(thinking),
        "no_questions": bool(no_questions),
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
            profile_name=profile_name,
            permission_mode=permission_mode,
            thinking=thinking,
            no_questions=no_questions,
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
            if event.get("type") == "final_response":
                # Layer C: every preceding tool_call has already updated the
                # journal's changed_files (append_event runs before this yield),
                # so the list is authoritative at this point. Correct a false
                # "nothing changed" claim before it reaches the user or the
                # journalled last_response.
                correction = _layer_c_correction(
                    str(event.get("text") or ""),
                    list(journal.state.get("changed_files") or []),
                )
                if correction:
                    event["text"] = str(event.get("text") or "") + correction
                    event["consistency_corrected"] = True
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
    profile_name: str = "Инженерный",
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
        profile_name=profile_name,
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
