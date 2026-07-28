"""Bounded delivery session — orchestration OVER the journalled agent stream.

One user submission may become up to ``MAX_SLICES`` internal bounded slices of
the SAME run (same run_id / project_root / permission_mode / TaskSpec / durable
checklist). This is deliberately NOT a second runtime: every slice is a plain
``stream_code_agent`` invocation (slice 2+ with ``resume=True``), so the journal,
guards, budgets, approval policy and TaskSpec verifier gates all apply per slice
exactly as they do today.

Auto-continuation fires ONLY when all of the following hold:
  - the slice stopped on a runtime budget: ``timeout`` / ``context_limit`` /
    ``max_steps`` (never after no_progress, loop_guard, error, cancelled,
    rejected approvals or a clean answer);
  - the task is still not verifier-confirmed (partial/unverified);
  - the slice produced PROVEN structural progress — a new ``touched_path`` from
    an executed tool call, a criterion flipped to confirmed by a verifier, or a
    durable checklist item flipped to ``completed`` — signals the model's text
    cannot forge;
  - the automatic-continuation budget is not exhausted
    (``MAX_AUTO_CONTINUATIONS`` after the first slice);
  - the user has not cancelled the session.

The client sees exactly ONE terminal ``done``. Slice boundaries surface as the
informational ``delivery_continuing`` event (unknown event types are silently
ignored by the frontend stream consumer, so this is compatible by construction).

Simple tasks (no structured TaskSpec) and one-shot IT-Ops diagnostic runs
(``itops-diag-`` prefix) bypass the session entirely: one ordinary run, exactly
as before this module existed.
"""
from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any, Callable, Iterator

from app.application.code_agent.agent_loop import (
    DEFAULT_MAX_STEPS,
    _CODE_AGENT_BASE_TOOLS,
    stream_code_agent,
)
from app.application.code_agent.loop_helpers import (
    FACTS_PREFIX,
    DeliverySessionActiveError,
    format_checklist_state,
    register_session as _register_session,
    request_session_cancel,
    session_active as _session_active,
    unregister_session as _unregister_session,
)
from app.application.code_agent.run_journal import RunJournal
from app.application.code_agent.taskspec import derive_task_spec

logger = logging.getLogger(__name__)

# One user submission = at most 1 + MAX_AUTO_CONTINUATIONS bounded slices.
MAX_AUTO_CONTINUATIONS = 3
MAX_SLICES = 1 + MAX_AUTO_CONTINUATIONS
# The ONLY stop reasons that may auto-continue: runtime budget exhaustion. Every
# behavioural stop (no_progress, loop_guard), error, cancel and clean answer is
# terminal for the session — the existing guards already said "stop".
AUTO_CONTINUE_STOP_REASONS = frozenset({"timeout", "context_limit", "max_steps"})

# Server-owned delivery contract for structural tasks. PREPENDED to the USER
# message (never the system prompt — the base prompt is at token capacity, and
# never appended: trailing text after a «Критерии готовности:» section would
# parse as an extra criterion). Injected only when the task derived a TaskSpec,
# so simple tasks and the prompt canaries pay zero tokens. Deliberately
# TASK-NEUTRAL: the checklist steps must be derived from the actual task — a
# structured bugfix must not inherit scaffold/packaging/deploy milestones.
# TaskSpec-invariance is pinned by test_contract_note_does_not_change_taskspec;
# neutrality by test_contract_note_is_task_neutral.
DELIVERY_CONTRACT_NOTE = (
    "[delivery-контракт] Это структурная задача с критериями готовности. "
    "Сразу составь durable-чеклист через todo_update из шагов, выведенных из "
    "САМОЙ задачи: сначала обследуй фактическое состояние проекта, затем "
    "перечисли только реально необходимые шаги и проверку результата. Не "
    "добавляй нерелевантные задаче этапы; деплой планируй только если "
    "пользователь явно его запросил. Отмечай пункт completed только после "
    "фактической проверки и держи чеклист актуальным.\n\n"
)

