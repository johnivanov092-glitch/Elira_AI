"""Code-agent loop helpers — text/format, Workflow requests, context-window, RAG and
telemetry utilities used by the streaming loop.

Extracted verbatim from ``agent_loop.py`` (no behaviour change) to shrink that
module. This is a *leaf*: it imports nothing from ``agent_loop`` (only from
``.history`` for ``summarize_history``, which is itself a leaf), so re-exporting
these names back into ``agent_loop`` forms no import cycle. The core loop calls
every helper here through the ``agent_loop`` module namespace, so tests that
``patch`` these names on ``agent_loop`` (e.g. request polling constants,
``_WORKFLOW_REQUEST_POLL_INTERVAL``, ``_record_code_route_metric``,
``_try_remember_turn``) keep working unchanged.
"""
from __future__ import annotations

import logging
import re
import threading
from pathlib import Path
from typing import Any, Callable, Iterable

from app.application.projects.scope import project_scope_id
from app.application.code_agent.history import summarize_history
from app.infrastructure.text import truncate_middle

logger = logging.getLogger(__name__)


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
    return truncate_middle(text, limit)


def _messages_char_count(messages: list[dict[str, Any]]) -> int:
    total = 0
    for item in messages:
        content = item.get("content")
        if isinstance(content, str):
            total += len(content)
    return total


def _short_arg_hint(args: dict[str, Any]) -> str:
    """Most identifying argument of a tool call, for the run's call log."""
    for key in ("path", "command", "pattern", "query"):
        val = args.get(key)
        if isinstance(val, str) and val:
            return val if len(val) <= 60 else val[:60] + "…"
    return ""


# A live Composer stream waits on a durable Workflow request and emits periodic
# SSE keepalives. Module-level values keep transport behaviour configurable.
_WORKFLOW_REQUEST_POLL_INTERVAL = 1.5
_WORKFLOW_REQUEST_KEEPALIVE_EVERY = 10.0


# Client-side <think> stripper (safety net). The reasoning/content split relies
# on llama-server's template parsing; if a model swap or llama.cpp update breaks
# it, raw think-blocks would leak into content and then into history. Handles an
# unterminated trailing <think> too (a cut-off generation).
_THINK_BLOCK_RE = re.compile(r"<think>.*?(?:</think>|\Z)", re.DOTALL | re.IGNORECASE)


def _strip_think_blocks(text: str) -> str:
    if "<think" not in (text or "").lower():
        return text or ""
    return _THINK_BLOCK_RE.sub("", text or "")


# --- Grounding across turns -------------------------------------------------
# conversation_history carries only user + assistant TEXT (tool results are
# dropped — see history._coerce_history). So facts the agent learned via tools
# in an earlier turn (which files exist, what a function is, a command's output)
# vanish, and a later factual follow-up gets answered from priors → confabulation
# (empirically: "add()" → invented "calc.py/multiply/test_calc.py"). We capture a
# compact digest of what the discovery tools revealed this run and hand it back
# next turn as an authoritative context block, so the model grounds instead of
# guessing. Read/inspect tools only — pure actions add no facts worth carrying.
_GROUNDING_FACT_TOOLS = frozenset({
    "project_map", "glob", "grep", "read_file", "run_bash", "run_server",
    "http_api", "browser", "recall", "write_file", "edit_file",
    # Raw search snippets and fetched page text are untrusted source material and
    # are intentionally not persisted as authoritative cross-turn facts.
    # Remote work grounds facts too — a remote read/check/write must survive into
    # the next turn's digest, not vanish because it happened over SSH.
    "ssh_run", "ssh_read", "ssh_write", "ssh_run_ps",
    # FIX-4: verifier verdicts are grounded facts AND real progress — a fresh
    # verdict (criterion transition) resets the stuck streak; a repeated identical
    # verdict collapses to the same fact-shape and does NOT count as progress.
    "ssh_assert_contains", "ssh_assert_not_contains", "ssh_port_check", "ssh_exists", "ssh_not_exists",
})
# Enumeration tools reveal the COMPLETE set of files/structure. Truncating their
# result to a short snippet was the residual grounding leak (live: the model had a
# partial file list and invented setup.py/requirements.txt/test_main.py on top).
# Carry their listing in full so "what files exist / list all files" is grounded
# authoritatively and the model stops padding the set with plausible inventions.
_ENUM_FACT_TOOLS = frozenset({"project_map", "glob"})
# Verifier tools whose FAILED verdict (verifier=True, ok=False) is STILL grounded
# knowledge — a criterion transitioning unconfirmed→failed is a useful state change
# and progress toward the next fix (rule 9). An ERROR-branch return (verifier
# absent, e.g. bad host) is NOT a verdict and still grounds nothing.
_VERIFIER_GROUNDING_TOOLS = frozenset({
    "ssh_assert_contains", "ssh_assert_not_contains", "ssh_port_check", "ssh_exists",
    "ssh_not_exists", "http_api", "browser",
})
# Fidelity of the cross-turn grounding digest. Raised (220→400 / 900→1500 /
# 3000→6000) now that the real window is 64k, not a tight small-model budget:
# more of each verified tool result survives into the next turn's [ПРОВЕРЕННЫЕ
# ФАКТЫ] block, so the "compress old but keep it accurate" side of grounding loses
# less. ~6000 chars ≈ 2000 tokens — negligible against 64k.
_FACT_SNIPPET_CHARS = 400
_ENUM_FACT_SNIPPET_CHARS = 1500
_FACTS_DIGEST_CHARS = 6000  # room for one full enumeration + several read facts
FACTS_PREFIX = "[ПРОВЕРЕННЫЕ ФАКТЫ]"


