from __future__ import annotations

from typing import Any, Callable

from app.application.context.compaction import maybe_compact
from app.application.context.usage import check_context_limit, get_context_usage


def should_compress(percent_or_usage: float | dict[str, Any]) -> bool:
    percent = (
        float(percent_or_usage.get("percent") or 0.0)
        if isinstance(percent_or_usage, dict)
        else float(percent_or_usage)
    )
    return percent >= 85.0


def prepare_compression(usage: dict[str, Any]) -> dict[str, Any]:
    decision = check_context_limit(usage)
    percent = float(decision["percent"])
    return {
        **decision,
        "prepared": percent >= 75.0,
        "keep_pairs": 2 if percent >= 90.0 else 4,
        "requires_confirmation": percent >= 95.0,
    }


def compress_history(
    messages: list[dict[str, Any]],
    *,
    num_ctx: int,
    model: str,
    chat_fn: Callable[..., dict[str, Any]] | None,
    summarize_fn: Callable[..., dict[str, Any]],
    usage: dict[str, Any] | None = None,
    pinned_message_ids: set[str] | None = None,
    audit_sink: Callable[[dict[str, Any]], None] | None = None,
    prepare_messages: Callable[[list[dict[str, Any]]], list[dict[str, Any]]] | None = None,
    manual: bool = False,
) -> tuple[list[dict[str, Any]], bool]:
    effective_usage = usage or get_context_usage(messages, ctx_size=num_ctx)
    decision = prepare_compression(effective_usage)
    if not manual and not should_compress(effective_usage):
        return messages, False
    return maybe_compact(
        messages,
        num_ctx,
        model,
        chat_fn,
        summarize_fn,
        threshold=0.0,
        keep_pairs=int(decision["keep_pairs"]),
        fallback_keep=int(decision["keep_pairs"]) * 2,
        prepare_messages=prepare_messages,
        pinned_message_ids=pinned_message_ids,
        audit_sink=audit_sink,
        trigger_reason="manual" if manual else str(decision["status"]),
    )


def apply_compression(
    original_messages: list[dict[str, Any]],
    compressed_messages: list[dict[str, Any]],
    *,
    ctx_size: int,
) -> dict[str, Any]:
    usage = get_context_usage(compressed_messages, ctx_size=ctx_size)
    decision = check_context_limit(usage)
    if not decision["allowed"]:
        return {
            "applied": False,
            "messages": original_messages,
            "usage": usage,
            "reason": "compressed context still exceeds the critical threshold",
        }
    return {"applied": True, "messages": compressed_messages, "usage": usage, "reason": ""}
