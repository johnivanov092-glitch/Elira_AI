"""Context compaction — keep the agent loop inside its token budget.

Trigger:
  When the approximate token count of the current message list exceeds
  `threshold` × `num_ctx` (default 70%), compaction runs.

  Approximation: tokens ≈ len(json.dumps(messages)) / 4.  This is a rough
  estimate that intentionally over-counts so we compact a little before
  exhaustion rather than at the last moment.

Compaction strategy:
  1. Split messages into system messages, messages-to-summarise, and the
     most-recent `keep_pairs` user/assistant exchange pairs (kept verbatim).
  2. Call `summarize_fn` on the older messages to get a rolling summary.
  3. Rebuild: [system messages] + [summary turn] + [recent pairs].

Deterministic fallback (when model is unavailable or summarise fails):
  Replace older messages with a single placeholder turn and keep the last
  `fallback_keep` non-system messages verbatim.
"""
from __future__ import annotations

import json
import logging
import hashlib
import time
import uuid
from typing import Any, Callable

logger = logging.getLogger(__name__)

# Tokens below the threshold → no compaction.
_DEFAULT_THRESHOLD: float = 0.70
# User/assistant exchange pairs to keep verbatim after compaction.
_DEFAULT_KEEP_PAIRS: int = 4
# Messages to keep in deterministic fallback (no summarise).
_DEFAULT_FALLBACK_KEEP: int = 8
# Placeholder inserted in fallback mode.
_FALLBACK_PLACEHOLDER = "[context compacted — earlier messages removed to fit context window]"
# Prefix added to the generated summary turn.
_SUMMARY_PREFIX = "[Compacted context summary]\n"
_MAX_SUMMARY_CHARS = 4_000
_MESSAGE_EXCERPT_CHARS = 300

SummarizeFn = Callable[..., dict[str, Any]]


def _approx_tokens(messages: list[dict[str, Any]]) -> int:
    """Rough token estimate: chars / 4."""
    return len(json.dumps(messages, ensure_ascii=False)) // 4


def _summary_hash(messages: list[dict[str, Any]]) -> str:
    summaries = [_summary_body(message) for message in messages if _is_summary_message(message)]
    raw = "\n\n".join(summaries).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _emit_audit(
    sink: Callable[[dict[str, Any]], None] | None,
    before: list[dict[str, Any]],
    after: list[dict[str, Any]],
    *,
    protected_count: int,
    trigger_reason: str,
) -> None:
    if sink is None:
        return
    tokens_before = _approx_tokens(before)
    tokens_after = _approx_tokens(after)
    sink({
        "compression_id": uuid.uuid4().hex,
        "timestamp": int(time.time() * 1000),
        "tokens_before": tokens_before,
        "tokens_after": tokens_after,
        "compression_ratio": round(tokens_after / max(tokens_before, 1), 4),
        "messages_before": len(before),
        "messages_after": len(after),
        "protected_count": protected_count,
        "rolling_summary_before_hash": _summary_hash(before),
        "rolling_summary_after_hash": _summary_hash(after),
        "trigger_reason": trigger_reason,
    })


def _is_summary_message(message: dict[str, Any]) -> bool:
    # Match by the marker regardless of role: the rolling summary is now emitted
    # as an ASSISTANT message (so there's exactly one system message, first, for
    # strict chat templates like Qwen), but sessions persisted before this change
    # still carry it as role="system" — both must be recognised.
    return (
        message.get("role") in {"system", "assistant"}
        and str(message.get("content") or "").startswith(_SUMMARY_PREFIX)
    )


def _summary_body(message: dict[str, Any]) -> str:
    content = str(message.get("content") or "")
    if content.startswith(_SUMMARY_PREFIX):
        return content[len(_SUMMARY_PREFIX):].strip()
    return content.strip()


def _cap_text(text: str, limit: int = _MAX_SUMMARY_CHARS) -> str:
    clean = (text or "").strip()
    if len(clean) <= limit:
        return clean
    return clean[: max(0, limit - 20)].rstrip() + "\n[summary truncated]"


