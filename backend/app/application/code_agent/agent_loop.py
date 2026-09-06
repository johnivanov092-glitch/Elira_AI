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
    ToolRegistry,
    build_runtime_tool_registry,
)
from app.application.tool_providers.mcp_provider import (
    creative_workflow_prompt,
)
from app.application.code_agent.capabilities import (
    ALL_BUILTIN_TOOLS,
    builtin_tools_for_groups,
    normalize_capability_groups,
    route_request_capabilities,
    is_local_tabular_catalog_probe,
    requires_bom_validation,
    should_require_local_catalog_search,
    should_require_web_catalog_fallback,
    should_escalate_web_after_failure,
    should_escalate_web_from_answer,
)
from app.application.code_agent.answer_media import merge_answer_media
from app.application.code_agent.document_validation import infer_expected_page_count
from app.application.code_agent.planning import (
    PlanArtifact,
    build_planning_messages,
    parse_plan_from_text,
    plan_artifact_from_dict,
    plan_context_block,
    planner_limits,
)
from app.application.code_agent.taskspec import (
    CriteriaTracker,
    derive_task_spec,
    is_continuation_message,
    taskspec_context,
    taskspec_report,
)
from app.application.code_agent.run_evidence import (
    EvidenceKind,
    RunEvidence,
)
from app.application.projects.scope import project_scope_id
from app.application.agent_kernel.executor import (
    ToolExecutionRequest,
    ToolExecutionResult,
    execute_tool as _kernel_exec,
    permission_mode_auto_approves,
    tool_args_sha256,
    workflow_approval_matches,
)
from app.application.monitoring.inference import extract_llm_usage, record_inference_telemetry
from app.infrastructure.llm.openai_compatible import (
    LLMStreamCancelHandle,
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
    project_corpus_status,
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
from app.application.code_agent.tools._files import recover_read_path_from_glob
# System-prompt construction extracted to .prompts; re-exported so the loop and
# tests keep importing these from agent_loop unchanged.
from app.application.code_agent.prompts import (  # noqa: F401
    BASE_SYSTEM_PROMPT,
    _CODE_AGENT_BASE_TOOLS,
    _build_base_system_prompt,
    _build_system_prompt,
    _build_turn_context,
    _build_project_context,
    _shell_guidance,
    _NO_PROJECT_BLOCK,
    _PROJECT_CONNECTED_BLOCK,
    _is_scratch_workspace,
    compute_placement_request,
    explicit_compute_target,
)
from app.application.persona.service import mode_temperature
from app.core.persona_defaults import DEFAULT_PROFILE
from app.application.code_agent.task_guidance import task_guidance_blocks
from app.application.code_agent.task_skills import (
    CATALOG_ID, CONTEXT_ID, SkillContext, catalog_context, insert_skill_context,
)
from app.core.redaction import redact_secrets
# History coercion + rolling summarization extracted to .history; it imports
# nothing from agent_loop (a leaf), so re-exporting here keeps existing importers
# (code_agent_routes, tests) and the loop's `summarize_fn=summarize_history`
# resolving with no import cycle. DEFAULT_MODEL lives there because
# summarize_history binds it as a default-arg value.
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
# Project-prompt CRUD extracted to .project_prompt; a leaf. Re-exported (with
# PROJECT_PROMPT_FILENAME) so code_agent_routes and tests keep importing these
# from agent_loop unchanged.
from app.application.code_agent.project_prompt import (  # noqa: F401
    PROJECT_PROMPT_FILENAME,
    get_project_prompt,
    init_project_prompt,
    set_project_prompt,
)

logger = logging.getLogger(__name__)

# Per-request DRY sampler params (llama.cpp accepts them in the request body —
# verified against the live server). DRY penalises repeated token sequences at
# sampling time, so the model can't lock into a degenerate "same paragraph
# forever" loop. Originally think-only; extended to ALL runs after a live
# non-think run produced a paragraph repeated ×20 in the ANSWER channel (the
# content path had no anti-repeat protection at all). Safe for codegen: DRY's
# default sequence breakers ("\n" etc.) reset matching at line boundaries, so
# legitimate repeated code structure isn't penalised the way run-on prose is.
# Bound the lookback; it can still include recent HISTORY on short replies.
# Allow short repeated facts/names: length=2 made Qwen mutate Elira after three
# identity questions. In a fixed-history/seed live A/B, length=12 preserved the
# exact name with thinking both off and low, while keeping DRY for long prose.
_DRY_WINDOW_TOKENS = 1024
_ANTI_REPEAT_SAMPLING = {
    "dry_multiplier": 0.8,
    "dry_base": 1.75,
    "dry_allowed_length": 12,
    "dry_penalty_last_n": _DRY_WINDOW_TOKENS,
    # Reset DRY matching at these separators so LEGITIMATELY-repeated IPs / MACs /
    # numbers / versions / paths (192.168.88.1, 2C-C8-1B, /24, v1.0) are not seen as
    # a penalizable repeat. Without "." DRY penalised the repeated octets of an IP,
    # and the model MUTATED them to dodge the penalty (live: 192.168→192.169→192.166
    # →192.170… during a network scan, plus a walk through 8.8.8.8/9.9.9.9/6.6.6.6).
    # Keeps the llama.cpp defaults (\n : " *) so repeated PROSE — the ×20-paragraph
    # runaway — is still caught (a repeated sentence resets only at its own period).
    # File-path delimiters are breakers too: without "_" / "\\", copying a long
    # path verbatim from glob into read_file is treated as repetition and Qwen
    # abbreviates or mutates the tool argument.
    # Digits are breakers too: a repeated number/model-code (RTX 5090 in every table
    # row, a price repeated down a column) was DRY-penalised and the model dropped a
    # digit to dodge it (live: "5090"→"509"/"090"). A digit resets the match, so
    # numbers survive verbatim; word-based degeneration (no digits) is still caught.
    "dry_sequence_breakers": [
        "\n", ":", "\"", "*", ".", "-", "/", "\\", "_", ",", ";", "=",
        "0", "1", "2", "3", "4", "5", "6", "7", "8", "9",
    ],
}


_REASONING_EFFORTS = frozenset({"none", "low", "medium", "xhigh"})


def _normalize_reasoning_effort(value: Any, *, thinking: bool = False) -> str:
    """Normalize the public effort selector while preserving the legacy bool."""
    effort = str(value or "").strip().lower()
    if effort in _REASONING_EFFORTS:
        return effort
    return "xhigh" if thinking else "none"


def _thinking_template_kwargs(effort: str | bool) -> dict[str, bool | str]:
    """Build Qwen's reasoning contract; ``none`` fully disables thinking."""
    normalized = _normalize_reasoning_effort(
        None if isinstance(effort, bool) else effort,
        thinking=bool(effort) if isinstance(effort, bool) else False,
    )
    if normalized != "none":
        return {
            "enable_thinking": True,
            "reasoning_effort": normalized,
        }
    return {
        "enable_thinking": False,
        "reasoning_effort": "none",
    }


# Completion is model-owned: evidence is recorded for observability, but the
# runtime never forces extra turns, rewrites the answer, or auto-runs verification.
_LLM_HEARTBEAT_EVERY = 10.0
_LLM_CANCEL_POLL_SECONDS = 0.1
def _effective_temperature(profile_name: str, role: str | None) -> float:
    """Compatibility arguments never switch the single personality's sampling."""
    return float(mode_temperature(profile_name))


def _chat_events(
    *,
    chat_fn: Callable[..., dict[str, Any]],
    chat_stream_fn: Callable[..., Any] | None,
    kwargs: dict[str, Any],
    cancel_event: "threading.Event | None" = None,
    cancel_handle: LLMStreamCancelHandle | None = None,
) -> Iterator[dict[str, Any]]:
    """Run a blocking provider call without leaving the SSE stream silent.

    When ``cancel_event`` is set mid-stream the worker stops pulling tokens
    and closes the underlying generator (which closes the upstream HTTP
    response), so pressing Stop actually frees the server instead of letting
    it generate the full answer into a queue nobody reads.
    """
    events: queue.Queue[tuple[str, Any]] = queue.Queue()
    upstream_handle = cancel_handle or LLMStreamCancelHandle()
    worker_kwargs = dict(kwargs)
    worker_options = dict(worker_kwargs.get("options") or {})
    worker_options["_stream_cancel_handle"] = upstream_handle
    worker_kwargs["options"] = worker_options

    def worker() -> None:
        stream = None
        try:
            if chat_stream_fn is None:
                events.put(("response", chat_fn(**worker_kwargs)))
                return
            final_response: dict[str, Any] | None = None
            collected: list[str] = []
            stream = chat_stream_fn(**worker_kwargs)
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
    next_heartbeat = time.monotonic() + _LLM_HEARTBEAT_EVERY
    try:
        while not done:
            try:
                timeout = max(
                    0.001,
                    min(_LLM_CANCEL_POLL_SECONDS, next_heartbeat - time.monotonic()),
                )
                kind, value = events.get(timeout=timeout)
            except queue.Empty:
                if cancel_event is not None and cancel_event.is_set():
                    return
                if time.monotonic() >= next_heartbeat:
                    next_heartbeat = time.monotonic() + _LLM_HEARTBEAT_EVERY
                    yield {"type": "heartbeat"}
                continue
            if kind == "done":
                done = True
            elif kind == "error":
                raise value
            else:
                yield {"type": kind, "value": value}
    finally:
        # The run owns the shared handle across planning, compaction and normal
        # generation. Only cancellation makes it permanently closed; normal
        # completion merely releases the provider response for the next call.
        if cancel_event is not None and cancel_event.is_set():
            upstream_handle.close()


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
_UPSTREAM_HANDLE_REGISTRY: dict[int, LLMStreamCancelHandle] = {}
_REGISTRY_LOCK = threading.Lock()

def _server_url_alive(url: str) -> bool:
    """R2 liveness gate: the remembered dev-server URL is backed by a tracked
    process that is alive AND listening. Module-level so tests patch it."""
    try:
        from app.application.code_agent.tools._run import url_is_live_server
        return url_is_live_server(url)
    except Exception:
        return False


def _run_owned_servers(run_id: str) -> list[dict]:
    """R2: alive servers this run started (module-level so tests patch it)."""
    try:
        from app.application.code_agent.tools._run import run_owned_servers
        return run_owned_servers(run_id)
    except Exception:
        return []


def _record_criterion_verdict(criteria, name: str, args: dict, tool_meta: dict,
                              text_result: str, tool_ok: bool, auto: bool = False) -> bool:
    """Feed one executed tool call into the observational criterion tracker.

    A verifier tool records structured evidence; ``run_bash`` records real
    stdout/stderr and exit code. The tracker never forces another model turn.
    ``auto`` remains only for compatibility with persisted historical reports.
    """
    if not criteria.items:
        return False
    if tool_meta.get("verifier"):
        return criteria.record(tool_name=name, args=args, ok=tool_ok,
                               evidence=str(tool_meta.get("evidence") or ""),
                               meta=tool_meta, auto=auto)
    if name == "run_bash":
        return criteria.record(tool_name=name, args=args, ok=tool_ok,
                               evidence=text_result, meta=tool_meta, auto=auto)
    return False


def _exec_with_heartbeat(thunk, step, cancel_event: threading.Event | None = None):
    """Run a blocking tool call (thunk) in a daemon thread, yielding `heartbeat`
    events every _LLM_HEARTBEAT_EVERY seconds while it runs. This keeps progress
    observable during a long network scan, build or test without imposing a
    product deadline. The FINAL yielded item is
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
        if cancel_event is not None and cancel_event.is_set():
            yield {
                "__result__": ToolExecutionResult(
                    status="error",
                    output={
                        "ok": False,
                        "text": "Выполнение остановлено пользователем.",
                        "error": "cancelled_by_user",
                    },
                    error="cancelled_by_user",
                )
            }
            return
        _now = time.monotonic()
        if _now - _last >= _LLM_HEARTBEAT_EVERY:
            yield {"type": "heartbeat", "step": step}
            _last = _now
    if "e" in box:
        raise box["e"]
    yield {"__result__": box["r"]}

# Live direct-stream Workflow requests. Durable request metadata lives in the
# Workflow store; this in-memory rendezvous only wakes the currently running
# code-agent generator after the UI resolves that durable request.
_WORKFLOW_RESPONSES: dict[str, dict[str, Any] | None] = {}
_WORKFLOW_RESPONSE_LOCK = threading.Lock()


def submit_workflow_response(
    response_id: str,
    action: str,
    values: dict[str, Any] | None = None,
) -> bool:
    """Deliver a Workflow UI resolution to a live direct code-agent request."""
    with _WORKFLOW_RESPONSE_LOCK:
        if response_id not in _WORKFLOW_RESPONSES:
            return False
        _WORKFLOW_RESPONSES[response_id] = {
            "action": str(action or "accept"),
            "values": dict(values) if isinstance(values, dict) else {},
        }
    return True


def request_cancel(run_id: str) -> bool:
    """Flip the cancel event for `run_id`. Returns True if the run was
    known, False otherwise. Raises when owned live resources cannot be stopped,
    so callers never acknowledge an incomplete cancellation.

    Beyond setting the flag (read between steps), this also KILLS any live
    shell process the run launched. A blocking tool runs in a daemon worker
    thread that never reads the event until it returns, so killing the OS
    process is what makes Stop abort a hung command immediately instead of
    waiting out the shell timeout.
    """
    # Cancel inference first. Shell/server cleanup can involve OS process-tree
    # work and must not delay closing a hot llama.cpp connection.
    with _REGISTRY_LOCK:
        ev = _CANCEL_REGISTRY.get(run_id)
        upstream_handle = (
            _UPSTREAM_HANDLE_REGISTRY.get(id(ev)) if ev is not None else None
        )
    if ev is not None:
        ev.set()
    cleanup_errors: list[Exception] = []
    if upstream_handle is not None:
        # Do not acknowledge /cancel until the provider's HTTP response is
        # closed. The UI may safely abort its SSE reader after this returns.
        try:
            upstream_handle.close()
        except Exception as exc:
            cleanup_errors.append(exc)

    # Attempt every cleanup stage even if an earlier one failed. The caller is
    # told about any surviving transport/process only after all owners had a
    # chance to stop.
    try:
        from app.application.code_agent.tools import kill_run_processes

        kill_run_processes(run_id)
    except Exception as exc:
        cleanup_errors.append(exc)
    try:
        from app.application.code_agent.tools import cancel_run_callbacks

        cancel_run_callbacks(run_id)
    except Exception as exc:
        cleanup_errors.append(exc)
    try:
        from app.application.code_agent.tools._run import (
            run_owned_servers,
            stop_run_servers,
        )

        stopped_servers = stop_run_servers(run_id)
        failed_remote_cleanup = [
            item
            for item in stopped_servers
            if item.get("remote_cleanup_status")
            not in {None, "stopped", "already_stopped"}
        ]
        remaining_servers = run_owned_servers(run_id)
        if failed_remote_cleanup or remaining_servers:
            raise RuntimeError(
                "background process cleanup incomplete"
                f" (remote_failures={len(failed_remote_cleanup)},"
                f" remaining={len(remaining_servers)})"
            )
    except Exception as exc:
        cleanup_errors.append(exc)
    if cleanup_errors:
        raise RuntimeError(
            f"live cleanup failed for run {run_id}: {cleanup_errors[0]}"
        ) from cleanup_errors[0]
    return ev is not None


def _register_run(run_id: str) -> threading.Event:
    try:
        from app.application.code_agent.tools._shell import clear_run_stop_marker

        clear_run_stop_marker(run_id)
    except Exception:
        logger.warning("failed to clear stale Stop marker for run %s", run_id, exc_info=True)
    ev = threading.Event()
    with _REGISTRY_LOCK:
        _CANCEL_REGISTRY[run_id] = ev
        _UPSTREAM_HANDLE_REGISTRY[id(ev)] = LLMStreamCancelHandle()
    return ev


def _cancel_handle_for(cancel_event: threading.Event) -> LLMStreamCancelHandle:
    with _REGISTRY_LOCK:
        handle = _UPSTREAM_HANDLE_REGISTRY.get(id(cancel_event))
    if handle is None:
        raise RuntimeError("run cancellation handle is not registered")
    return handle


def _unregister_run(run_id: str) -> None:
    with _REGISTRY_LOCK:
        ev = _CANCEL_REGISTRY.pop(run_id, None)
        upstream_handle = (
            _UPSTREAM_HANDLE_REGISTRY.pop(id(ev), None) if ev is not None else None
        )
    if upstream_handle is not None:
        upstream_handle.close()


# Text/format, Workflow-request, context-window, RAG and telemetry helpers
# were extracted to .loop_helpers (a leaf — imports nothing from agent_loop), and
# re-exported here so existing importers (tests, file_watcher) and the core loop
# below keep resolving these names from agent_loop unchanged. Because the core
# loop calls every one of them through this module's namespace, tests that
# `patch("...agent_loop.<name>")` still take effect.
from app.application.code_agent.loop_helpers import (  # noqa: F401
    ContextBudgetError,
    TOOL_RESULT_LLM_LIMIT,
    _WORKFLOW_REQUEST_KEEPALIVE_EVERY,
    _WORKFLOW_REQUEST_POLL_INTERVAL,
    _ASK_USER_SCHEMA,
    _WORKFLOW_REQUEST_SCHEMA,
    FACTS_PREFIX,
    _fact_from_tool,
    _facts_digest,
    _recent_tool_snippet,
    _recent_tools_digest,
    RECENT_TOOLS_PREFIX,
    build_task_state_block,
    format_checklist_state,
    session_cancel_requested,
    tool_state_changed,
    upsert_task_state_message,
    _flatten_for_summary,
    _messages_char_count,
    _strip_think_blocks,
    _prepare_messages_for_llm,
    _record_code_route_metric,
    _schema_tool_name,
    _short_arg_hint,
    _truncate,
    _truncate_for_llm,
    _try_remember_turn,
)


def _load_planning_state(run_id: str) -> "tuple[PlanArtifact | None, bool]":
    """(reusable plan, planner_already_attempted) from the RunJournal — for
    Resume / auto-continuation. A valid stored plan is REUSED; if the planner
    already ran once but produced no plan (planning_fallback), the attempt flag
    is True so the caller skips a second planner call (bounded, no re-plan
    loop). Any read failure → (None, False), a safe fresh start."""
    try:
        from app.application.code_agent.run_journal import RunJournal

        state = RunJournal.load(run_id).state
    except Exception:
        return None, False
    stored = state.get("plan")
    plan = plan_artifact_from_dict(stored) if isinstance(stored, dict) else None
    return plan, bool(state.get("planning_attempted"))


def _load_runtime_activation_state(
    run_id: str,
) -> tuple[set[str], set[str], dict[str, str], bool, bool, set[str]]:
    """Restore run-scoped integration and built-in schema visibility."""
    try:
        from app.application.code_agent.run_journal import RunJournal

        stored = RunJournal.load(run_id).state.get("runtime_activation") or {}
    except Exception:
        stored = {}
    if not isinstance(stored, dict):
        stored = {}
    return (
        {str(value) for value in stored.get("mcp_server_ids") or [] if str(value)},
        {str(value) for value in stored.get("lsp_server_ids") or [] if str(value)},
        {
            str(key): str(value)
            for key, value in (stored.get("mcp_schema_queries") or {}).items()
            if str(key) and str(value)
        }
        if isinstance(stored.get("mcp_schema_queries"), dict)
        else {},
        bool(stored.get("ssh")),
        bool(stored.get("itops")),
        set(normalize_capability_groups(stored.get("capability_groups"))),
    )


def _bounded_planning_recon(registry, *, char_cap: int) -> str:
    """Deterministic, READ-ONLY recon handed to the planner: a bounded project
    map. Uses the SAME registry (no second executor) and only a read-only tool,
    so planning can never mutate anything. Best-effort — an empty string on any
    failure keeps planning bounded and non-blocking."""
    try:
        result = registry.dispatch_raw("project_map", {"max_depth": 2})
        text = str((result or {}).get("text") or "")
        return text[:max(1, int(char_cap))]
    except Exception:
        return ""


def _stream_code_agent_core(
    *,
    user_message: str,
    memory_query: str | None = None,
    task_instructions: str = "",
    project_root: Path | str,
    working_dir: Path | str | None = None,
    model: str = "auto",
    agent_id: str = "code-agent",
    conversation_history: list[dict[str, Any]] | None = None,
    run_id: str | None = None,
    num_ctx: int | None = None,
    base_tools: tuple[str, ...] | list[str] | None = None,
    auto_remember: bool = True,
    chat_fn: Callable[..., dict[str, Any]] | None = None,
    chat_stream_fn: Callable[..., Any] | None = None,
    compaction_audit_sink: Callable[[dict[str, Any]], None] | None = None,
    profile_name: str = "Инженерный",
    permission_mode: str = "ask",
    thinking: bool = False,
    reasoning_effort: str | None = None,
    resume: bool = False,
    pause_for_workflow_request: bool = False,
    workflow_approval: dict[str, Any] | None = None,
    resource_refs: list[dict[str, Any]] | None = None,
    initial_sources: list[dict[str, Any]] | None = None,
) -> Iterator[dict[str, Any]]:
    """Stream the agent loop as events.

    Reasoning and draft answer deltas are emitted live. Only final_response
    accepts an answer; interrupted/retried drafts are not final conversation.

    Yields dicts with a `type` discriminator:
      - {"type": "run_started", "run_id": ...}
      - {"type": "step_started", "step": N}
      - {"type": "tool_started", "step": N, "tool": str, "arguments": dict}
      - {"type": "tool_call", "step": N, "tool": str, "arguments": dict,
         "result": str, "touched_path"?: str,
         "old_content"?: str, "new_content"?: str, "diff_action"?: str}
      - {"type": "final_response", "step": N, "text": str,
         "answer_status": "complete" | "degraded" | "needs_input"}
      - {"type": "done", "ok": bool, "steps": int, "stop_reason": str,
         "error": str | None}
    """
    profile_name = DEFAULT_PROFILE
    selected_reasoning_effort = _normalize_reasoning_effort(
        reasoning_effort,
        thinking=thinking,
    )
    thinking = selected_reasoning_effort != "none"
    root = Path(project_root).resolve()
    scope_id = project_scope_id(root)
    rid = run_id or uuid.uuid4().hex
    effective_agent_id = str(agent_id or "code-agent").strip() or "code-agent"
    pending_workflow_approval = (
        dict(workflow_approval) if isinstance(workflow_approval, dict) else {}
    )
    cancel_event = _register_run(rid)
    upstream_cancel_handle = _cancel_handle_for(cancel_event)
    # Delivery: a session-level Stop may land while NO slice is registered
    # (between slices / during continuation build), where request_cancel finds
    # no live run. Make it visible to THIS slice the moment it registers — the
    # loop's own cancel checks then terminate before any model or tool call.
    if session_cancel_requested(rid):
        cancel_event.set()
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

        model, offline_ctx, _route_decision = _resolve_code_route(
            model,
            num_ctx,
            agent_id=effective_agent_id,
        )
        from app.application.context.profile import (
            ContextResolutionError,
            resolve_context_window,
        )

        try:
            context_profile = resolve_context_window(
                offline_ctx or None,
                model=model,
                thinking=thinking,
                live=chat_fn is None,
                fresh=True,
            )
        except ContextResolutionError as exc:
            yield {"type": "run_started", "run_id": rid}
            yield {
                "type": "done",
                "ok": False,
                "steps": 0,
                "stop_reason": "error",
                "error": str(exc),
            }
            return
        safe_num_ctx = int(context_profile["ctx_size"])
        _record_code_route_metric(rid, _route_decision, safe_num_ctx, agent_id=effective_agent_id)
        raw_user_message = user_message if memory_query is None else memory_query
        # Hidden integrations are activated per run through runtime_control.
        # New requests must not serialize every globally running MCP schema.
        (
            active_mcp_server_ids,
            active_lsp_server_ids,
            active_mcp_schema_queries,
            ssh_tools_active,
            itops_tools_active,
            active_capability_groups,
        ) = _load_runtime_activation_state(rid)
        # Compatibility domain labels supply task/evidence requirements only.
        # The public entrypoints preserve raw user text in memory_query.
        # Library/attachment contents are evidence, not download or SSH intent.
        request_route = route_request_capabilities(
            user_message if memory_query is None else memory_query,
            domain_policy=profile_name,
            conversation_history=conversation_history,
        )
        # Domain/evidence hints never preload schemas or change the persona.

        requested_builtin_tools = {
            str(name).strip()
            for name in (base_tools or ())
            if str(name).strip() in ALL_BUILTIN_TOOLS
        }

        def rebuild_registry() -> ToolRegistry:
            builtin_names = set(builtin_tools_for_groups(active_capability_groups))
            # Search mode and attachment/resource routing arrive through
            # base_tools. Preserve those targeted schema additions instead of
            # merely journalling and then discarding them.
            builtin_names.update(requested_builtin_tools)
            return build_runtime_tool_registry(
                root,
                include_builtin=True,
                builtin_tool_names=builtin_names,
                mcp_server_ids=active_mcp_server_ids,
                mcp_schema_queries=active_mcp_schema_queries,
                lsp_server_ids=active_lsp_server_ids,
                include_ssh=ssh_tools_active,
                include_itops=itops_tools_active,
            )

        def runtime_activation_snapshot() -> dict[str, Any]:
            return {
                "mcp_server_ids": sorted(active_mcp_server_ids),
                "lsp_server_ids": sorted(active_lsp_server_ids),
                "mcp_schema_queries": dict(sorted(active_mcp_schema_queries.items())),
                "ssh": ssh_tools_active,
                "itops": itops_tools_active,
                "capability_groups": sorted(active_capability_groups),
            }

        # Aggregate the default work tools into one registry. The agent loop only
        # talks to the registry from here on.
        #   - BuiltinToolProvider exposes discovery on every request.
        #   - SSH/IT Ops schemas appear after their runtime_control discovery
        #     request in this run.
        #   - MCP/LSP schemas appear only for servers explicitly started by this
        #     run; other globally running integrations remain hidden.
        # The HTTP app seeds these at startup, but the runtime is also called
        # directly by tests and CLI integrations. Reuse the same idempotent
        # seeder so ToolExecutor never sees an unregistered built-in spec.
        from app.application.tool_registry.runtime import seed_builtin_tools

        seed_builtin_tools()
        registry = rebuild_registry()
        all_schemas = registry.collect_schemas()
        # Ordinary work tools are visible from the first step. Specialist
        # built-ins and integrations appear after explicit model calls.
        initial_tools = tuple(dict.fromkeys(
            name for schema in all_schemas
            if (name := _schema_tool_name(schema))
        ))
        chat = chat_fn or _local_chat
        stream_chat = chat_stream_fn
        if chat_fn is None and stream_chat is None:
            stream_chat = _local_chat_stream

        system_prompt = _build_system_prompt(root, model_name=model, profile_name=profile_name)
        request_context = _build_turn_context(
            root, working_dir=working_dir, active_tools=initial_tools,
            model_name=model, profile_name=profile_name, task_text=raw_user_message,
            memory_query=memory_query or "", resource_refs=resource_refs,
        )
        _creative_context = [str(user_message or "")]
        for _turn in list(conversation_history or [])[-8:]:
            if isinstance(_turn, dict) and isinstance(_turn.get("content"), str):
                _creative_context.append(str(_turn["content"])[:4000])
        if re.search(
            r"(?:\bblender\b|\bunity\b|блендер|юнити|сцен\w*|scene\w*|"
            r"префаб\w*|prefab\w*|террейн\w*|terrain\w*|\b3d\b)",
            "\n".join(_creative_context),
            re.IGNORECASE,
        ):
            _creative_prompt = creative_workflow_prompt({
                _schema_tool_name(schema) for schema in all_schemas
                if _schema_tool_name(schema)
            })
            if _creative_prompt:
                request_context += "\n\n" + _creative_prompt
        messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
        messages.extend(_coerce_history(conversation_history))
        # Keep the current request last in this mutable turn. Finishing on
        # persona/context instructions encourages the model to recite them.
        effective_user_message = ""
        if request_context:
            effective_user_message = "[Контекст текущего запроса]\n" + request_context
        # TaskSpec (Phase 6): on a STRUCTURED task, derive goal + success criteria +
        # verifiers and keep them in focus. None for simple/conversational tasks —
        # so nothing is injected there (zero tokens, canaries untouched).
        task_spec = derive_task_spec(user_message, project_root=root)
        task_spec_source = "current_message" if task_spec is not None else "none"
        document_page_count_contract = infer_expected_page_count(user_message)
        if task_spec is None and is_continuation_message(user_message):
            # FIX-8: ONLY on an explicit continuation ("делай"/"продолжай"/"да") —
            # restore the most recent STRUCTURED TaskSpec from the user's history so
            # the verifier-gate/criteria don't silently switch off. A NEW question
            # after a structured task must NOT drag the old task's criteria back.
            for _m in reversed(conversation_history or []):
                if isinstance(_m, dict) and _m.get("role") == "user":
                    _hist_spec = derive_task_spec(str(_m.get("content") or ""), project_root=root)
                    if _hist_spec is not None:
                        task_spec = _hist_spec
                        task_spec_source = "conversation_history"
                        break
        if document_page_count_contract is None and is_continuation_message(user_message):
            for _m in reversed(conversation_history or []):
                if isinstance(_m, dict) and _m.get("role") == "user":
                    document_page_count_contract = infer_expected_page_count(
                        str(_m.get("content") or "")
                    )
                    if document_page_count_contract is not None:
                        break
        if task_spec is not None:
            effective_user_message = f"{taskspec_context(task_spec)}\n\n{effective_user_message}"
        # Bind the completion contract to THIS run's spec/source so every terminal
        # done event carries uniform task-state (FIX-1/8).
        def _completion_fields(crit, *, terminated_incomplete: bool = False) -> dict:
            cs = crit.completion_status()
            return {
                "completion_status": cs,
                "criteria": crit.report(),
                "criteria_confirmed": cs == "confirmed",  # compat mirror only (FIX-6)
                "partial": terminated_incomplete or cs in ("partial", "unverified", "failed"),
                "task_spec": taskspec_report(task_spec) if task_spec else None,
                "task_spec_source": task_spec_source,
            }
        # Capture tone once per run at the tail. Changing it must not rewrite
        # the system/schema/history prefix cached by the inference server.
        try:
            from app.application.persona.mood import mood_overlay_line

            effective_user_message += "\n\n[Текущий тон Elira]\n" + mood_overlay_line()
        except Exception:
            logger.debug("transient persona tone unavailable", exc_info=True)
        effective_user_message = (
            effective_user_message.strip() + "\n\n[Текущее сообщение пользователя]\n" + user_message
        ).lstrip()
        messages.append({"role": "user", "content": effective_user_message})

        yield {
            "type": "run_started",
            "run_id": rid,
            "profile_name": profile_name,
            "ui_profile_name": "Elira / Auto",
            "domain_policies": list(request_route.domain_policies),
            "evidence_reasons": list(request_route.evidence_reasons),
            "runtime_activation": runtime_activation_snapshot(),
        }
        yield {
            "type": "context_resolved",
            "step": 0,
            "requested_context_mode": context_profile.get("requested_context_mode"),
            "requested_context_cap": context_profile.get("requested_context_cap"),
            "server_context_window": context_profile.get("server_context_window"),
            "effective_context_window": context_profile.get("effective_context_window"),
            "limiting_source": context_profile.get("limiting_source"),
            "context_profile_source": context_profile.get("context_profile_source"),
            "reserved_output_tokens": context_profile.get("reserved_output_tokens"),
            "reserved_system_tokens": context_profile.get("reserved_system_tokens"),
            "safety_margin_tokens": context_profile.get("safety_margin_tokens"),
            "safe_input_budget": context_profile.get("safe_input_budget"),
            "compaction_thresholds": context_profile.get("compaction_thresholds"),
            "thinking": bool(thinking),
            "reasoning_effort": selected_reasoning_effort,
        }

        last_text = ""
        tool_round_trips = 0
        compaction_count = 0
        call_log: list[str] = []
        # Grounding across turns: compact facts the discovery tools revealed this
        # run, handed back next turn as an authoritative context block so the
        # model grounds instead of confabulating (see loop_helpers._fact_from_tool
        # and the FACTS_PREFIX block in the frontend history builder).
        established_facts: list[str] = []
        # Verbatim buffer of recent grounding-tool outputs (last few, in full-ish),
        # carried into the next turn alongside the compact facts digest.
        recent_tool_outputs: list[str] = []
        # One run-local evidence ledger owns mutation, verification, artifact,
        # remote-observation and external-source truth. A mutation advances its
        # project epoch, making older verification receipts stale.
        run_evidence = RunEvidence(sources=initial_sources or [])
        _last_failure: dict[str, str] = {}  # for the deterministic stop summary
        _failure_counts: dict[str, int] = {}
        _download_delivery_correction_sent = False
        _evidence_answer_correction_sent = False
        _local_tabular_catalog_probe_seen = False
        _library_search_seen = False
        _local_catalog_correction_sent = False
        _web_catalog_fallback_correction_sent = False
        _catalog_web_fallback_required = False
        _catalog_web_fetch_seen = False
        _catalog_web_fetch_correction_sent = False
        _bom_snapshot: dict[str, Any] | None = None
        _bom_validation_correction_sent = False
        _last_glob_matches: tuple[str, ...] = ()
        _read_file_failures: dict[str, int] = {}
        pending_redirected_jobs: set[int] = set()

        # TaskSpec per-criterion state (Ph7.4/7.5): DONE is decided by verifiers,
        # not the model's word. Each criterion is unconfirmed → confirmed (a matching
        # verifier passed) / failed (matching verifier red). completion_status is a
        # deterministic function of this — kept SEPARATE from runtime `ok`. We never
        # burn an extra LLM turn to nag; unconfirmed criteria are reported at finalize.
        criteria = CriteriaTracker.from_spec(task_spec)
        _last_server_url = ""
        touched_files: list[str] = []  # every file the run mutated (for the report)
        durable_state: dict[str, Any] = {}
        try:
            from app.application.code_agent.run_journal import RunJournal

            durable_state = RunJournal.load(rid).state
        except Exception:
            durable_state = {}
        mutated_files: list[str] = list(durable_state.get("mutated_files") or [])
        verification_log: list[str] = list(durable_state.get("verifications") or [])
        durable_failures: list[str] = list(durable_state.get("failed_attempts") or [])
        durable_project_epoch = int(durable_state.get("project_epoch") or 0)
        durable_criteria_epoch = int(durable_state.get("criteria_epoch") or 0)
        if resume and durable_criteria_epoch == durable_project_epoch:
            criteria.restore_report(list(durable_state.get("criteria") or []))

        # ── Structured planning preflight ────────────────────────────────────
        # On a structured task the selected reasoning mode is used for a planning
        # call and then remains active for every execution/verification model call.
        # Only the physical context/output window bounds the plan artifact.
        # The plan is durable in the RunJournal (state["plan"]): on Resume /
        # auto-continuation the existing plan is REUSED and the planner never
        # runs a second time. request.thinking (the user setting) is never
        # rewritten; the actually-applied mode is journalled separately.
        plan: PlanArtifact | None = None
        applied_thinking_mode = "raw" if thinking else "off"
        _existing_plan, _planned_before = _load_planning_state(rid)
        if _existing_plan is not None:
            plan = _existing_plan
            applied_thinking_mode = "plan_reused"
        elif _planned_before:
            # Planner already ran once (fallback, no stored plan): do NOT re-plan.
            applied_thinking_mode = "planning_fallback"
        elif thinking and task_spec is not None:
            if cancel_event.is_set():
                yield {
                    "type": "done", "ok": False, "steps": 0,
                    "stop_reason": "cancelled", "error": "Cancelled by user",
                    **_completion_fields(criteria, terminated_incomplete=True),
                }
                return
            yield {"type": "planning_started", "run_id": rid}
            _planner_max_tokens, _planner_project_chars = planner_limits(context_profile)
            _project_ctx = _bounded_planning_recon(
                registry, char_cap=_planner_project_chars,
            )
            _planner_response: dict[str, Any] = {}
            _planner_failed = False
            try:
                _planner_kwargs = {
                    "model": model,
                    "messages": build_planning_messages(
                        taskspec_context=taskspec_context(task_spec),
                        project_context=_project_ctx,
                        project_char_cap=_planner_project_chars,
                    ),
                    # No tools by construction. The per-call output cap is
                    # enforced by the OpenAI-compatible provider.
                    "options": {
                        "num_ctx": safe_num_ctx,
                        "active_context_limit": safe_num_ctx,
                        "max_tokens": _planner_max_tokens,
                        "reasoning_effort": selected_reasoning_effort,
                        "chat_template_kwargs": _thinking_template_kwargs(
                            selected_reasoning_effort
                        ),
                    },
                }
                for _planner_event in _chat_events(
                    chat_fn=chat,
                    chat_stream_fn=stream_chat,
                    kwargs=_planner_kwargs,
                    cancel_event=cancel_event,
                    cancel_handle=upstream_cancel_handle,
                ):
                    if cancel_event.is_set():
                        break
                    if _planner_event["type"] == "heartbeat":
                        yield {"type": "heartbeat", "phase": "planning"}
                    elif _planner_event["type"] == "response":
                        _planner_response = dict(_planner_event["value"] or {})
                    # Reasoning/delta output is intentionally not surfaced or
                    # retained: only the validated PlanArtifact crosses phases.
            except Exception:
                _planner_failed = True
            if not _planner_failed:
                _planner_content = str(
                    ((_planner_response.get("message") or {}).get("content") or "")
                )
                plan = parse_plan_from_text(_planner_content)
            if cancel_event.is_set():
                yield {
                    "type": "done", "ok": False, "steps": 0,
                    "stop_reason": "cancelled", "error": "Cancelled by user",
                    **_completion_fields(criteria, terminated_incomplete=True),
                }
                return
            if plan is None or not plan.is_valid():
                plan = None
                applied_thinking_mode = "planning_fallback"
                yield {
                    "type": "planning_fallback", "run_id": rid,
                    "reason": "planner produced no valid PlanArtifact",
                }
            else:
                applied_thinking_mode = "planning_then_execution"
                yield {"type": "plan_ready", "run_id": rid, "plan": plan.to_dict()}

        # The planning LLM can select optional schema groups before the first
        # execution turn.  This keeps the execution prompt prefix stable instead
        # of loading (for example) browser tools after tens of thousands of tokens
        # and forcing an expensive cold prefill.  It changes visibility only;
        # execution still goes through the canonical registry/executor.
        if plan is not None:
            planned_groups = set(normalize_capability_groups(plan.capability_groups))
            new_groups = planned_groups - active_capability_groups
            if new_groups:
                active_capability_groups.update(new_groups)
                registry = rebuild_registry()
                all_schemas = registry.collect_schemas()
                yield {
                    "type": "runtime_activation_changed",
                    "run_id": rid,
                    "source": "planner",
                    "runtime_activation": runtime_activation_snapshot(),
                }

        # Structural event ONLY when a planning preflight actually engaged. A
        # direct/simple run emits nothing new and retains the one-call path.
        if applied_thinking_mode in (
            "planning_then_execution", "planning_fallback", "plan_reused",
        ):
            yield {
                "type": "phase_changed", "run_id": rid, "phase": "execution",
                "applied_thinking_mode": applied_thinking_mode,
            }
        _brain_phase_tracking = applied_thinking_mode in (
            "planning_then_execution", "plan_reused",
        )
        _verification_phase_emitted = False

        def _enter_verification_phase() -> dict[str, Any] | None:
            nonlocal _verification_phase_emitted
            if not _brain_phase_tracking or _verification_phase_emitted:
                return None
            _verification_phase_emitted = True
            return {
                "type": "phase_changed", "run_id": rid, "phase": "verification",
                "applied_thinking_mode": applied_thinking_mode,
            }

        if plan is not None:
            # The plan is model-authored context, not higher-priority authority.
            # Keep it in a normal user turn so it cannot elevate project-derived
            # text into the system role and does not create assistant→assistant
            # message ordering before the first execution call.
            messages.append({"role": "user", "content": plan_context_block(plan)})
        step = 0
        sent_guidance: set[str] = set()
        guidance_message_ids: set[str] = set()
        skill_context = SkillContext()
        if resume:
            try:
                skill_context.restore(rid)
            except (OSError, ValueError) as exc:
                yield {"type": "done", "ok": False, "steps": 0, "stop_reason": "error",
                       "error_code": "skill_restore_failed", "error": str(exc), "resumable": False}
                return
        skill_catalog = catalog_context()
        if skill_catalog:
            messages = insert_skill_context(messages, skill_catalog, CATALOG_ID)
            guidance_message_ids.add(CATALOG_ID)
        skill_reminder_pending = False
        skill_reminder_sent = False
        refresh_task_state = True
        while True:
            step += 1
            if cancel_event.is_set():
                yield {
                    "type": "done",
                    "ok": False,
                    "steps": step - 1,
                    "stop_reason": "cancelled",
                    "error": "Cancelled by user",
                    **_completion_fields(criteria, terminated_incomplete=True),
                }
                return

            yield {"type": "step_started", "step": step}

            if skill_reminder_pending and not skill_reminder_sent and not skill_context.snapshots():
                # A bounded reminder after actual project/tool discovery. This
                # uses the next existing inference turn, never blocks tools and
                # never assigns a domain/persona from user keywords.
                messages = insert_skill_context(messages, (
                    "[Рабочее напоминание Elira] Ты начала работу с проектом или системой. "
                    "Проверь каталог навыков выше и перед дальнейшей профильной работой "
                    "загрузи подходящие инструкции через runtime_control(operation='skill_load', "
                    "name=имя, query=причина). Для кода обычно нужен навык языка и code-change; "
                    "для поиска причины сбоя — diagnostics. Выбери сама по текущей задаче. "
                    "Если подходящего навыка нет или задача не требует профильной инструкции, "
                    "продолжай доступными инструментами."
                ), "elira-skill-reminder")
                skill_reminder_sent = True

            checklist_items: list[dict] = []
            try:
                if task_spec is not None:
                    from app.application.task_planner.service import list_checklist

                    checklist_items = list(
                        (list_checklist(rid) or {}).get("items") or []
                    )
            except Exception:
                checklist_items = []
            if (task_spec is not None or checklist_items) and refresh_task_state:
                messages = upsert_task_state_message(
                    messages,
                    build_task_state_block(
                        goal=str(getattr(task_spec, "goal", "")),
                        constraints=list(
                            getattr(task_spec, "constraints", None) or []
                        ),
                        criteria_rows=criteria.report(),
                        checklist_items=checklist_items,
                        mutated_files=mutated_files,
                        verifications=verification_log,
                        failed_attempts=[
                            *durable_failures,
                            *(call for call in call_log if call.endswith("error")),
                        ],
                        next_step="",
                    ),
                )
                refresh_task_state = False

            # Context accounting must include the exact JSON schemas sent this
            # step. Previously the UI could report ~11% while MCP schemas filled
            # almost the complete 128K server window.
            step_schemas = list(all_schemas)
            step_schemas.append(_ASK_USER_SCHEMA)
            if active_capability_groups or requested_builtin_tools or active_mcp_server_ids or active_lsp_server_ids or ssh_tools_active or itops_tools_active:
                step_schemas.append(_WORKFLOW_REQUEST_SCHEMA)
            guidance = task_guidance_blocks(
                {_schema_tool_name(schema) for schema in all_schemas},
                domain_policies=request_route.domain_policies,
            )
            if task_instructions:
                guidance["delivery"] = task_instructions
            if "work" in guidance and "work" not in sent_guidance:
                guidance["work"] += "\n" + _build_project_context(root, working_dir)
            if "project" in guidance and "project" not in sent_guidance:
                guidance["project"] += "\n" + _shell_guidance()
                guidance["project"] += (
                    _NO_PROJECT_BLOCK if _is_scratch_workspace(root) else _PROJECT_CONNECTED_BLOCK
                )
            new_guidance = [text for key, text in guidance.items() if key not in sent_guidance]
            if new_guidance:
                block = "[Инструкции текущей задачи]\n" + "\n\n".join(new_guidance)
                message_id = f"{rid}:guidance:{step}"
                guidance_message = {"role": "user", "content": block, "_msg_id": message_id}
                if step == 1:
                    # Put initial work instructions before the current request,
                    # so the model answers the user rather than the instructions.
                    messages.insert(len(messages) - 1, guidance_message)
                else:
                    messages.append(guidance_message)
                guidance_message_ids.add(message_id)
                sent_guidance.update(guidance)
            active_skill_text = skill_context.context()
            if active_skill_text:
                messages = insert_skill_context(messages, active_skill_text, CONTEXT_ID)
                guidance_message_ids.add(CONTEXT_ID)
            source_context = run_evidence.source_context(messages, max_chars=min(7000, safe_num_ctx))
            messages = [message for message in messages if message.get("_msg_id") != "web-source-context"]
            if source_context:
                source_message = {"role": "assistant", "content": source_context, "_msg_id": "web-source-context"}
                source_index = len(messages) - 1
                # Preserve the assistant tool_calls -> tool results protocol.
                while source_index > 1 and messages[source_index].get("role") == "tool":
                    source_index -= 1
                messages.insert(max(1, source_index), source_message)
            try:
                messages, _compacted, context_usage = _prepare_messages_for_llm(
                    messages,
                    num_ctx=safe_num_ctx,
                    model=model,
                    chat_fn=chat,
                    context_profile=context_profile,
                    tool_schemas=step_schemas,
                    cancel_handle=upstream_cancel_handle,
                    audit_sink=compaction_audit_sink,
                    pinned_message_ids=guidance_message_ids | ({"web-source-context"} if source_context else set()),
                )
            except ContextBudgetError as exc:
                if cancel_event.is_set():
                    yield {
                        "type": "done",
                        "ok": False,
                        "steps": step - 1,
                        "stop_reason": "cancelled",
                        "error": "Cancelled by user",
                        **_completion_fields(criteria, terminated_incomplete=True),
                    }
                    return
                if exc.usage is not None:
                    yield {
                        "type": "context_prepared",
                        "step": step,
                        "context": exc.usage,
                    }
                # One fresh slice can remove recent output. If that slice still
                # cannot make its first call, repeating it cannot make progress.
                terminal_budget = exc.fixed_payload_exceeds_budget or (resume and step == 1)
                yield {
                    "type": "done",
                    "ok": False,
                    "steps": step - 1,
                    "stop_reason": "error" if terminal_budget else "context_limit",
                    "resumable": not terminal_budget,
                    "error_code": "context_budget_exceeded",
                    "error": str(exc),
                    **_completion_fields(criteria, terminated_incomplete=True),
                }
                return
            if cancel_event.is_set():
                yield {
                    "type": "done",
                    "ok": False,
                    "steps": step - 1,
                    "stop_reason": "cancelled",
                    "error": "Cancelled by user",
                    **_completion_fields(criteria, terminated_incomplete=True),
                }
                return
            if _compacted:
                compaction_count += 1
                # Compaction creates a new prompt prefix anyway. Refresh the
                # task snapshot on the next step, then keep it stable again.
                refresh_task_state = True
                from app.application.context.compaction import extract_rolling_summary

                rolling_summary = extract_rolling_summary(messages)
                yield {
                    "type": "context_compacted",
                    "step": step,
                    "context": context_usage,
                    "rolling_summary": rolling_summary or None,
                }
            yield {
                "type": "context_prepared",
                "step": step,
                "context": context_usage,
            }

            # Schemas remain compact, but visibility is not an execution gate.
            # Inline recovery may recognize any implemented built-in the model
            # already knows; integrations still require a live owning provider.
            _inline_tool_names = {
                name for schema in step_schemas
                if (name := _schema_tool_name(schema))
            } | set(ALL_BUILTIN_TOOLS)
            schema_chars = (
                len(json.dumps(step_schemas, ensure_ascii=False, separators=(",", ":")))
                if step_schemas
                else 0
            )
            llm_prompt_chars = _messages_char_count(messages) + schema_chars
            run_evidence.mark_sources_presented(messages)
            if run_evidence.sources:
                yield {"type": "source_evidence", "step": step, "sources": run_evidence.sources}
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
                # Always send an explicit per-request mode. Reasoning-capable
                # server profiles default to ON, so omitting kwargs when the chip
                # is off would silently re-enable thinking after a profile switch.
                active_reasoning_effort = selected_reasoning_effort
                llm_options["reasoning_effort"] = active_reasoning_effort
                llm_options["chat_template_kwargs"] = _thinking_template_kwargs(
                    active_reasoning_effort
                )
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
                    cancel_handle=upstream_cancel_handle,
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
                        visible, pending_delta = pending_delta[:-32], pending_delta[-32:]
                        if visible:
                            yield {"type": "delta", "step": step, "text": visible, "answer_state": "draft"}
                response_content = str(((response.get("message") or {}).get("content") or ""))
                if not suppress_deltas and not _contains_tool_trace(response_content) and pending_delta:
                    yield {"type": "delta", "step": step, "text": pending_delta, "answer_state": "draft"}
            except Exception as exc:
                if cancel_event.is_set():
                    yield {
                        "type": "done",
                        "ok": False,
                        "steps": step,
                        "stop_reason": "cancelled",
                        "error": "Cancelled by user",
                        **_completion_fields(criteria, terminated_incomplete=True),
                    }
                    return
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
                    streaming=stream_chat is not None,
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
                    **_completion_fields(criteria, terminated_incomplete=True),
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
                    **_completion_fields(criteria, terminated_incomplete=True),
                }
                return

            message = (response or {}).get("message") or {}
            # Safety net: reasoning/content separation relies on the SERVER's
            # template parsing. If that ever breaks (model swap, llama.cpp
            # update), raw <think> blocks would flow into content → history →
            # the model keeps reasoning as content. Strip them client-side too.
            content = _strip_think_blocks(message.get("content") or "").strip()
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
                streaming=stream_chat is not None,
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
                "cached_prompt_tokens": int(step_usage.get("cached_prompt_tokens") or 0),
                "cache_hit_ratio": round(
                    float(step_usage.get("prompt_cache_hit_ratio") or 0.0), 4
                ),
                "completion_tokens": int(step_usage.get("completion_tokens") or 0),
                "total_tokens": int(step_usage.get("total_tokens") or 0),
                "prompt_tokens_per_second": round(
                    float(step_usage.get("prompt_tokens_per_second") or 0.0), 1
                ),
                "tokens_per_second": round(float(step_usage.get("tokens_per_second") or 0.0), 1),
                "ttft_ms": int(step_usage.get("ttft_ms") or 0),
                "context": context_usage,
                "profile": context_profile,
            }

            # Some local tool-calling models emit tool calls as JSON in
            # content instead of structured tool_calls. Recover them so the
            # loop still works.
            inline_calls: list[dict[str, Any]] = []
            if not tool_calls and content:
                inline_calls = _extract_inline_tool_calls(content, _inline_tool_names)
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
                call_log.append("repaired malformed internal tool trace")
                continue

            if content:
                last_text = content

            if not tool_calls:
                final_text = _strip_tool_call_markup(content or last_text)
                if pending_redirected_jobs:
                    pending_list = ", ".join(
                        str(pid) for pid in sorted(pending_redirected_jobs)
                    )
                    messages.append({"role": "assistant", "content": final_text})
                    messages.append({
                        "role": "user",
                        "content": (
                            "[internal background-job correction] Задачу нельзя "
                            "завершать, пока автоматически перенесённая SSH job не "
                            "получила terminal status. Вызови "
                            "run_server(action='logs', kind='job', pid=...) для каждого PID: "
                            f"{pending_list}. Если status=running — опроси позже; "
                            "если completed/failed/cancelled — учти результат и "
                            "только затем отвечай пользователю."
                        ),
                    })
                    call_log.append(
                        "completion blocked by redirected SSH job: " + pending_list
                    )
                    continue
                if (
                    not _local_catalog_correction_sent
                    and should_require_local_catalog_search(
                        final_text,
                        local_tabular_probe_seen=_local_tabular_catalog_probe_seen,
                        library_search_seen=_library_search_seen,
                    )
                ):
                    messages.append({"role": "assistant", "content": final_text})
                    messages.append({
                        "role": "user",
                        "content": (
                            "[internal local-catalog correction] Нельзя считать нулевой "
                            "exact-match или показанные head()/первые N строк доказательством "
                            "отсутствия позиции в локальном прайсе. Выполни "
                            "runtime_control(operation='library_search', query=...) с "
                            "несколькими независимыми токенами назначения, категории и, если "
                            "известны, бренда/модели. Если документ не находится в Library, "
                            "повтори локальный поиск по отдельным токенам без жёсткой фразы. "
                            "Только после этой проверки можно подтвердить отсутствие или "
                            "искать внешнюю альтернативу. Эта коррекция одноразовая."
                        ),
                    })
                    _local_catalog_correction_sent = True
                    call_log.append("completion blocked by unverified local-catalog absence")
                    continue
                if (
                    not _web_catalog_fallback_correction_sent
                    and should_require_web_catalog_fallback(
                        user_message,
                        final_text,
                        library_search_seen=_library_search_seen,
                        external_source_seen=run_evidence.has_external_source,
                    )
                ):
                    _web_was_activated = "web" not in active_capability_groups
                    if _web_was_activated:
                        active_capability_groups.add("web")
                        registry = rebuild_registry()
                        all_schemas = registry.collect_schemas()
                    messages.append({"role": "assistant", "content": final_text})
                    messages.append({
                        "role": "user",
                        "content": (
                            "[internal catalog Web fallback] Обязательный компонент "
                            "сборки не подтверждён после полного локального Library-поиска. "
                            "Нельзя завершать неполный BOM: вызови web_search, затем "
                            "web_fetch для первичного/магазинного источника и предложи "
                            "совместимую внешнюю альтернативу с подтверждёнными моделью, "
                            "ценой/наличием и URL. Локальные позиции не заменяй внешними, "
                            "если они уже подтверждены."
                        ),
                    })
                    _web_catalog_fallback_correction_sent = True
                    _catalog_web_fallback_required = True
                    call_log.append("completion blocked by missing catalog Web fallback")
                    if _web_was_activated:
                        yield {
                            "type": "runtime_activation_changed",
                            "run_id": rid,
                            "step": step,
                            "source": "catalog_absence_fallback",
                            "runtime_activation": runtime_activation_snapshot(),
                        }
                    continue
                if (
                    _catalog_web_fallback_required
                    and not _catalog_web_fetch_seen
                    and not _catalog_web_fetch_correction_sent
                ):
                    messages.append({"role": "assistant", "content": final_text})
                    messages.append({
                        "role": "user",
                        "content": (
                            "[internal catalog source correction] Одного web_search "
                            "недостаточно. Вызови web_fetch для выбранного результата и "
                            "подтверди на странице точную модель, совместимость и цену/наличие."
                        ),
                    })
                    _catalog_web_fetch_correction_sent = True
                    call_log.append("completion blocked until catalog source fetch")
                    continue
                if (
                    "web" not in active_capability_groups
                    and not _evidence_answer_correction_sent
                    and should_escalate_web_from_answer(final_text, raw_user_message)
                ):
                    active_capability_groups.add("web")
                    registry = rebuild_registry()
                    all_schemas = registry.collect_schemas()
                    messages.append({"role": "assistant", "content": final_text})
                    messages.append({
                        "role": "user",
                        "content": (
                            "[internal evidence correction] Ответ не проверен инструментами. "
                            "Web tools доступны: отсутствие актуальных знаний модели не "
                            "означает отсутствие доступа к источникам. Самостоятельно выполни "
                            "нужный поиск и прочитай первичные источники, затем ответь на исходный "
                            "вопрос; не спрашивай, выполнять ли поиск или загружать инструменты. "
                            "Явные ограничения пользователя сохраняются. Локальное состояние "
                            "по-прежнему проверяй локальными tools."
                        ),
                    })
                    _evidence_answer_correction_sent = True
                    yield {
                        "type": "runtime_activation_changed",
                        "run_id": rid,
                        "step": step,
                        "source": "evidence_uncertain_answer",
                        "runtime_activation": runtime_activation_snapshot(),
                    }
                    continue
                if (
                    requires_bom_validation(user_message)
                    and _bom_snapshot is None
                    and not _bom_validation_correction_sent
                ):
                    messages.append({"role": "assistant", "content": final_text})
                    messages.append({
                        "role": "user",
                        "content": (
                            "[internal BOM correction] Нельзя завершать локальную "
                            "спецификацию/КП с арифметикой модели. Вызови bom_validate "
                            "по исходному XLSX/CSV: передай точные колонки, выбранные коды "
                            "и количества, остаток, наценку, НДС и услуги. Используй только "
                            "подтверждённые rows/total из результата. Если ok=false — исправь "
                            "подбор; не создавай и не публикуй финальный документ до ok=true."
                        ),
                    })
                    _bom_validation_correction_sent = True
                    call_log.append("completion blocked by missing deterministic BOM validation")
                    continue
                if (
                    request_route.download_requested
                    and not run_evidence.receipts_of_kind(EvidenceKind.ARTIFACT)
                    and not _download_delivery_correction_sent
                ):
                    candidate = mutated_files[-1] if mutated_files else ""
                    messages.append({"role": "assistant", "content": final_text})
                    messages.append({
                        "role": "user",
                        "content": (
                            "[internal delivery correction] Пользователь запросил файл для "
                            "скачивания, но download artifact ещё не создан. "
                            + (
                                f"Опубликуй последний созданный файл `{candidate}` через "
                                "resource_publish и только затем дай итоговый ответ."
                                if candidate
                                else "Сначала создай требуемый файл, затем обязательно вызови "
                                "resource_publish и только после этого отвечай."
                            )
                        ),
                    })
                    _download_delivery_correction_sent = True
                    call_log.append("delivery router requested resource_publish")
                    continue
                download_delivery_failed = bool(
                    request_route.download_requested
                    and not run_evidence.receipts_of_kind(EvidenceKind.ARTIFACT)
                )
                bom_validation_failed = bool(
                    requires_bom_validation(user_message)
                    and _bom_snapshot is None
                )
                catalog_web_failed = bool(
                    _catalog_web_fallback_required
                    and not _catalog_web_fetch_seen
                )
                if download_delivery_failed:
                    final_text = (
                        "Файл не был опубликован: Workflow не получил подтверждённый "
                        "download artifact, поэтому кнопка скачивания не создана."
                    )
                elif bom_validation_failed:
                    final_text = (
                        "BOM не завершён: детерминированная проверка кодов, остатков, "
                        "цен, НДС и итогов не получила статус ok=true. Непроверенные "
                        "позиции и суммы не публикуются."
                    )
                elif catalog_web_failed:
                    final_text = (
                        "BOM не завершён: внешняя альтернатива не подтверждена чтением "
                        "источника. Результат Web-поиска без web_fetch не считается "
                        "проверкой модели, совместимости и наличия."
                    )
                elif requires_bom_validation(user_message) and _bom_snapshot is not None:
                    artifact_note = (
                        " Финальный файл опубликован."
                        if run_evidence.receipts_of_kind(EvidenceKind.ARTIFACT)
                        else ""
                    )
                    final_text = (
                        "BOM детерминированно проверен по локальному каталогу. "
                        f"Канонический итог: {_bom_snapshot['total']}."
                        f"{artifact_note} Receipt: "
                        f"{_bom_snapshot['receipt_sha256']}."
                    )
                unverified_document_qa_claim = (
                    run_evidence.has_unverified_document_qa_claim(final_text)
                )
                if unverified_document_qa_claim:
                    final_text = run_evidence.document_qa_backstop()
                answer_status = (
                    "degraded"
                    if (
                        download_delivery_failed
                        or bom_validation_failed
                        or catalog_web_failed
                        or unverified_document_qa_claim
                    )
                    else "complete"
                )
                # Evidence remains structured in the done event for observability,
                # but it cannot block finalization or rewrite the model's answer.
                criteria.finalize_conditionals()
                # Background processes are reported, never auto-stopped on a healthy
                # final answer. Explicit run_server(stop) or Workflow Stop owns
                # termination.
                try:
                    _alive = _run_owned_servers(rid)
                    if _alive:
                        # Background runtimes are not auto-stopped at finalization.
                        # They stop only through run_server(action='stop') or Workflow Stop.
                        _srv = "; ".join(
                            f"{s.get('kind', 'server')} pid={s['pid']}"
                            + (f" — {s['url']}" if s.get("url") else "")
                            for s in _alive)
                        final_text = final_text.rstrip() + (
                            f"\n\n[Фоновые процессы оставлены работать: {_srv}. "
                            "Остановить: run_server(action='stop', pid=…) или кнопкой Stop.]"
                        )
                except Exception:
                    pass
                _facts = _facts_digest(established_facts)
                _recent_digest = _recent_tools_digest(recent_tool_outputs)
                citations = run_evidence.citations(final_text)
                source_status = (
                    "unresolved" if any(item["status"] != "matched" for item in citations)
                    else "matched" if citations else "none"
                )
                yield {
                    "type": "final_response", "step": step, "text": final_text,
                    "answer_state": "accepted",
                    "answer_status": answer_status,
                    "established_facts": _facts,
                    "recent_tool_output": _recent_digest,
                    "sources": run_evidence.sources, "citations": citations,
                    "source_status": source_status,
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
                        verified=run_evidence.has_current_passing_verification,
                        mutation_targets=(
                            receipt.target
                            for receipt in run_evidence.receipts_of_kind(EvidenceKind.MUTATION)
                        ),
                        verification_targets=(
                            receipt.target
                            for receipt in run_evidence.receipts_of_kind(EvidenceKind.VERIFICATION)
                            if receipt.passed
                            and receipt.project_epoch == run_evidence.project_epoch
                        ),
                    )
                yield {
                    "type": "done",
                    "ok": True,  # runtime health — NOT "task solved"; see completion_status
                    "steps": step,
                    "stop_reason": "answer",
                    "answer_status": answer_status,
                    "error": None,
                    "established_facts": _facts,
                    "recent_tool_output": _recent_digest,
                    **_completion_fields(criteria),
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
                parsed_args = ToolRegistry._coerce_args(raw_args)
                _read_requested_path = ""
                _read_recovered_from = ""
                if name == "read_file":
                    _read_resource_resolved = False
                    _read_requested_path = str(parsed_args.get("path") or "").strip()
                    _read_target = Path(_read_requested_path)
                    if not _read_target.is_absolute():
                        _read_target = root / _read_target
                    try:
                        _project_file_exists = _read_target.resolve().is_file()
                    except OSError:
                        _project_file_exists = False
                    _recovered_path = recover_read_path_from_glob(
                        root,
                        _read_requested_path,
                        _last_glob_matches,
                    )
                    if _recovered_path:
                        _read_recovered_from = _read_requested_path
                        parsed_args["path"] = _recovered_path
                    elif not _project_file_exists:
                        _requested_name = Path(_read_requested_path.replace("\\", "/")).name.casefold()
                        _resource_matches = [
                            ref for ref in (resource_refs or [])
                            if Path(str(ref.get("name") or "").replace("\\", "/")).name.casefold()
                            == _requested_name
                            and str(ref.get("resource_id") or "").strip()
                        ]
                        if len(_resource_matches) == 1:
                            _resource_ref = _resource_matches[0]
                            _read_resource_resolved = True
                            parsed_args["_runtime_resource_id"] = str(
                                _resource_ref["resource_id"]
                            )
                            parsed_args["_runtime_resource_name"] = str(
                                _resource_ref.get("name") or ""
                            )
                    if (
                        not _recovered_path
                        and not _read_resource_resolved
                        and _read_file_failures.get(_read_requested_path, 0) >= 2
                    ):
                        parsed_args["_runtime_refuse_reason"] = (
                            "Сначала используй glob, ResourceRef или другой подтверждённый путь."
                        )
                if name == "resource_process":
                    _explicit_target = explicit_compute_target(user_message)
                    if (
                        str(parsed_args.get("operation") or "").strip().lower()
                        == "transcribe"
                        and str(parsed_args.get("execution_target") or "auto").strip().lower()
                        == "auto"
                        and _explicit_target is not None
                    ):
                        # User/workflow choice outranks an omitted/model-defaulted
                        # auto target and is passed through the canonical tool.
                        parsed_args["execution_target"] = _explicit_target
                    _placement_request = compute_placement_request(
                        task_text=user_message,
                        arguments=parsed_args,
                        resource_refs=resource_refs,
                    )
                    if _placement_request is not None:
                        # Reuse the existing ask_user state machine exactly. The
                        # assistant message shares this call object, so keep its
                        # function name/arguments consistent for the continuation.
                        name = "ask_user"
                        parsed_args = _placement_request
                        fn["name"] = name
                        fn["arguments"] = dict(parsed_args)
                if name == "ask_user":
                    _question = str(parsed_args.get("question") or "").strip()
                    _raw_opts = parsed_args.get("options")
                    _options = [str(o) for o in _raw_opts][:8] if isinstance(_raw_opts, list) else []
                    _input_rule: dict[str, Any] = {
                        "type": "string",
                        "title": _question or "Ответ",
                    }
                    if _options:
                        _input_rule["enum"] = _options
                    _request = {
                        "kind": "input",
                        "message": _question,
                        "schema": {
                            "type": "object",
                            "properties": {"answer": _input_rule},
                            "required": ["answer"],
                            "additionalProperties": False,
                        },
                        "sensitive": False,
                    }
                    if pause_for_workflow_request:
                        yield {
                            "type": "workflow_request",
                            "step": step,
                            "status": "needs_input",
                            "request": _request,
                        }
                        yield {
                            "type": "done",
                            "ok": False,
                            "steps": step,
                            "stop_reason": "workflow_request",
                            "status": "needs_input",
                            "request": _request,
                            "error": None,
                            **_completion_fields(criteria, terminated_incomplete=True),
                        }
                        return
                    _qid = uuid.uuid4().hex
                    with _WORKFLOW_RESPONSE_LOCK:
                        _WORKFLOW_RESPONSES[_qid] = None
                    yield {
                        "type": "workflow_request",
                        "step": step,
                        "status": "needs_input",
                        "response_id": _qid,
                        "request": _request,
                    }
                    _wait_started = time.monotonic()
                    _last_keepalive = _wait_started
                    _response: dict[str, Any] | None = None
                    while True:
                        if cancel_event.is_set():
                            break
                        with _WORKFLOW_RESPONSE_LOCK:
                            _stored = _WORKFLOW_RESPONSES.get(_qid)
                        if _stored is not None:
                            _response = dict(_stored)
                            break
                        _now = time.monotonic()
                        if _now - _last_keepalive >= _WORKFLOW_REQUEST_KEEPALIVE_EVERY:
                            yield {
                                "type": "workflow_request_wait",
                                "step": step,
                                "response_id": _qid,
                                "waited_s": int(_now - _wait_started),
                            }
                            _last_keepalive = _now
                        time.sleep(_WORKFLOW_REQUEST_POLL_INTERVAL)
                    with _WORKFLOW_RESPONSE_LOCK:
                        _WORKFLOW_RESPONSES.pop(_qid, None)
                    if cancel_event.is_set():
                        yield {
                            "type": "done", "ok": False, "steps": step,
                            "stop_reason": "cancelled", "error": "Cancelled by user",
                            **_completion_fields(criteria, terminated_incomplete=True),
                        }
                        return
                    _ans_text = (
                        "Workflow UI response: "
                        + json.dumps(_response or {}, ensure_ascii=False)
                    )
                    yield {
                        "type": "tool_call", "step": step, "tool": name,
                        "arguments": redact_secrets(parsed_args), "result": _ans_text,
                        "ok": str((_response or {}).get("action") or "") == "accept",
                    }
                    messages.append({"role": "tool", "content": _ans_text, "name": name})
                    tool_round_trips += 1
                    call_log.append(f"ask_user({(_question[:40] or '?')})")
                    continue
                if name == "workflow_request":
                    _kind = str(parsed_args.get("kind") or "").strip().lower()
                    _message = str(parsed_args.get("message") or "").strip()
                    if _kind not in {"input", "secret", "elevation"}:
                        _request_error = "workflow_request kind must be input, secret or elevation"
                        yield {
                            "type": "tool_call",
                            "step": step,
                            "tool": name,
                            "arguments": redact_secrets(parsed_args),
                            "result": _request_error,
                            "ok": False,
                        }
                        messages.append({
                            "role": "tool",
                            "content": _request_error,
                            "name": name,
                        })
                        tool_round_trips += 1
                        continue
                    _request_schema = parsed_args.get("schema")
                    if not isinstance(_request_schema, dict):
                        _request_schema = {}
                    if _kind == "elevation":
                        _program = str(parsed_args.get("program") or "").strip()
                        _raw_args = parsed_args.get("args")
                        _elevation_args = (
                            [str(value) for value in _raw_args]
                            if isinstance(_raw_args, list)
                            else []
                        )
                        if not _program:
                            _request_error = "elevation request requires program"
                            yield {
                                "type": "tool_call",
                                "step": step,
                                "tool": name,
                                "arguments": redact_secrets(parsed_args),
                                "result": _request_error,
                                "ok": False,
                            }
                            messages.append({
                                "role": "tool",
                                "content": _request_error,
                                "name": name,
                            })
                            tool_round_trips += 1
                            continue
                        _native_spec: dict[str, Any] = {
                            "program": _program,
                            "args": _elevation_args,
                        }
                        _cwd = str(parsed_args.get("cwd") or "").strip()
                        if _cwd:
                            _native_spec["cwd"] = _cwd
                        _request_schema = {"x-elira-elevation": _native_spec}
                    _request = {
                        "kind": _kind,
                        "message": _message,
                        "schema": _request_schema,
                        "sensitive": _kind == "secret",
                    }
                    _request_status = f"needs_{_kind}"
                    if pause_for_workflow_request:
                        yield {
                            "type": "workflow_request",
                            "step": step,
                            "status": _request_status,
                            "request": _request,
                        }
                        yield {
                            "type": "done",
                            "ok": False,
                            "steps": step,
                            "stop_reason": "workflow_request",
                            "status": _request_status,
                            "request": _request,
                            "error": None,
                            **_completion_fields(criteria, terminated_incomplete=True),
                        }
                        return
                    _response_id = uuid.uuid4().hex
                    with _WORKFLOW_RESPONSE_LOCK:
                        _WORKFLOW_RESPONSES[_response_id] = None
                    yield {
                        "type": "workflow_request",
                        "step": step,
                        "status": _request_status,
                        "response_id": _response_id,
                        "request": _request,
                    }
                    _wait_started = time.monotonic()
                    _last_keepalive = _wait_started
                    _response: dict[str, Any] | None = None
                    while True:
                        if cancel_event.is_set():
                            break
                        with _WORKFLOW_RESPONSE_LOCK:
                            _stored = _WORKFLOW_RESPONSES.get(_response_id)
                        if _stored is not None:
                            _response = dict(_stored)
                            break
                        _now = time.monotonic()
                        if _now - _last_keepalive >= _WORKFLOW_REQUEST_KEEPALIVE_EVERY:
                            yield {
                                "type": "workflow_request_wait",
                                "step": step,
                                "response_id": _response_id,
                                "waited_s": int(_now - _wait_started),
                            }
                            _last_keepalive = _now
                        time.sleep(_WORKFLOW_REQUEST_POLL_INTERVAL)
                    with _WORKFLOW_RESPONSE_LOCK:
                        _WORKFLOW_RESPONSES.pop(_response_id, None)
                    if cancel_event.is_set():
                        yield {
                            "type": "done",
                            "ok": False,
                            "steps": step,
                            "stop_reason": "cancelled",
                            "error": "Cancelled by user",
                            **_completion_fields(criteria, terminated_incomplete=True),
                        }
                        return
                    _response_text = (
                        "Workflow UI response: "
                        + json.dumps(_response or {}, ensure_ascii=False)
                    )
                    yield {
                        "type": "tool_call",
                        "step": step,
                        "tool": name,
                        "arguments": redact_secrets(parsed_args),
                        "result": _response_text,
                        "ok": str((_response or {}).get("action") or "") == "accept",
                    }
                    messages.append({
                        "role": "tool",
                        "content": _response_text,
                        "name": name,
                    })
                    tool_round_trips += 1
                    call_log.append(f"workflow_request({_kind})")
                    continue
                if name == "todo_update":
                    # P12.1: checklist mutations are bound to the current run.
                    # The model never chooses the run_id; executor policy/audit
                    # still applies below because todo_update is a normal tool.
                    parsed_args["run_id"] = rid
                    # Resumed runs may replace or reshape their checklist freely.
                if name == "delegate_task":
                    # P12.2: subagents are children of the current run. The
                    # model chooses role/task, not parent_run_id. The workflow
                    # permission is inherited so bypass stays bypass end-to-end.
                    parsed_args["run_id"] = rid
                    parsed_args["permission_mode"] = permission_mode
                if name in {"file_gen", "resource_publish"}:
                    # Bind document-QA retry accounting to this run. The schema does
                    # not expose run_id, so the model cannot choose or reuse it.
                    parsed_args["run_id"] = rid
                    if requires_bom_validation(user_message):
                        if _bom_snapshot is None:
                            parsed_args["_runtime_refuse_reason"] = (
                                "Сначала вызови bom_validate и получи ok=true; затем "
                                "создавай финальный BOM-документ."
                            )
                        elif name == "file_gen":
                            parsed_args["_runtime_bom_snapshot"] = dict(_bom_snapshot)
                        else:
                            parsed_args["_runtime_refuse_reason"] = (
                                "Произвольный файл нельзя связать с BOM receipt. "
                                "Создай финальный документ через file_gen: runtime "
                                "подставит только канонические rows/total."
                            )
                if document_page_count_contract is not None:
                    is_generated_document = (
                        name == "file_gen"
                        and str(parsed_args.get("format") or "").strip().lower()
                        in {"word", "docx", "pdf"}
                    )
                    is_published_document = (
                        name == "resource_publish"
                        and str(parsed_args.get("project_path") or "").strip().lower()
                        .endswith((".docx", ".pdf"))
                    )
                    if is_generated_document or is_published_document:
                        # The user contract outranks a model-supplied guess/omission.
                        parsed_args["expected_page_count"] = document_page_count_contract
                # Phase is presentation-only. Evidence is recorded only after
                # successful executor return below, never from model intent.
                if RunEvidence.is_verification_tool(name, arguments=parsed_args):
                    _phase_event = _enter_verification_phase()
                    if _phase_event is not None:
                        yield _phase_event
                _request = ToolExecutionRequest(
                    run_id=rid,
                    agent_id=effective_agent_id,
                    project_scope_id=scope_id,
                    tool_name=name,
                    args=parsed_args,
                    source="code_agent",
                    permission_mode=permission_mode,
                    workflow_approved=workflow_approval_matches(
                        pending_workflow_approval,
                        name,
                        parsed_args,
                    ),
                )
                if _request.workflow_approved:
                    pending_workflow_approval = {}
                _delay_tool_started = not (
                    _request.workflow_approved
                    or permission_mode_auto_approves(_request)
                )
                if not _delay_tool_started:
                    yield {
                        "type": "tool_started",
                        "step": step,
                        "tool": name,
                        "arguments": redact_secrets(parsed_args),
                    }
                _exec_result = None
                for _hb in _exec_with_heartbeat(
                    lambda: _kernel_exec(_request, dispatch_fn=registry.dispatch_raw),
                    step,
                    cancel_event,
                ):
                    if "__result__" in _hb:
                        _exec_result = _hb["__result__"]
                    else:
                        yield _hb
                if _exec_result.status == "waiting_approval":
                    raw_request = (_exec_result.output or {}).get("request")
                    _display_args = redact_secrets(parsed_args)
                    _workflow_request = (
                        dict(raw_request) if isinstance(raw_request, dict) else {
                            "kind": "approval",
                            "message": (
                                f"Подтвердить действие {name} с аргументами: "
                                f"{json.dumps(_display_args, ensure_ascii=False)}"
                            ),
                            "schema": {
                                "x-elira-tool": {
                                    "name": name,
                                    "arguments": _display_args,
                                    "args_sha256": tool_args_sha256(parsed_args),
                                },
                            },
                            "sensitive": False,
                        }
                    )
                    _response_id = uuid.uuid4().hex
                    if pause_for_workflow_request:
                        yield {
                            "type": "workflow_request",
                            "step": step,
                            "status": "waiting_approval",
                            "response_id": _response_id,
                            "request": _workflow_request,
                        }
                        yield {
                            "type": "done",
                            "ok": False,
                            "steps": step,
                            "stop_reason": "workflow_request",
                            "status": "waiting_approval",
                            "response_id": _response_id,
                            "request": _workflow_request,
                            "error": None,
                            **_completion_fields(criteria, terminated_incomplete=True),
                        }
                        return
                    with _WORKFLOW_RESPONSE_LOCK:
                        _WORKFLOW_RESPONSES[_response_id] = None
                    yield {
                        "type": "workflow_request",
                        "step": step,
                        "status": "waiting_approval",
                        "response_id": _response_id,
                        "request": _workflow_request,
                    }
                    _wait_started = time.monotonic()
                    _last_keepalive = _wait_started
                    _response: dict[str, Any] | None = None
                    while True:
                        if cancel_event.is_set():
                            break
                        with _WORKFLOW_RESPONSE_LOCK:
                            stored_response = _WORKFLOW_RESPONSES.get(_response_id)
                        if stored_response is not None:
                            _response = dict(stored_response)
                            break
                        _now = time.monotonic()
                        if _now - _last_keepalive >= _WORKFLOW_REQUEST_KEEPALIVE_EVERY:
                            yield {
                                "type": "workflow_request_wait",
                                "step": step,
                                "response_id": _response_id,
                                "waited_s": int(_now - _wait_started),
                            }
                            _last_keepalive = _now
                        time.sleep(_WORKFLOW_REQUEST_POLL_INTERVAL)
                    with _WORKFLOW_RESPONSE_LOCK:
                        _WORKFLOW_RESPONSES.pop(_response_id, None)
                    if cancel_event.is_set():
                        yield {
                            "type": "done",
                            "ok": False,
                            "steps": step,
                            "stop_reason": "cancelled",
                            "error": "Cancelled by user",
                            **_completion_fields(criteria, terminated_incomplete=True),
                        }
                        return
                    if str((_response or {}).get("action") or "") == "accept":
                        # The accepted Workflow request authorizes this exact
                        # tool+arguments pair once; no internal approval store.
                        _request.workflow_approved = True
                        if _delay_tool_started:
                            yield {
                                "type": "tool_started",
                                "step": step,
                                "tool": name,
                                "arguments": redact_secrets(parsed_args),
                            }
                        for _hb in _exec_with_heartbeat(
                            lambda: _kernel_exec(_request, dispatch_fn=registry.dispatch_raw),
                            step,
                            cancel_event,
                        ):
                            if "__result__" in _hb:
                                _exec_result = _hb["__result__"]
                            else:
                                yield _hb
                    else:
                        _exec_result = ToolExecutionResult(
                            status="rejected",
                            output={
                                "ok": False,
                                "text": (
                                    "Пользователь отклонил это действие. Не повторяй "
                                    "вызов; скорректируй подход или заверши ход."
                                ),
                                "error": "workflow_request_declined",
                            },
                            error="workflow_request_declined",
                        )
                tool_meta = _exec_result.output
                _skill_snapshot_changed = False
                _skill_receipt = None
                if (name == "runtime_control" and str(parsed_args.get("operation") or "").strip().lower() == "skill_load"
                        and _exec_result.status == "ok" and tool_meta.get("ok")):
                    try:
                        skill_result = tool_meta.get("result", {})
                        _skill_snapshot_changed = skill_context.activate(
                            skill_result["skill"], skill_result.get("reason", ""),
                        )
                        selected = next(item for item in skill_context.snapshots()
                                        if item["name"] == skill_result["skill"]["name"])
                        _skill_receipt = {key: selected[key] for key in ("name", "title", "sha256", "reason")}
                        _skill_receipt["already_loaded"] = not _skill_snapshot_changed
                        # The full instruction has one pinned owner. Tool history
                        # and UI receive a receipt, not a duplicate instruction.
                        tool_meta = {"ok": True, "status": "completed", "text": json.dumps({
                            "skill": _skill_receipt,
                            "message": "Skill instructions are active in the current task context.",
                        }, ensure_ascii=False)}
                    except (KeyError, TypeError, OSError, ValueError) as exc:
                        tool_meta = {"ok": False, "error": "skill_activation_failed", "text": str(exc)}
                _runtime_activation_snapshot: dict[str, Any] | None = None
                _runtime_request_status = str(
                    tool_meta.get("status") or ""
                ).strip()
                if (
                    name == "runtime_control"
                    and _runtime_request_status
                    in {
                        "needs_input",
                        "needs_secret",
                        "needs_elevation",
                        "waiting_approval",
                    }
                ):
                    _raw_runtime_request = tool_meta.get("request")
                    _runtime_request = (
                        dict(_raw_runtime_request)
                        if isinstance(_raw_runtime_request, dict)
                        else {}
                    )
                    _runtime_kind = str(
                        _runtime_request.get("kind")
                        or {
                            "needs_input": "input",
                            "needs_secret": "secret",
                            "needs_elevation": "elevation",
                            "waiting_approval": "approval",
                        }[_runtime_request_status]
                    ).strip()
                    _runtime_request.update({
                        "kind": _runtime_kind,
                        "message": str(
                            _runtime_request.get("message")
                            or "Runtime требует данные для продолжения."
                        ),
                        "schema": (
                            dict(_runtime_request.get("schema"))
                            if isinstance(_runtime_request.get("schema"), dict)
                            else {}
                        ),
                        "sensitive": _runtime_kind == "secret"
                        or bool(_runtime_request.get("sensitive", False)),
                    })
                    _runtime_response_id = uuid.uuid4().hex
                    yield {
                        "type": "tool_call",
                        "step": step,
                        "tool": name,
                        "arguments": redact_secrets(parsed_args),
                        "result": _truncate(str(tool_meta.get("text") or "")),
                        "ok": False,
                        "state_changed": False,
                    }
                    if pause_for_workflow_request:
                        yield {
                            "type": "workflow_request",
                            "step": step,
                            "status": _runtime_request_status,
                            "response_id": _runtime_response_id,
                            "request": _runtime_request,
                        }
                        yield {
                            "type": "done",
                            "ok": False,
                            "steps": step,
                            "stop_reason": "workflow_request",
                            "status": _runtime_request_status,
                            "response_id": _runtime_response_id,
                            "request": _runtime_request,
                            "error": None,
                            **_completion_fields(criteria, terminated_incomplete=True),
                        }
                        return
                    with _WORKFLOW_RESPONSE_LOCK:
                        _WORKFLOW_RESPONSES[_runtime_response_id] = None
                    yield {
                        "type": "workflow_request",
                        "step": step,
                        "status": _runtime_request_status,
                        "response_id": _runtime_response_id,
                        "request": _runtime_request,
                    }
                    _runtime_wait_started = time.monotonic()
                    _runtime_last_keepalive = _runtime_wait_started
                    _runtime_response: dict[str, Any] | None = None
                    while True:
                        if cancel_event.is_set():
                            break
                        with _WORKFLOW_RESPONSE_LOCK:
                            _stored_runtime_response = _WORKFLOW_RESPONSES.get(
                                _runtime_response_id
                            )
                        if _stored_runtime_response is not None:
                            _runtime_response = dict(_stored_runtime_response)
                            break
                        _now = time.monotonic()
                        if (
                            _now - _runtime_last_keepalive
                            >= _WORKFLOW_REQUEST_KEEPALIVE_EVERY
                        ):
                            yield {
                                "type": "workflow_request_wait",
                                "step": step,
                                "response_id": _runtime_response_id,
                                "waited_s": int(_now - _runtime_wait_started),
                            }
                            _runtime_last_keepalive = _now
                        time.sleep(_WORKFLOW_REQUEST_POLL_INTERVAL)
                    with _WORKFLOW_RESPONSE_LOCK:
                        _WORKFLOW_RESPONSES.pop(_runtime_response_id, None)
                    if cancel_event.is_set():
                        yield {
                            "type": "done",
                            "ok": False,
                            "steps": step,
                            "stop_reason": "cancelled",
                            "error": "Cancelled by user",
                            **_completion_fields(criteria, terminated_incomplete=True),
                        }
                        return
                    _runtime_response_text = (
                        "Workflow UI response for runtime_control: "
                        + json.dumps(_runtime_response or {}, ensure_ascii=False)
                    )
                    messages.append({
                        "role": "tool",
                        "content": _runtime_response_text,
                        "name": name,
                    })
                    tool_round_trips += 1
                    call_log.append(f"runtime_control({_runtime_request_status})")
                    continue
                if (
                    name == "capability_load"
                    and _exec_result.status == "ok"
                    and bool(tool_meta.get("ok", True))
                ):
                    _capability_group = str(
                        tool_meta.get("capability_group")
                        or parsed_args.get("group")
                        or ""
                    ).strip().lower()
                    if _capability_group in normalize_capability_groups(
                        (_capability_group,)
                    ):
                        active_capability_groups.add(_capability_group)
                        registry = rebuild_registry()
                        all_schemas = registry.collect_schemas()
                        _runtime_activation_snapshot = runtime_activation_snapshot()
                if (
                    name == "runtime_control"
                    and _exec_result.status == "ok"
                    and bool(tool_meta.get("ok", True))
                ):
                    # Reveal only the provider explicitly selected in THIS run.
                    # Other configured/running integrations remain behind the
                    # compact runtime_control surface and add zero prompt tokens.
                    _runtime_operation = str(
                        parsed_args.get("operation") or ""
                    ).strip().lower()
                    _runtime_config = parsed_args.get("config")
                    _runtime_server_id = str(
                        tool_meta.get("server_id")
                        or parsed_args.get("server_id")
                        or (
                            _runtime_config.get("id")
                            if isinstance(_runtime_config, dict)
                            else ""
                        )
                        or ""
                    ).strip()
                    if _runtime_operation in {"mcp_start", "mcp_restart", "mcp_tools"} and _runtime_server_id:
                        active_mcp_server_ids.add(_runtime_server_id)
                        active_mcp_schema_queries[_runtime_server_id] = str(
                            parsed_args.get("query") or user_message
                        ).strip()
                    elif _runtime_operation in {"mcp_stop", "mcp_remove"}:
                        active_mcp_server_ids.discard(_runtime_server_id)
                        active_mcp_schema_queries.pop(_runtime_server_id, None)
                    elif _runtime_operation in {"lsp_start", "lsp_restart"} and _runtime_server_id:
                        active_lsp_server_ids.add(_runtime_server_id)
                    elif _runtime_operation in {"lsp_stop", "lsp_remove"}:
                        active_lsp_server_ids.discard(_runtime_server_id)
                    elif _runtime_operation in {"ssh_hosts", "ssh_set_hosts"}:
                        ssh_tools_active = True
                    elif _runtime_operation.startswith("itops_"):
                        itops_tools_active = True
                    registry = rebuild_registry()
                    all_schemas = registry.collect_schemas()
                    _runtime_activation_snapshot = runtime_activation_snapshot()
                if name == "run_server":
                    _rs_act = str(parsed_args.get("action") or "start").lower()
                    if tool_meta.get("actual_url"):
                        _last_server_url = str(tool_meta.get("actual_url"))
                    elif _last_server_url and (
                        _rs_act in ("stop", "stop_all") or not tool_meta.get("ok", True)
                    ):
                        # R2 (scoped, review #1/#8/#15): forget the URL only if ITS
                        # server is really gone — a failed call about a DIFFERENT
                        # server (wrong-pid logs, second start on a taken port, a
                        # rejected approval that never ran) must not wipe a live
                        # server's address. A later verdict re-adopts via actual_url.
                        if not _server_url_alive(_last_server_url):
                            _last_server_url = ""
                if (
                    tool_meta.get("backgrounded") is True
                    and str(tool_meta.get("status") or "") == "running"
                    and tool_meta.get("pid") is not None
                ):
                    pending_redirected_jobs.add(int(tool_meta["pid"]))
                if name == "run_server" and parsed_args.get("pid") is not None:
                    _job_pid = int(parsed_args["pid"])
                    _job_status = str(tool_meta.get("status") or "")
                    if (
                        _job_pid in pending_redirected_jobs
                        and str(parsed_args.get("action") or "start").lower()
                        in {"logs", "stop"}
                        and _job_status in {"completed", "failed", "cancelled"}
                    ):
                        pending_redirected_jobs.discard(_job_pid)
                text_result = str(tool_meta.get("text", ""))
                _tool_ok = bool(tool_meta.get("ok", _exec_result.status == "ok"))
                if _tool_ok and name == "glob":
                    _last_glob_matches = tuple(
                        line.strip()
                        for line in text_result.splitlines()
                        if line.strip()
                        and not line.startswith("ERROR:")
                        and not line.startswith("No files match")
                    )
                if name == "read_file" and _read_requested_path:
                    if _tool_ok:
                        _read_file_failures.pop(_read_requested_path, None)
                    elif tool_meta.get("error") != "read_retry_exhausted":
                        _read_file_failures[_read_requested_path] = (
                            _read_file_failures.get(_read_requested_path, 0) + 1
                        )
                if name == "bom_validate":
                    _bom_snapshot = None
                    if _tool_ok:
                        from app.application.code_agent.tools._bom import make_bom_snapshot

                        _bom_snapshot = make_bom_snapshot(tool_meta)
                        if _bom_snapshot is None:
                            _tool_ok = False
                            tool_meta.update({
                                "ok": False,
                                "error": "invalid_bom_snapshot",
                                "text": (
                                    "ERROR: bom_validate returned no immutable "
                                    "rows/total/catalog receipt."
                                ),
                            })
                            text_result = str(tool_meta["text"])
                if _read_recovered_from:
                    _recovery_note = (
                        "[runtime: однозначно восстановил обрезанный путь из результата "
                        f"предыдущего glob: '{_read_recovered_from}' → "
                        f"'{parsed_args.get('path')}']\n"
                    )
                    text_result = _recovery_note + text_result
                    tool_meta["text"] = text_result
                    tool_meta["recovered_from"] = _read_recovered_from
                    tool_meta["recovered_path"] = str(parsed_args.get("path") or "")
                if _tool_ok and is_local_tabular_catalog_probe(name, parsed_args):
                    _local_tabular_catalog_probe_seen = True
                if (
                    _tool_ok
                    and name == "runtime_control"
                    and str(parsed_args.get("operation") or "").strip().lower()
                    == "library_search"
                    and str(parsed_args.get("query") or "").strip()
                ):
                    _library_search_seen = True
                if (
                    _tool_ok
                    and name == "web_fetch"
                    and _catalog_web_fallback_required
                ):
                    _catalog_web_fetch_seen = True
                _failed_call = _exec_result.status != "ok" or tool_meta.get("ok") is False
                _evidence_web_activated = False
                if _failed_call:
                    _failure_counts[name] = _failure_counts.get(name, 0) + 1
                    _failure_counts["__all__"] = _failure_counts.get("__all__", 0) + 1
                    _failure_error = str(
                        tool_meta.get("error") or text_result.split("\n", 1)[0]
                    )[:500]
                    if (
                        "web" not in active_capability_groups
                        and should_escalate_web_after_failure(
                            tool_name=name,
                            error=_failure_error,
                            failure_count=_failure_counts["__all__"],
                            arguments=parsed_args,
                        )
                    ):
                        active_capability_groups.add("web")
                        registry = rebuild_registry()
                        all_schemas = registry.collect_schemas()
                        _runtime_activation_snapshot = runtime_activation_snapshot()
                        _evidence_web_activated = True
                _state_changed = tool_state_changed(
                    name,
                    tool_meta,
                    exec_ok=(
                        _exec_result.status == "ok"
                        and bool(tool_meta.get("ok", True))
                    ),
                )
                event: dict[str, Any] = {
                    "type": "tool_call",
                    "step": step,
                    "tool": name,
                    "arguments": redact_secrets({
                        key: value for key, value in parsed_args.items()
                        if not key.startswith("_runtime_")
                    }),
                    "result": _truncate(text_result),
                    "ok": bool(tool_meta.get("ok", _exec_result.status == "ok")),
                    # Server-owned mutation flag (ToolSpec.side_effect + real
                    # touched target on a successful execution). Read-only
                    # touched_path (read_file/ssh_read) stays False — delivery
                    # auto-continuation counts MUTATIONS, not reads.
                    "state_changed": _state_changed,
                }
                if _runtime_activation_snapshot is not None:
                    event["runtime_activation"] = _runtime_activation_snapshot
                if _skill_receipt is not None:
                    event["skill"] = _skill_receipt
                if skill_catalog and (name in {"read_file", "project_map", "glob", "grep", "run_bash", "write_file", "edit_file"}
                        or name.startswith(("itops_", "ssh_", "lsp_"))
                        or (name == "runtime_control" and str(parsed_args.get("operation") or "").startswith(("itops_", "ssh_", "lsp_")))):
                    skill_reminder_pending = True
                task_state_verification = ""
                if (
                    name == "run_bash"
                    and tool_meta.get("exit_code") is not None
                    and RunEvidence.is_verification_tool(
                        name,
                        arguments=parsed_args,
                        output=tool_meta,
                    )
                ):
                    from app.core.redaction import redact_text

                    command = redact_text(str(parsed_args.get("command") or ""))[:120]
                    task_state_verification = (
                        f"{command} → exit={tool_meta.get('exit_code')}"
                    )
                    event["task_state_verification"] = task_state_verification
                if not _tool_ok:
                    event["task_state_failure"] = f"{name}: error"
                for opt in (
                    "touched_path", "old_content", "new_content", "diff_action",
                    "exit_code", "verifier", "evidence", "download_url",
                    "download_name", "project_path", "size", "sha256", "document_qa",
                    "actual_url", "local_url", "actual_port", "port", "pid",
                    "server_started", "media", "action", "kind", "status",
                    "job_id", "log_path", "recovered",
                    "backgrounded", "redirected_from", "redirect_reason",
                    "remote_pid", "remote_pid_pending",
                    "remote_cleanup_supported", "remote_cleanup_status",
                    "remote_process_identity_captured", "remote_cleanup_ready",
                    "error", "recovered_from", "recovered_path",
                    "resolved_from_resource", "resource_id", "resource_name",
                    "sources",
                ):
                    if opt in tool_meta:
                        # Keep diff payloads truncated too to keep events small.
                        val = tool_meta[opt]
                        if isinstance(val, str) and opt in {"old_content", "new_content"} and len(val) > 40000:
                            event[opt] = val[:40000] + "\n[... truncated]"
                        else:
                            event[opt] = val
                run_evidence.record_tool_result(
                    tool_name=name,
                    arguments=parsed_args,
                    execution_status=_exec_result.status,
                    output=tool_meta,
                    text_result=text_result,
                    state_changed=_state_changed,
                )
                if _state_changed:
                    criteria.invalidate_after_mutation()
                    verification_log.clear()
                if _skill_snapshot_changed:
                    yield {"type": "skills_changed", "step": step,
                           "active_skills": skill_context.snapshots()}
                yield event
                if _evidence_web_activated:
                    yield {
                        "type": "runtime_activation_changed",
                        "run_id": rid,
                        "step": step,
                        "source": "evidence_failure",
                        "trigger_tool": name,
                        "runtime_activation": runtime_activation_snapshot(),
                    }
                tool_round_trips += 1
                _hint = _short_arg_hint(parsed_args)
                call_log.append(
                    f"{name}({_hint}) {'ok' if tool_meta.get('ok', True) else 'error'}"
                )
                if _failed_call:
                    _path = str(parsed_args.get("path") or parsed_args.get("command") or "").strip()
                    _err = str(tool_meta.get("error") or text_result.split("\n", 1)[0])[:180]
                    _last_failure = {"tool": name, "path": _path, "error": _err}
                elif _exec_result.status == "ok" and bool(tool_meta.get("ok", True)):
                    # A later successful operation makes an unrelated old failure a
                    # bad deterministic continuation hint. Keep historical failure
                    # evidence, but clear the ACTIVE failure context.
                    _last_failure = {}
                # Smart-truncate tool output before feeding it back to the
                # LLM. Without this, a single huge `run_bash` or `read_file`
                # could blow out `num_ctx` and start eating the system
                # prompt off the front of the context.
                _tool_content = _truncate_for_llm(text_result)
                if _evidence_web_activated:
                    _tool_content += (
                        "\n\n[EVIDENCE ROUTER] Web tools are now available. Before retrying "
                        "this failed external/unknown operation, search the official "
                        "documentation or primary source for the exact error and version."
                    )
                # Grounding fact from this call — computed HERE (before the tool
                # message is appended) so the progress controller can judge whether
                # the call revealed anything NEW.
                _fact = _fact_from_tool(
                    name, _hint, text_result, ok=_tool_ok, verifier=bool(tool_meta.get("verifier")),
                )
                if tool_meta.get("touched_path"):
                    touched_files.append(str(tool_meta.get("touched_path")))
                    if _state_changed:
                        mutated = str(tool_meta.get("touched_path"))
                        if mutated not in mutated_files:
                            mutated_files.append(mutated)
                if task_state_verification:
                    verification_log.append(task_state_verification)
                # Per-criterion state (Ph7.4): feed verifier verdicts BEFORE the
                # next model turn, so the completion report remains honest.
                # A verifier tool (verifier=True) confirms/fails a matching criterion;
                # a coding test/verify that went GREEN (exit 0) is a passing check too.
                # A verifier tool records structured evidence; run_bash records real
                # stdout/stderr + exit_code (command_output / command_check). ONLY a
                # call that actually RAN (kernel status ok) is a verdict: a rejected
                # call never executed, and its {ok:False} output
                # must not hard-fail a criterion (a
                # tool's own red result, e.g. assert-miss or exit!=0, still ships with
                # status ok and records honestly).
                if _exec_result.status == "ok":
                    _record_criterion_verdict(
                        criteria, name, parsed_args, tool_meta, text_result, _tool_ok)
                messages.append({
                    "role": "tool",
                    "content": _tool_content,
                    "name": name,
                })
                if _fact:
                    established_facts.append(_fact)
                elif _failed_call:
                    # No grounding fact from a failure, but the deterministic stop
                    # summary must still name the concrete file/command + error.
                    established_facts.append(
                        f"НЕУДАЧА: {name}({_last_failure.get('path','')}) — "
                        f"{_last_failure.get('error','')}"
                    )
                _recent = _recent_tool_snippet(name, _hint, text_result)
                if _recent:
                    recent_tool_outputs.append(_recent)

    finally:
        try:
            _unregister_run(rid)
        except Exception:
            pass


def stream_code_agent(
    *,
    user_message: str,
    memory_query: str | None = None,
    task_instructions: str = "",
    project_root: Path | str,
    working_dir: Path | str | None = None,
    model: str = "auto",
    agent_id: str = "code-agent",
    conversation_history: list[dict[str, Any]] | None = None,
    run_id: str | None = None,
    session_id: str | None = None,
    num_ctx: int | None = None,
    base_tools: tuple[str, ...] | list[str] | None = None,
    auto_remember: bool = True,
    chat_fn: Callable[..., dict[str, Any]] | None = None,
    chat_stream_fn: Callable[..., Any] | None = None,
    resume: bool = False,
    profile_name: str = "Инженерный",
    permission_mode: str = "ask",
    thinking: bool = False,
    reasoning_effort: str | None = None,
    pause_for_workflow_request: bool = False,
    workflow_approval: dict[str, Any] | None = None,
    resource_refs: list[dict[str, Any]] | None = None,
    source_run_ids: list[str] | None = None,
) -> Iterator[dict[str, Any]]:
    """Journalled public stream around the existing model/tool runtime."""
    from app.application.code_agent.run_journal import RunJournal, discover_capabilities, related_sources, sanitize_event

    rid = run_id or uuid.uuid4().hex
    initial_tools = tuple(base_tools) if base_tools is not None else _CODE_AGENT_BASE_TOOLS
    journal = RunJournal.load(rid) if resume else RunJournal(rid)
    selected_reasoning_effort = _normalize_reasoning_effort(
        reasoning_effort,
        thinking=thinking,
    )
    request = {
        "user_message": user_message,
        "memory_query": memory_query,
        "task_instructions": task_instructions,
        "project_root": str(project_root),
        "working_dir": str(working_dir) if working_dir is not None else None,
        "model": model,
        "agent_id": agent_id,
        "conversation_history": conversation_history or [],
        "session_id": session_id,
        "num_ctx": int(num_ctx) if num_ctx else None,
        "base_tools": list(initial_tools),
        "auto_remember": bool(auto_remember),
        "profile_name": profile_name,
        "permission_mode": permission_mode,
        "thinking": selected_reasoning_effort != "none",
        "reasoning_effort": selected_reasoning_effort,
        "pause_for_workflow_request": bool(pause_for_workflow_request),
        "resource_refs": list(resource_refs or []),
        "source_run_ids": list(source_run_ids or [])[-8:],
    }
    terminal = False
    answer_media = merge_answer_media(
        [],
        journal.state.get("answer_media") if resume else [],
    )
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
            memory_query=memory_query,
            task_instructions=task_instructions,
            project_root=project_root,
            working_dir=working_dir,
            model=model,
            agent_id=agent_id,
            conversation_history=conversation_history,
            run_id=rid,
            num_ctx=num_ctx,
            base_tools=initial_tools,
            auto_remember=auto_remember,
            chat_fn=chat_fn,
            chat_stream_fn=chat_stream_fn,
            compaction_audit_sink=audit_sink,
            profile_name=profile_name,
            permission_mode=permission_mode,
            thinking=thinking,
            reasoning_effort=selected_reasoning_effort,
            resume=resume,
            pause_for_workflow_request=pause_for_workflow_request,
            workflow_approval=workflow_approval,
            resource_refs=resource_refs,
            initial_sources=(
                list(journal.state.get("web_sources") or []) if resume
                else related_sources(session_id, list(source_run_ids or []), list(conversation_history or []))
            ),
        ):
            # Display/persistence only: keep canonical tool output and approval
            # digests inside the executor unchanged.
            event = sanitize_event(raw_event)
            event.setdefault("run_id", rid)
            if resume and event.get("type") == "run_started":
                continue
            if event.get("type") == "done":
                event.setdefault("resumable", bool(
                    event.get("partial")
                    or event.get("stop_reason")
                    in {"error", "context_limit", "workflow_request"}
                ))
                terminal = True
            if event.get("type") == "tool_call" and event.get("media"):
                answer_media = merge_answer_media(answer_media, event.get("media"))
            if event.get("type") == "final_response" and answer_media:
                event["media"] = list(answer_media)
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
    memory_query: str | None = None,
    project_root: Path | str,
    working_dir: Path | str | None = None,
    model: str = "auto",
    agent_id: str = "code-agent",
    conversation_history: list[dict[str, Any]] | None = None,
    run_id: str | None = None,
    num_ctx: int | None = None,
    base_tools: tuple[str, ...] | list[str] | None = None,
    auto_remember: bool = True,
    chat_fn: Callable[..., dict[str, Any]] | None = None,
    chat_stream_fn: Callable[..., Any] | None = None,
    profile_name: str = "Инженерный",
    permission_mode: str = "ask",
    thinking: bool = False,
    reasoning_effort: str | None = None,
    pause_for_workflow_request: bool = False,
    resume: bool = False,
    workflow_approval: dict[str, Any] | None = None,
    resource_refs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Synchronous single-shot wrapper around stream_code_agent. Drains
    the generator and aggregates the result into the legacy dict shape.
    Workflow input waits until a UI decision or Stop.
    """
    tool_calls_log: list[dict[str, Any]] = []
    response_text = ""
    response_media: list[dict[str, str]] = []
    response_citations: list[dict[str, Any]] = []
    response_sources: list[dict[str, Any]] = []
    source_status = "none"
    ok = False
    stop_reason = "error"
    error: str | None = None
    partial = False
    steps = 0
    # FIX-2: task-state must survive the sync path so API/automation can't see
    # only runtime `ok` without the task result.
    completion_status = "none"
    criteria: list[dict[str, Any]] = []
    criteria_confirmed = False
    task_spec: dict[str, Any] | None = None
    request_status = ""
    request: dict[str, Any] | None = None
    response_id = ""

    for event in stream_code_agent(
        user_message=user_message,
        memory_query=memory_query,
        project_root=project_root,
        working_dir=working_dir,
        model=model,
        agent_id=agent_id,
        conversation_history=conversation_history,
        run_id=run_id,
        num_ctx=num_ctx,
        base_tools=base_tools,
        auto_remember=auto_remember,
        chat_fn=chat_fn,
        chat_stream_fn=chat_stream_fn,
        profile_name=profile_name,
        permission_mode=permission_mode,
        thinking=thinking,
        reasoning_effort=reasoning_effort,
        pause_for_workflow_request=pause_for_workflow_request,
        resume=resume,
        workflow_approval=workflow_approval,
        resource_refs=resource_refs,
    ):
        et = event.get("type")
        if et == "tool_call":
            tool_calls_log.append({
                "step": event["step"],
                "tool": event["tool"],
                "arguments": event["arguments"],
                "result": event["result"],
                **{k: event[k] for k in (
                    "ok", "exit_code", "verifier", "evidence",
                    "touched_path", "old_content", "new_content", "diff_action",
                    "download_url", "download_name", "project_path", "size", "sha256",
                    "actual_url", "local_url", "actual_port", "port", "pid",
                    "server_started", "media", "sources", "skill",
                ) if k in event},
            })
        elif et == "final_response":
            response_text = event.get("text", "")
            response_media = list(event.get("media") or [])
            response_citations = list(event.get("citations") or [])
            response_sources = list(event.get("sources") or [])
            source_status = str(event.get("source_status") or "none")
        elif et == "workflow_request":
            request_status = str(event.get("status") or "")
            raw_request = event.get("request")
            request = dict(raw_request) if isinstance(raw_request, dict) else None
            response_id = str(event.get("response_id") or "")
        elif et == "done":
            ok = bool(event.get("ok"))
            partial = bool(event.get("partial"))
            stop_reason = str(event.get("stop_reason", "error"))
            error = event.get("error")
            steps = int(event.get("steps", 0))
            completion_status = str(event.get("completion_status") or "none")
            criteria = list(event.get("criteria") or [])
            criteria_confirmed = bool(event.get("criteria_confirmed"))
            task_spec = event.get("task_spec")
            request_status = str(event.get("status") or request_status)
            raw_request = event.get("request")
            if isinstance(raw_request, dict):
                request = dict(raw_request)
            response_id = str(event.get("response_id") or response_id)

    return {
        "ok": ok,
        "response": response_text,
        "media": response_media,
        "citations": response_citations,
        "sources": response_sources,
        "source_status": source_status,
        "steps": steps,
        "tool_calls": tool_calls_log,
        "stop_reason": stop_reason,
        "error": error,
        "partial": partial,
        # Task result — SEPARATE from runtime `ok`. Consumers must gate "solved"
        # on completion_status == "confirmed", never on ok alone.
        "completion_status": completion_status,
        "criteria": criteria,
        "criteria_confirmed": criteria_confirmed,
        "task_spec": task_spec,
        "status": request_status,
        "request": request,
        "response_id": response_id,
    }