_CONTINUATION_MESSAGE = (
    "Продолжи незавершённую задачу с последнего подтверждённого результата. "
    "Сначала проверь фактическое состояние файлов и чеклиста (todo_update без "
    "аргументов покажет его), не повторяй уже выполненные изменения и не "
    "пересоздавай план."
)

# Session-level cancel/ownership registry: lives in loop_helpers (leaf) so the
# core loop consults it too (a Stop landing between slices cancels the next
# slice right after _register_run). Re-exported here — routes and tests keep
# importing request_session_cancel / DeliverySessionActiveError from this
# module. request_cancel() (agent_loop) covers a LIVE slice; the session token
# covers the gaps and makes a user Stop terminal for the whole session.


def _duplicate_session_done(run_id: str) -> dict[str, Any]:
    """Stable refusal for ANY second entry (stream or resume, delivery-shaped
    or not) on a run_id whose delivery session is live."""
    return {
        "type": "done",
        "run_id": run_id,
        "ok": False,
        "steps": 0,
        "stop_reason": "error",
        "error": "delivery_session_already_active",
        "resumable": False,
    }


def _is_diag_run(run_id: str) -> bool:
    try:
        from app.application.agent_kernel.operation_scope import DIAG_RUN_PREFIX

        return run_id.startswith(DIAG_RUN_PREFIX)
    except Exception:
        return run_id.startswith("itops-diag-")


def _delivery_shaped(user_message: str, project_root: Any) -> bool:
    """Structural project task ⇔ the existing 0-token TaskSpec heuristic fires.
    No new classifier: simple/conversational tasks stay single-slice."""
    try:
        root = Path(str(project_root))
        return derive_task_spec(user_message, project_root=root if root.is_dir() else None) is not None
    except Exception:
        return False


def _checklist_items(run_id: str) -> list[dict]:
    try:
        from app.application.task_planner.service import list_checklist

        return list((list_checklist(run_id) or {}).get("items") or [])
    except Exception:
        logger.warning("delivery: checklist read failed for %s", run_id, exc_info=True)
        return []


def _checklist_completed_count(run_id: str) -> int:
    return sum(1 for it in _checklist_items(run_id) if str(it.get("status")) == "completed")


def _confirmed_count(criteria: Any) -> int:
    if not isinstance(criteria, list):
        return 0
    return sum(1 for row in criteria if isinstance(row, dict) and row.get("status") == "confirmed")


def _first_open_checklist_item(items: list[dict]) -> dict | None:
    for it in items:
        if str(it.get("status")) in ("pending", "in_progress"):
            return it
    return None


def _next_milestone(run_id: str, done_event: dict) -> str | None:
    """Deterministic "what comes next": first open checklist item, else first
    unconfirmed criterion. Server truth only — never model prose."""
    open_item = _first_open_checklist_item(_checklist_items(run_id))
    if open_item is not None:
        return str(open_item.get("text") or "") or None
    for row in done_event.get("criteria") or []:
        if isinstance(row, dict) and row.get("status") != "confirmed":
            text = str(row.get("text") or "").strip()
            if text:
                return text
    return None