def _fact_from_tool(
    name: str, arg_hint: str, text_result: str, *, ok: bool = True, verifier: bool = False,
) -> str | None:
    """One grounded-fact line from a discovery tool's result, or None when the
    tool is not fact-bearing / failed / empty. A failed VERDICT from a verifier
    (verifier=True, ok=False) is still grounded — the unconfirmed→failed transition
    is progress for the next fix (rule 9) — but a failed read/run, or a verifier
    that couldn't RUN, grounds nothing. Enumeration tools carry a larger snippet."""
    if name not in _GROUNDING_FACT_TOOLS:
        return None
    if not ok and not (verifier and name in _VERIFIER_GROUNDING_TOOLS):
        return None
    cap = _ENUM_FACT_SNIPPET_CHARS if name in _ENUM_FACT_TOOLS else _FACT_SNIPPET_CHARS
    snippet = " ".join((text_result or "").split())[:cap]
    if not snippet:
        return None
    hint = (arg_hint or "").strip()
    return f"{name}({hint}): {snippet}" if hint else f"{name}: {snippet}"


def _facts_digest(facts: list[str]) -> str:
    """Order-preserving de-duplicated digest of this run's grounded facts,
    capped so it can never dominate the context window. Empty string if none."""
    seen: set[str] = set()
    lines: list[str] = []
    for f in facts:
        if not f or f in seen:
            continue
        seen.add(f)
        lines.append(f"- {f}")
    body = "\n".join(lines)
    return body[:_FACTS_DIGEST_CHARS]


# Verbatim "recent tool outputs" carried into the NEXT turn — the last few
# grounding-tool results in full-ish, not just the fact summary. Complements the
# facts digest: the summary covers ALL turns compactly; this gives the immediately-
# prior turn's raw output so a follow-up ("что там в файле про X?") reads the real
# text, not a 400-char snippet. Bounded so it never dominates the 64k window.
RECENT_TOOLS_PREFIX = "[РЕЗУЛЬТАТЫ ИНСТРУМЕНТОВ ПРОШЛОГО ХОДА]"
_RECENT_TOOL_ENTRY_CHARS = 2500   # per single tool output
_RECENT_TOOL_KEEP = 6             # last N grounding-tool results
_RECENT_TOOL_DIGEST_CHARS = 9000  # total cap (~3000 tokens)


def _recent_tool_snippet(name: str, arg_hint: str, text_result: str) -> str | None:
    """A fuller (still bounded) record of ONE grounding tool's output for the
    verbatim recent-outputs buffer. None when the tool isn't grounding-bearing."""
    if name not in _GROUNDING_FACT_TOOLS:
        return None
    text = (text_result or "").strip()
    if not text:
        return None
    if len(text) > _RECENT_TOOL_ENTRY_CHARS:
        text = text[:_RECENT_TOOL_ENTRY_CHARS] + " …[обрезано]"
    hint = (arg_hint or "").strip()
    head = f"{name}({hint})" if hint else name
    return f"### {head}\n{text}"


