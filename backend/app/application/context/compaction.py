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

SummarizeFn = Callable[..., dict[str, Any]]


def _approx_tokens(messages: list[dict[str, Any]]) -> int:
    """Rough token estimate: chars / 4."""
    return len(json.dumps(messages, ensure_ascii=False)) // 4


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

    Returns
    -------
    (new_messages, was_compacted)
        *new_messages* is the (possibly compacted) message list.
        *was_compacted* is True if compaction occurred.
    """
    if _approx_tokens(messages) < int(num_ctx * threshold):
        return messages, False

    system_msgs = [m for m in messages if m.get("role") == "system"]
    non_system = [m for m in messages if m.get("role") != "system"]

    keep_count = keep_pairs * 2  # keep_pairs pairs = keep_count messages
    recent = non_system[-keep_count:] if len(non_system) > keep_count else non_system
    to_summarize = non_system[:-keep_count] if len(non_system) > keep_count else []

    if not to_summarize:
        # Nothing old enough to summarize; fall back to simple truncation.
        return system_msgs + non_system[-fallback_keep:], True

    try:
        result = summarize_fn(
            messages=to_summarize,
            model=model,
            num_ctx=num_ctx,
            chat_fn=chat_fn,
        )
    except Exception as exc:
        logger.warning("Context compaction summarize failed: %s — using fallback", exc)
        result = {"ok": False, "summary": "", "error": str(exc)}

    if result.get("ok") and result.get("summary"):
        summary_msg: dict[str, Any] = {
            "role": "system",
            "content": _SUMMARY_PREFIX + result["summary"],
        }
        logger.debug(
            "Context compacted: %d → %d messages (summarized %d, kept %d)",
            len(messages),
            len(system_msgs) + 1 + len(recent),
            len(to_summarize),
            len(recent),
        )
        return system_msgs + [summary_msg] + recent, True

    # Deterministic fallback: placeholder + recent
    logger.warning(
        "Context compaction model call failed (%s); using deterministic fallback",
        result.get("error", "unknown"),
    )
    fallback_msg: dict[str, Any] = {"role": "system", "content": _FALLBACK_PLACEHOLDER}
    return system_msgs + [fallback_msg] + non_system[-fallback_keep:], True