def _make_summary_message(summary: str) -> dict[str, Any]:
    # ASSISTANT, not system: keeps the message list to a SINGLE leading system
    # message. A strict template (Qwen: "System message must be at the beginning")
    # 400s on a second system message — this removes the need for the server-side
    # lenient-template workaround.
    return {"role": "assistant", "content": _SUMMARY_PREFIX + _cap_text(summary)}


def extract_rolling_summary(messages: list[dict[str, Any]]) -> str:
    summaries = [_summary_body(message) for message in messages if _is_summary_message(message)]
    return "\n\n".join(summary for summary in summaries if summary).strip()


def _excerpt(value: Any, limit: int = _MESSAGE_EXCERPT_CHARS) -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 16)].rstrip() + " ...[truncated]"


_OLDER_DROPPED_MARKER = "[older summaries dropped]"


def _merge_summary(previous_summaries: list[str], new_summary: str) -> str:
    parts = [p.strip() for p in previous_summaries if p.strip()]
    if new_summary.strip():
        parts.append(new_summary.strip())
    if not parts:
        return ""
    # Evict OLDEST blocks when over budget: a rolling summary must prefer the
    # most recent work. (Previously the merged text was head-capped, so once
    # saturated the NEWEST summaries were the ones truncated away and the
    # rolling summary fossilized on the start of the session.)
    budget = _MAX_SUMMARY_CHARS - len(_OLDER_DROPPED_MARKER) - 2
    kept: list[str] = []
    total = 0
    dropped = False
    for part in reversed(parts):
        cost = len(part) + (2 if kept else 0)
        if kept and total + cost > budget:
            dropped = True
            break
        kept.append(part)
        total += cost
    kept.reverse()
    merged = "\n\n".join(kept)
    if dropped:
        merged = _OLDER_DROPPED_MARKER + "\n\n" + merged
    return _cap_text(merged)