def _recent_tools_digest(entries: list[str]) -> str:
    """Join the last few recent-tool snippets (most-recent last), capped total."""
    if not entries:
        return ""
    body = "\n\n".join(entries[-_RECENT_TOOL_KEEP:])
    if len(body) > _RECENT_TOOL_DIGEST_CHARS:
        body = body[-_RECENT_TOOL_DIGEST_CHARS:]  # keep the most-recent tail
    return body


# ── Delivery-session ownership registry ─────────────────────────────────────
# Lives in this LEAF module (not in delivery_session) so the core agent loop
# can consult it without an import cycle: a session-level Stop that lands while
# NO slice is registered (between slices / during continuation build) must
# cancel the NEXT slice the moment it registers its run.
_SESSION_LOCK = threading.Lock()
_SESSION_CANCEL: dict[str, threading.Event] = {}


class DeliverySessionActiveError(RuntimeError):
    """A live delivery session already owns this run_id's cancel token."""


def request_session_cancel(run_id: str) -> bool:
    """Mark the delivery session for `run_id` cancelled. Returns True when a
    live session was found."""
    with _SESSION_LOCK:
        ev = _SESSION_CANCEL.get(run_id)
    if ev is None:
        return False
    ev.set()
    return True


def session_cancel_requested(run_id: str) -> bool:
    """True when a live delivery session for `run_id` has a PENDING cancel —
    consulted by the core loop right after _register_run so a Stop that landed
    in the inter-slice window cancels the new slice before any model call."""
    with _SESSION_LOCK:
        ev = _SESSION_CANCEL.get(run_id)
    return ev.is_set() if ev is not None else False


def session_active(run_id: str) -> bool:
    with _SESSION_LOCK:
        return run_id in _SESSION_CANCEL


def register_session(run_id: str) -> threading.Event:
    """Atomic ownership claim. A live session's cancel token is NEVER
    overwritten — a duplicate stream/resume of the same run_id raises
    DeliverySessionActiveError and the original stays the sole owner."""
    with _SESSION_LOCK:
        if run_id in _SESSION_CANCEL:
            raise DeliverySessionActiveError(run_id)
        ev = threading.Event()
        _SESSION_CANCEL[run_id] = ev
    return ev


def unregister_session(run_id: str, ev: threading.Event) -> None:
    """Release ownership — identity-guarded: only the session that registered
    `ev` may remove the entry, so a failed duplicate can never evict the
    original owner's token."""
    with _SESSION_LOCK:
        if _SESSION_CANCEL.get(run_id) is ev:
            _SESSION_CANCEL.pop(run_id, None)


def tool_state_changed(tool_name: str, tool_meta: dict, *, exec_ok: bool) -> bool:
    """Server-owned MUTATION signal for one executed tool call: True only when
    the execution succeeded, reported a real touched target, AND the ToolSpec
    registry declares the tool side-effectful. No hardcoded tool-name list —
    the registry's `side_effect` flag is the single seam, so read-only tools
    (read_file/ssh_read/glob/…) yield False by their own spec, blocked/error
    calls fail the exec_ok gate, and a no-op (e.g. ssh_replace without a match)
    reports no touched_path. Unknown/unregistered specs default to False —
    conservative: never counts as delivery progress."""
    if not exec_ok:
        return False
    meta = tool_meta or {}
    provider_confirmed_change = meta.get("state_changed") is True
    if not provider_confirmed_change and not str(meta.get("touched_path") or "").strip():
        return False
    # Proven NO-OP: when the tool itself reports the before/after content
    # (write_file/edit_file), identical bytes mean nothing changed — an
    # "overwrite with the same content" is not a mutation.
    old, new = meta.get("old_content"), meta.get("new_content")
    if isinstance(old, str) and isinstance(new, str) and old == new:
        return False
    try:
        from app.application.tool_registry.runtime import get_tool

        spec = get_tool(tool_name) or {}
    except Exception:
        logger.warning("tool_state_changed: spec lookup failed for %s", tool_name, exc_info=True)
        return False
    return bool(spec.get("side_effect"))