def _resume_facts_block(run_id: str, state: dict) -> str:
    """Server-owned resume context: what is done / what remains / what is
    verified — from the journal + durable checklist, never from model text.
    Prefixed with FACTS_PREFIX so _coerce_history re-tags it into a SYSTEM
    message with authoritative framing on the next slice."""
    lines = [
        FACTS_PREFIX,
        "Продолжение того же прогона. Состояние по данным сервера (журнал + чеклист):",
    ]
    changed = [str(p) for p in (state.get("changed_files") or []) if str(p).strip()]
    if changed:
        shown = ", ".join(changed[:30])
        more = f" (+{len(changed) - 30})" if len(changed) > 30 else ""
        lines.append(f"— Затронутые файлы ({len(changed)}): {shown}{more}")
    else:
        lines.append("— Файлы ещё не менялись.")
    items = _checklist_items(run_id)
    if items:
        done = sum(1 for it in items if str(it.get("status")) == "completed")
        lines.append(f"— Чеклист ({done}/{len(items)} выполнено):")
        lines.append(format_checklist_state(items))
        open_item = _first_open_checklist_item(items)
        if open_item is not None:
            lines.append(f"— Первый открытый пункт: «{open_item.get('text')}» (id={open_item.get('id')}).")
    criteria = state.get("criteria") or []
    if isinstance(criteria, list) and criteria:
        confirmed = _confirmed_count(criteria)
        lines.append(f"— Критерии задачи: подтверждено verifier'ом {confirmed}/{len(criteria)}.")
    lines.append(
        "Работай от этого состояния: продолжай первый открытый пункт, не повторяй "
        "уже выполненное, план не пересоздавай (меняй статусы через updates)."
    )
    return "\n".join(lines)


def build_continuation_kwargs(
    run_id: str,
    *,
    approval_wait_seconds: int = 300,
    chat_fn: Callable[..., dict[str, Any]] | None = None,
    chat_stream_fn: Callable[..., Any] | None = None,
    thinking_override: bool | None = None,
) -> dict[str, Any]:
    """Continuation slice kwargs for `run_id` from the persisted journal: the
    ORIGINAL request identity (project_root, permission_mode, TaskSpec source
    message, budgets) + rebuilt history enriched with the server-owned resume
    context (checklist completed/open items, touched files, criteria state)."""
    journal = RunJournal.load(run_id)
    state = journal.state
    req = state.get("request") or {}
    if not isinstance(req, dict):
        raise ValueError(f"run request is invalid: {run_id}")

    history = list(req.get("conversation_history") or [])
    original_message = str(req.get("user_message") or "").strip()
    if original_message:
        history.append({"role": "user", "content": original_message})
    last_response = str(state.get("last_response") or "").strip()
    if last_response:
        history.append({"role": "assistant", "content": last_response})
    history.append({"role": "assistant", "content": _resume_facts_block(run_id, state)})

    user_message = _CONTINUATION_MESSAGE
    open_item = _first_open_checklist_item(_checklist_items(run_id))
    if open_item is not None:
        user_message += f" Следующий пункт чеклиста: «{open_item.get('text')}»."

    thinking = bool(req.get("thinking", False))
    if thinking_override is not None:
        thinking = thinking_override
    if state.get("thinking_fallback_applied"):
        # Durable one-shot: once this run fell back to thinking-OFF, no later
        # slice — automatic OR manual Resume — re-enables it. The journalled
        # request and the user's global toggle stay untouched.
        thinking = False
    return {
        "user_message": user_message,
        "project_root": str(req.get("project_root") or ""),
        "working_dir": req.get("working_dir"),
        "model": str(req.get("model") or "auto"),
        "agent_id": str(req.get("agent_id") or "code-agent"),
        "max_steps": int(req.get("max_steps") or DEFAULT_MAX_STEPS),
        "conversation_history": history,
        "run_id": run_id,
        "num_ctx": req.get("num_ctx") or None,
        "base_tools": tuple(req.get("base_tools") or _CODE_AGENT_BASE_TOOLS),
        "execution_timeout_seconds": req.get("execution_timeout_seconds"),
        "auto_remember": bool(req.get("auto_remember", True)),
        "chat_fn": chat_fn,
        "chat_stream_fn": chat_stream_fn,
        "approval_wait_seconds": approval_wait_seconds,
        "resume": True,
        "access_mode": str(req.get("access_mode") or "project-workspace"),
        "profile_name": str(req.get("profile_name") or "Инженерный"),
        "permission_mode": str(req.get("permission_mode") or "ask"),
        "thinking": thinking,
        "no_questions": bool(req.get("no_questions", False)),
    }