def _deterministic_summary(previous_summaries: list[str], messages: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    if previous_summaries:
        lines.append("Previous summary:")
        lines.extend(_cap_text("\n\n".join(previous_summaries), 1_500).splitlines())

    recent_user = next((m for m in reversed(messages) if m.get("role") == "user"), None)
    if recent_user:
        lines.append(f"Current goal: {_excerpt(recent_user.get('content'))}")

    tool_lines: list[str] = []
    for message in messages:
        if message.get("role") != "tool":
            continue
        name = str(message.get("name") or "tool")
        tool_lines.append(f"- {name}: {_excerpt(message.get('content'), 220)}")
        if len(tool_lines) >= 8:
            break
    if tool_lines:
        lines.append("Recent tool results:")
        lines.extend(tool_lines)

    if not lines:
        lines.append(_FALLBACK_PLACEHOLDER)
    return _cap_text("\n".join(lines))


def maybe_compact(
    messages: list[dict[str, Any]],
    num_ctx: int,
    model: str,
    chat_fn: Callable[..., dict[str, Any]] | None,
    summarize_fn: SummarizeFn,
    *,
    threshold: float = _DEFAULT_THRESHOLD,
    keep_pairs: int = _DEFAULT_KEEP_PAIRS,
    fallback_keep: int = _DEFAULT_FALLBACK_KEEP,
    prepare_messages: Callable[[list[dict[str, Any]]], list[dict[str, Any]]] | None = None,
    pinned_message_ids: set[str] | None = None,
    audit_sink: Callable[[dict[str, Any]], None] | None = None,
    trigger_reason: str = "threshold",
) -> tuple[list[dict[str, Any]], bool]:
    """Compact *messages* if they exceed *threshold* × *num_ctx* tokens.

    Parameters
    ----------
    messages:
        Current message list (system + user/assistant turns).
    num_ctx:
        Maximum context window in tokens.
    model:
        Model name passed to summarize_fn.
    chat_fn:
        Chat callable passed to summarize_fn (may be None to use default).
    summarize_fn:
        Callable with the same signature as ``agent_loop.summarize_history``.
        Receives ``(messages, model, num_ctx, chat_fn)`` keyword args.
    threshold:
        Fraction of num_ctx at which to trigger compaction (default 0.70).
    keep_pairs:
        Number of recent user/assistant exchange pairs to preserve verbatim.
    fallback_keep:
        Non-system messages to keep in deterministic fallback.
    prepare_messages:
        Optional transform applied to the to-summarize slice before it is
        handed to ``summarize_fn``. The agent loop passes a flattener that
        converts tool-result messages and assistant tool_calls into plain
        text turns — otherwise the summarizer input loses all tool work
        (``_coerce_history`` keeps only non-empty user/assistant content).

    Returns
    -------
    (new_messages, was_compacted)
        *new_messages* is the (possibly compacted) message list.
        *was_compacted* is True if compaction occurred.
    """
    if _approx_tokens(messages) < int(num_ctx * threshold):
        return messages, False

    previous_summaries = [_summary_body(m) for m in messages if _is_summary_message(m)]
    system_msgs = [m for m in messages if m.get("role") == "system" and not _is_summary_message(m)]
    # Exclude the rolling summary (now an assistant message) from the compactable
    # pool — it is carried via previous_summaries and re-emitted fresh, never
    # re-summarized.
    non_system = [m for m in messages if m.get("role") != "system" and not _is_summary_message(m)]
    pinned_ids = {str(value) for value in (pinned_message_ids or set())}
    pinned = [
        message for message in non_system
        if str(message.get("_msg_id") or "") in pinned_ids
    ]
    compactable = [message for message in non_system if message not in pinned]

    keep_count = keep_pairs * 2  # keep_pairs pairs = keep_count messages
    recent = compactable[-keep_count:] if len(compactable) > keep_count else compactable
    to_summarize = compactable[:-keep_count] if len(compactable) > keep_count else []

    if not to_summarize:
        # Nothing old enough to summarize; keep any rolling summary as one
        # capped message and truncate recent turns deterministically.
        summary = _merge_summary(previous_summaries, "")
        summary_msgs = [_make_summary_message(summary)] if summary else []
        result_messages = system_msgs + summary_msgs + pinned + compactable[-fallback_keep:]
        _emit_audit(
            audit_sink, messages, result_messages,
            protected_count=len(pinned), trigger_reason=trigger_reason,
        )
        return result_messages, True

    try:
        summarize_messages = list(to_summarize)
        if prepare_messages is not None:
            summarize_messages = prepare_messages(summarize_messages)
        if previous_summaries:
            summarize_messages = [{
                "role": "system",
                "content": "Previous compacted summary:\n" + "\n\n".join(previous_summaries),
            }] + summarize_messages
        result = summarize_fn(
            messages=summarize_messages,
            model=model,
            num_ctx=num_ctx,
            chat_fn=chat_fn,
        )
    except Exception as exc:
        logger.warning("Context compaction summarize failed: %s — using fallback", exc)
        result = {"ok": False, "summary": "", "error": str(exc)}

    if result.get("ok") and result.get("summary"):
        summary_msg = _make_summary_message(_merge_summary(previous_summaries, str(result["summary"])))
        logger.debug(
            "Context compacted: %d → %d messages (summarized %d, kept %d)",
            len(messages),
            len(system_msgs) + 1 + len(recent),
            len(to_summarize),
            len(recent),
        )
        result_messages = system_msgs + [summary_msg] + pinned + recent
        _emit_audit(
            audit_sink, messages, result_messages,
            protected_count=len(pinned), trigger_reason=trigger_reason,
        )
        return result_messages, True

    # Deterministic fallback: one structured summary + recent messages.
    logger.warning(
        "Context compaction model call failed (%s); using deterministic fallback",
        result.get("error", "unknown"),
    )
    fallback_msg = _make_summary_message(_deterministic_summary(previous_summaries, to_summarize))
    result_messages = system_msgs + [fallback_msg] + pinned + compactable[-fallback_keep:]
    _emit_audit(
        audit_sink, messages, result_messages,
        protected_count=len(pinned), trigger_reason=trigger_reason,
    )
    return result_messages, True