def build_task_state_block(
    *,
    goal: str = "",
    constraints: list[str] | None = None,
    criteria_rows: list[dict] | None = None,
    checklist_items: list[dict] | None = None,
    mutated_files: list[str] | None = None,
    verifications: list[str] | None = None,
    failed_attempts: list[str] | None = None,
    next_step: str = "",
) -> str:
    """Build a bounded digest exclusively from typed runtime state."""
    lines: list[str] = []
    if goal.strip():
        lines.append(f"Задача: {goal.strip()[:400]}")
    if next_step.strip():
        lines.append(f"Следующий шаг: {next_step.strip()[:300]}")
    for constraint in (constraints or [])[:8]:
        lines.append(f"Ограничение: {str(constraint).strip()[:200]}")
    rows = criteria_rows or []
    if rows:
        confirmed = sum(1 for row in rows if row.get("status") == "confirmed")
        lines.append(f"Критерии ({confirmed}/{len(rows)} подтверждено):")
        for row in rows[:18]:
            mark = {"confirmed": "✓", "failed": "✗"}.get(
                str(row.get("status")),
                "·",
            )
            evidence = str(row.get("evidence") or "").strip().replace("\n", " ")[:120]
            line = f"  {mark} {str(row.get('text') or '')[:160]}"
            if evidence:
                line += f" [{row.get('verifier')}: {evidence}]"
            lines.append(line)
    items = checklist_items or []
    if items:
        completed = sum(1 for item in items if item.get("status") == "completed")
        lines.append(f"Чеклист ({completed}/{len(items)}):")
        lines.append(format_checklist_state(items, max_items=20))
    mutated = list(dict.fromkeys(mutated_files or []))
    if mutated:
        shown = ", ".join(mutated[:20])
        more = f" (+{len(mutated) - 20})" if len(mutated) > 20 else ""
        lines.append(f"Изменённые файлы ({len(mutated)}): {shown}{more}")
    for verification in (verifications or [])[-10:]:
        lines.append(f"Проверка: {str(verification)[:180]}")
    for failure in (failed_attempts or [])[-6:]:
        lines.append(f"Неудачная попытка: {str(failure)[:160]}")
    return "\n".join(lines)[:6000]


def upsert_task_state_message(
    messages: list[dict[str, Any]],
    block_text: str,
) -> list[dict[str, Any]]:
    """Replace or insert the one compaction-protected task-state message."""
    from app.application.context.compaction import (
        TASK_STATE_MARKER_KEY,
        TASK_STATE_MARKER_VALUE,
        TASK_STATE_PREFIX,
    )

    if not block_text.strip():
        return messages
    out = [
        message
        for message in messages
        if message.get(TASK_STATE_MARKER_KEY) != TASK_STATE_MARKER_VALUE
    ]
    insert_at = 1 if out and out[0].get("role") == "system" else 0
    out.insert(
        insert_at,
        {
            "role": "assistant",
            "content": TASK_STATE_PREFIX + block_text,
            TASK_STATE_MARKER_KEY: TASK_STATE_MARKER_VALUE,
        },
    )
    return out


def format_checklist_state(items: list[dict], *, max_items: int = 30) -> str:
    """Deterministic one-line-per-item digest of the durable run checklist,
    built from the task_planner rows (server truth, not model prose)."""
    lines: list[str] = []
    for item in items[:max_items]:
        status = str(item.get("status") or "pending")
        mark = "x" if status == "completed" else ("~" if status == "in_progress" else " ")
        lines.append(f"- [{mark}] ({item.get('id')}) [{status}] {item.get('text')}")
    if len(items) > max_items:
        lines.append(f"- … ещё {len(items) - max_items} пунктов")
    return "\n".join(lines)


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
    thresholds = context_profile.get("compaction_thresholds") or {}
    compact_threshold = float(
        (thresholds.get("auto") or {}).get("percent")
        or (60.0 if num_ctx < 16_384 else 75.0)
    )
    strong_threshold = float(
        (thresholds.get("strong") or {}).get("percent")
        or (80.0 if num_ctx < 16_384 else 90.0)
    )
    critical_threshold = float(
        (thresholds.get("critical") or {}).get("percent") or 95.0
    )
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
    if float(usage["percent"]) >= critical_threshold or (
        safe_input_budget > 0 and int(usage["current_tokens"]) > safe_input_budget
    ):
        raise ContextBudgetError(
            "Контекст остаётся критически заполненным после сжатия: "
            f"{usage['current_tokens']} входных токенов, окно {usage['ctx_size']}. "
            "Начните новый чат или оставьте только необходимые материалы."
        )
    return messages, compacted, usage