def stream_delivery_session(
    *,
    user_message: str,
    project_root: Path | str,
    working_dir: Path | str | None = None,
    model: str = "auto",
    agent_id: str = "code-agent",
    max_steps: int = DEFAULT_MAX_STEPS,
    conversation_history: list[dict[str, Any]] | None = None,
    run_id: str | None = None,
    num_ctx: int | None = None,
    base_tools: tuple[str, ...] | list[str] | None = None,
    execution_timeout_seconds: int | None = None,
    auto_remember: bool = True,
    chat_fn: Callable[..., dict[str, Any]] | None = None,
    chat_stream_fn: Callable[..., Any] | None = None,
    approval_wait_seconds: int = 300,
    access_mode: str = "project-workspace",
    profile_name: str = "Инженерный",
    permission_mode: str = "ask",
    thinking: bool = False,
    no_questions: bool = False,
    max_auto_continuations: int = MAX_AUTO_CONTINUATIONS,
) -> Iterator[dict[str, Any]]:
    """Public stream for a NEW user submission (`POST /api/code-agent/stream`)."""
    rid = run_id or uuid.uuid4().hex
    if _session_active(rid):
        # Ownership covers EVERY entry: while a delivery session is live, a
        # second /stream on the same run_id (delivery-shaped or not) is refused
        # before any stream_code_agent is started.
        yield _duplicate_session_done(rid)
        return
    first_kwargs: dict[str, Any] = {
        "user_message": user_message,
        "project_root": project_root,
        "working_dir": working_dir,
        "model": model,
        "agent_id": agent_id,
        "max_steps": max_steps,
        "conversation_history": conversation_history,
        "run_id": rid,
        "num_ctx": num_ctx,
        "base_tools": base_tools,
        "execution_timeout_seconds": execution_timeout_seconds,
        "auto_remember": auto_remember,
        "chat_fn": chat_fn,
        "chat_stream_fn": chat_stream_fn,
        "approval_wait_seconds": approval_wait_seconds,
        "access_mode": access_mode,
        "profile_name": profile_name,
        "permission_mode": permission_mode,
        "thinking": thinking,
        "no_questions": no_questions,
    }
    shaped = not _is_diag_run(rid) and _delivery_shaped(user_message, project_root)
    if not shaped:
        # Simple task / diagnostic one-shot: one ordinary run, byte-for-byte
        # today's behaviour (no session registry, no extra events).
        yield from stream_code_agent(**first_kwargs)
        return
    first_kwargs["user_message"] = DELIVERY_CONTRACT_NOTE + user_message
    yield from _run_session(
        rid,
        first_kwargs,
        approval_wait_seconds=approval_wait_seconds,
        chat_fn=chat_fn,
        chat_stream_fn=chat_stream_fn,
        max_auto_continuations=max_auto_continuations,
        seen_touched=set(),
        prev_confirmed=0,
    )


def stream_resume_session(
    run_id: str,
    *,
    approval_wait_seconds: int = 300,
    chat_fn: Callable[..., dict[str, Any]] | None = None,
    chat_stream_fn: Callable[..., Any] | None = None,
    max_auto_continuations: int = MAX_AUTO_CONTINUATIONS,
) -> Iterator[dict[str, Any]]:
    """Public stream for the manual Resume button — one user action, so it gets
    the same enriched continuation context and the same bounded session."""
    if _session_active(run_id):
        yield _duplicate_session_done(run_id)
        return
    kwargs = build_continuation_kwargs(
        run_id,
        approval_wait_seconds=approval_wait_seconds,
        chat_fn=chat_fn,
        chat_stream_fn=chat_stream_fn,
    )
    state = RunJournal.load(run_id).state
    req = state.get("request") or {}
    shaped = not _is_diag_run(run_id) and _delivery_shaped(
        str(req.get("user_message") or ""), str(req.get("project_root") or "")
    )
    if not shaped:
        yield from stream_code_agent(**kwargs)
        return
    yield from _run_session(
        run_id,
        kwargs,
        approval_wait_seconds=approval_wait_seconds,
        chat_fn=chat_fn,
        chat_stream_fn=chat_stream_fn,
        max_auto_continuations=max_auto_continuations,
        seen_touched={str(p) for p in (state.get("changed_files") or []) if str(p).strip()},
        prev_confirmed=_confirmed_count(state.get("criteria")),
    )


