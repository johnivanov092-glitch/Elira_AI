"""Code-agent loop helpers — text/format, approval, context-window, RAG and
telemetry utilities used by the streaming loop.

Extracted verbatim from ``agent_loop.py`` (no behaviour change) to shrink that
module. This is a *leaf*: it imports nothing from ``agent_loop`` (only from
``.history`` for ``summarize_history``, which is itself a leaf), so re-exporting
these names back into ``agent_loop`` forms no import cycle. The core loop calls
every helper here through the ``agent_loop`` module namespace, so tests that
``patch`` these names on ``agent_loop`` (e.g. ``_approval_status``,
``_APPROVAL_POLL_INTERVAL``, ``_record_code_route_metric``,
``_try_remember_turn``) keep working unchanged.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Callable

from app.application.projects.scope import project_scope_id
from app.application.code_agent.history import summarize_history
from app.application.code_agent.tool_policy import CRITICAL_TOOLS, EDIT_ONLY_TOOLS

logger = logging.getLogger(__name__)


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


# Permission modes (selector in the composer, mirrored in Settings):
#   "ask"          — every require_approval tool pauses for the user (default).
#   "accept_edits" — auto-approve filesystem-only edits; still pause shell/net.
#   "bypass"       — auto-approve every require_approval tool, no prompts.
# Auto-approved under "accept_edits". Defined in tool_policy (single source of
# truth); imported here to preserve the old name.
_EDIT_ONLY_TOOLS = EDIT_ONLY_TOOLS


def _mode_auto_approves(permission_mode: str, tool_name: str) -> bool:
    """Whether the active permission mode pre-approves this tool without asking."""
    if permission_mode == "bypass":
        return True
    if permission_mode == "accept_edits":
        return tool_name in _EDIT_ONLY_TOOLS
    return False


# Tools that must ALWAYS be confirmed, even in bypass (from tool_policy; shell
# criticality is decided per-command below via is_shell_critical).
_CRITICAL_TOOLS: frozenset[str] = CRITICAL_TOOLS


def _is_critical_call(tool_name: str, args: dict[str, Any] | None) -> bool:
    """A specific call that must NEVER auto-approve — the user confirms it even in
    bypass mode. Covers destructive-but-legitimate shell commands (rm / git reset
    / drop / docker rm / kill / uninstall …) and any tool in _CRITICAL_TOOLS.
    Catastrophic commands are blocked outright elsewhere; this is 'ask, never
    auto'. Keeps bypass = 'no friction for normal work' while still guarding the
    handful of operations that destroy data."""
    if tool_name in _CRITICAL_TOOLS:
        return True
    if tool_name == "run_bash":
        try:
            from app.application.code_agent.tools import is_shell_critical
            return is_shell_critical(str((args or {}).get("command", "")))
        except Exception:
            return False
    return False


_REPEAT_REQUEST_MARKERS = (
    "еще раз", "ещё раз", "повтор", "снова", "заново", "repeat", "again", "same",
)


def _looks_like_repeat_request(text: str) -> bool:
    """True if the user explicitly asked to repeat / say it again, so an identical
    answer is legitimate and the anti-repeat gate must NOT fire."""
    t = (text or "").strip().lower()
    return any(m in t for m in _REPEAT_REQUEST_MARKERS)


def _norm_answer(text: str) -> str:
    """Normalise an assistant answer for exact-duplicate comparison: collapse all
    whitespace and strip. Deterministic — only answers that are byte-identical
    after normalisation match, so genuinely different replies never trip the
    anti-repeat gate (no fuzzy similarity, no false positives on real work)."""
    return " ".join((text or "").split()).strip()


def _mark_approval_approved(approval_id: str) -> bool:
    """Programmatically grant an approval row (for non-'ask' permission modes)."""
    try:
        from app.application.monitoring import runtime as _mon
        _mon.update_approval_status(approval_id, status="approved")
        return True
    except Exception:
        return False


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
            options={"num_ctx": int(num_ctx), "active_context_limit": int(num_ctx)},
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


def _try_remember_turn(*, user_message: str, response_text: str, project_root: Path) -> None:
    """Fire-and-forget: write a short summary of a successful agent turn
    to RAG so future `recall(query)` can surface it. Failures are logged
    but never raised.
    """
    if _is_throwaway_project(project_root):
        return
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