def _is_throwaway_project(project_root: Path) -> bool:
    """True when the project lives under the OS temp dir — a disposable
    sandbox from a smoke/experimental run, not a real user project.

    We must not persist agent_turn memories for these: they flood RAG with
    noise like ``[agent_turn project=tmpXXXX] task: hello | outcome: Hello
    world`` that never matches a real future query but still dilutes recall.
    (pytest is already isolated via ELIRA_DATA_DIR; this guards the *real*
    canonical DB against manual/smoke runs over temp dirs.)
    """
    try:
        import tempfile

        root = project_root.expanduser().resolve()
        tmp = Path(tempfile.gettempdir()).resolve()
        return root == tmp or tmp in root.parents
    except Exception:
        return False


def _try_remember_turn(
    *,
    user_message: str,
    response_text: str,
    project_root: Path,
    verified: bool = False,
    mutation_targets: Iterable[str] = (),
    verification_targets: Iterable[str] = (),
) -> None:
    """Persist only a verified project change, never free-form model prose.

    ``response_text`` stays in the signature for call-site compatibility but is
    intentionally not stored: a fluent final answer is not evidence.
    """
    if _is_throwaway_project(project_root):
        return
    changed = sorted({
        str(target or "").strip()[:240]
        for target in mutation_targets
        if str(target or "").strip()
    })
    if not verified or not changed:
        return
    try:
        from app.application.rag_memory.service import add_to_rag
    except Exception:
        return
    user = (user_message or "").strip()
    if not user:
        return
    if len(user) > 300:
        user = user[:300] + " [...]"
    checks = sorted({
        str(target or "").strip()[:240]
        for target in verification_targets
        if str(target or "").strip()
    })
    project_name = project_root.name or str(project_root)
    scope_id = project_scope_id(project_root)
    summary = (
        f"[verified_turn project={project_name}] task: {user} | "
        f"changed: {', '.join(changed[:20])} | "
        f"verified: {', '.join(checks[:10]) or 'current project epoch passed'}"
    )
    try:
        add_to_rag(text=summary, category="verified_turn", importance=4, project=scope_id)
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


# ask_user is handled INLINE by the loop — it pauses the run
# and waits for a human answer, so it is never dispatched via the executor. The
# schema is appended to every step so the model can always reach it.
_ASK_USER_SCHEMA = {
    "type": "function",
    "function": {
        "name": "ask_user",
        "description": (
            "Ask the user ONE short clarifying question and wait for the answer, "
            "then continue the SAME run. Use only when the task is genuinely "
            "ambiguous (which host / file / option among several). Provide "
            "`options` (a list of choices) when the answer is one of a few known "
            "values — the UI renders them as buttons. Do NOT ask for anything you "
            "can find yourself with read_file/glob/grep/config; ask sparingly."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "The question to ask the user."},
                "options": {
                    "type": "array", "items": {"type": "string"},
                    "description": "Optional list of concrete answer choices.",
                },
            },
            "required": ["question"],
        },
    },
}


_WORKFLOW_REQUEST_SCHEMA = {
    "type": "function",
    "function": {
        "name": "workflow_request",
        "description": (
            "Pause the current Workflow agent step and request one explicit value "
            "from the Workflow UI. Use `input` for ordinary user data, `secret` "
            "when the UI must store a credential and return only its secret_ref, "
            "or `elevation` when Windows must run one command through UAC. This is "
            "not an authorization policy: after the UI resolves the request the "
            "same Workflow step resumes with the returned values."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": ["input", "secret", "elevation"],
                },
                "message": {
                    "type": "string",
                    "description": "Short message shown in the Workflow request card.",
                },
                "schema": {
                    "type": "object",
                    "description": (
                        "JSON Schema for input values. For elevation this field is "
                        "ignored and the native command fields are used."
                    ),
                },
                "program": {
                    "type": "string",
                    "description": "Executable passed to the native UAC bridge.",
                },
                "args": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Argument vector passed to the elevated process.",
                },
                "cwd": {
                    "type": "string",
                    "description": "Optional working directory for the elevated process.",
                },
            },
            "required": ["kind", "message"],
        },
    },
}


def _schema_tool_name(schema: dict) -> str:
    return str((schema.get("function") or {}).get("name") or "")