def _run_session(
    rid: str,
    first_kwargs: dict[str, Any],
    *,
    approval_wait_seconds: int,
    chat_fn: Callable[..., dict[str, Any]] | None,
    chat_stream_fn: Callable[..., Any] | None,
    max_auto_continuations: int,
    seen_touched: set[str],
    prev_confirmed: int,
) -> Iterator[dict[str, Any]]:
    try:
        cancel_ev = _register_session(rid)
    except DeliverySessionActiveError:
        # A live session already owns this run_id — refuse the duplicate with a
        # stable error and WITHOUT touching the original's cancel token.
        yield _duplicate_session_done(rid)
        return
    auto_used = 0
    slice_no = 1
    thinking_override: bool | None = None
    prev_checklist_done = _checklist_completed_count(rid)
    current_kwargs = first_kwargs
    try:
        while True:
            done_event: dict[str, Any] | None = None
            slice_new_touched: list[str] = []
            slice_state_changes = 0
            fallback_seen = False
            try:
                for ev in stream_code_agent(**current_kwargs):
                    et = ev.get("type")
                    if et == "tool_call":
                        # Proven progress = real MUTATIONS this slice (runtime
                        # state_changed: ToolSpec.side_effect + successful
                        # execution + real change). Counted PER SLICE, not per
                        # novel path — re-editing a file touched earlier is
                        # progress too. new_touched_paths stays as telemetry.
                        if ev.get("state_changed"):
                            slice_state_changes += 1
                        touched = str(ev.get("touched_path") or "").strip()
                        if touched and touched not in seen_touched:
                            seen_touched.add(touched)
                            slice_new_touched.append(touched)
                    elif et == "reasoning_fallback":
                        fallback_seen = True
                    elif et == "done":
                        # Intercept: the session decides whether this terminal
                        # reaches the client or becomes a slice boundary.
                        done_event = dict(ev)
                        continue
                    yield ev
            except Exception as exc:  # noqa: BLE001 — journal/lock failures land here
                logger.exception("delivery slice %d failed for %s", slice_no, rid)
                done_event = {
                    "type": "done",
                    "run_id": rid,
                    "ok": False,
                    "steps": 0,
                    "stop_reason": "error",
                    "error": str(exc),
                    "partial": True,
                    "resumable": True,
                }
            if done_event is None:
                done_event = {
                    "type": "done",
                    "run_id": rid,
                    "ok": False,
                    "steps": 0,
                    "stop_reason": "error",
                    "error": "agent stream ended without a terminal event",
                    "partial": True,
                    "resumable": True,
                }

            if fallback_seen:
                # Delivery (B): the thinking-OFF fallback is one-shot for the
                # whole session — later slices must not re-enter thinking.
                thinking_override = False

            checklist_done_now = _checklist_completed_count(rid)
            confirmed_now = _confirmed_count(done_event.get("criteria"))
            progress = {
                "state_changes": slice_state_changes,
                "criteria_confirmed_delta": max(0, confirmed_now - prev_confirmed),
                "checklist_completed_delta": max(0, checklist_done_now - prev_checklist_done),
                # telemetry only — novel paths are NOT a continuation licence
                "new_touched_paths": len(slice_new_touched),
            }
            proven_progress = (
                progress["state_changes"] > 0
                or progress["criteria_confirmed_delta"] > 0
                or progress["checklist_completed_delta"] > 0
            )
            stop = str(done_event.get("stop_reason") or "")
            not_confirmed = str(done_event.get("completion_status") or "none") != "confirmed"
            # An `answer` terminal is runtime-healthy but NOT solved when the
            # criteria are unverified (done.ok stays runtime health — untouched).
            # The real Mini CRM run ended exactly here: real mutations, an open
            # durable checklist, 0/18 confirmed — and the session stopped. Such
            # a slice earns the SAME bounded continuation toward verification;
            # without progress or without an open checklist it still stops
            # honestly (no answer→answer loop; the hard cap is shared).
            answer_eligible = (
                stop == "answer"
                and bool(done_event.get("resumable"))
                and _first_open_checklist_item(_checklist_items(rid)) is not None
            )
            should_continue = (
                (stop in AUTO_CONTINUE_STOP_REASONS or answer_eligible)
                and not_confirmed
                and bool(done_event.get("partial"))
                and proven_progress
                and auto_used < max_auto_continuations
                and not cancel_ev.is_set()
            )
            if not should_continue:
                final = done_event
                final.setdefault("run_id", rid)
                if auto_used:
                    final["auto_continuations"] = auto_used
                if final.get("partial") and final.get("resumable"):
                    milestone = _next_milestone(rid, final)
                    if milestone:
                        final["next_milestone"] = milestone
                yield final
                return

            auto_used += 1
            prev_confirmed = confirmed_now
            prev_checklist_done = checklist_done_now
            boundary = {
                "type": "delivery_continuing",
                "run_id": rid,
                "slice": slice_no,
                "next_slice": slice_no + 1,
                "auto_continuation": auto_used,
                "max_auto_continuations": max_auto_continuations,
                "stop_reason": done_event.get("stop_reason"),
                "progress": progress,
            }
            try:
                # Append-only observer write: events.jsonl gains the boundary
                # record, state.json is NOT rewritten — the session holds no run
                # lock here and must not race a concurrent manual resume.
                RunJournal.load(rid).append_external_event(boundary)
            except Exception:
                logger.warning("delivery: failed to journal slice boundary for %s", rid, exc_info=True)
            yield boundary
            if cancel_ev.is_set():
                # Stop landed in the inter-slice gap (after delivery_continuing,
                # before the next slice): no live loop saw the cancel registry,
                # so the session terminates HERE — the next slice never calls a
                # model or a tool. Journal and SSE must agree: PERSIST first
                # (per-run lock only — a foreign run's agent.lock cannot block
                # it), then emit. If persistence still fails, emit the slice's
                # already-durable terminal instead of an unrecorded cancel.
                final = dict(done_event)
                final.update({
                    "type": "done",
                    "run_id": rid,
                    "ok": False,
                    "stop_reason": "cancelled",
                    "error": "Cancelled by user",
                    "partial": True,
                    "resumable": True,
                    "auto_continuations": auto_used,
                })
                try:
                    RunJournal.load(rid).record_terminal_event(final)
                except Exception:
                    logger.exception("delivery: failed to journal gap-cancel for %s", rid)
                    fallback = dict(done_event)
                    fallback.setdefault("run_id", rid)
                    fallback["auto_continuations"] = auto_used
                    yield fallback
                    return
                yield final
                return
            slice_no += 1
            try:
                current_kwargs = build_continuation_kwargs(
                    rid,
                    approval_wait_seconds=approval_wait_seconds,
                    chat_fn=chat_fn,
                    chat_stream_fn=chat_stream_fn,
                    thinking_override=thinking_override,
                )
            except Exception as exc:  # journal unreadable — honest terminal
                logger.exception("delivery: continuation build failed for %s", rid)
                final = dict(done_event)
                final.setdefault("run_id", rid)
                final["auto_continuations"] = auto_used
                final["error"] = f"continuation failed: {exc}"
                final["resumable"] = True
                yield final
                return
    finally:
        _unregister_session(rid, cancel_ev)
