from __future__ import annotations

import json
from typing import Any


CONTEXT_CATEGORIES = (
    "system",
    "chat",
    "user_input",
    "rolling_summary",
    "task_ledger",
    "tools",
    "rag",
    "ocr",
    "vision",
    "code",
    "documents",
    "artifacts",
)


def estimate_tokens(value: Any) -> int:
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return max(0, (len(text) + 3) // 4)


def count_tokens(value: Any) -> int:
    """Compatibility name used by the context packer."""
    return estimate_tokens(value)


def calculate_budget(
    *,
    ctx_size: int,
    reserved_output_tokens: int = 4096,
    reserved_system_tokens: int = 4096,
    safety_margin_tokens: int = 2048,
) -> dict[str, int]:
    safe_ctx = max(1, int(ctx_size))
    output = max(0, int(reserved_output_tokens))
    system = max(0, int(reserved_system_tokens))
    margin = max(0, int(safety_margin_tokens))
    return {
        "ctx_size": safe_ctx,
        "reserved_output_tokens": output,
        "reserved_system_tokens": system,
        "safety_margin_tokens": margin,
        "available_input_tokens": max(0, safe_ctx - output - system - margin),
    }


def get_context_usage(
    messages: list[dict[str, Any]],
    *,
    ctx_size: int,
    reserved_output_tokens: int = 4096,
    reserved_system_tokens: int = 4096,
    safety_margin_tokens: int = 2048,
    extra_categories: dict[str, Any] | None = None,
) -> dict[str, Any]:
    extended = bool(extra_categories) or any(
        str(message.get("context_category") or "").strip()
        for message in messages
    )
    categories = CONTEXT_CATEGORIES if extended else (
        "system", "chat", "rolling_summary", "tools",
    )
    breakdown = {category: 0 for category in categories}
    last_user_index = max(
        (index for index, message in enumerate(messages) if message.get("role") == "user"),
        default=-1,
    )
    for index, message in enumerate(messages):
        tokens = estimate_tokens(message)
        role = str(message.get("role") or "")
        content = str(message.get("content") or "")
        explicit = str(message.get("context_category") or "").strip()
        if explicit in breakdown:
            breakdown[explicit] += tokens
        elif "Compacted context summary" in content or "CONTEXT SUMMARY" in content:
            breakdown["rolling_summary"] += tokens
        elif role == "system":
            breakdown["system"] += tokens
        elif role == "tool" or message.get("tool_calls"):
            breakdown["tools"] += tokens
        elif extended and role == "user" and index == last_user_index:
            breakdown["user_input"] += tokens
        else:
            breakdown["chat"] += tokens

    for category, value in (extra_categories or {}).items():
        if category in breakdown:
            breakdown[category] += estimate_tokens(value)

    prompt_tokens = sum(breakdown.values())
    budget = calculate_budget(
        ctx_size=ctx_size,
        reserved_output_tokens=reserved_output_tokens,
        reserved_system_tokens=reserved_system_tokens,
        safety_margin_tokens=safety_margin_tokens,
    )
    total_tokens = (
        prompt_tokens
        + budget["reserved_output_tokens"]
        + budget["reserved_system_tokens"]
        + budget["safety_margin_tokens"]
    )
    safe_ctx = budget["ctx_size"]
    percent = min(100.0, round(total_tokens * 100 / safe_ctx, 1))
    return {
        "current_tokens": prompt_tokens,
        "reserved_output_tokens": budget["reserved_output_tokens"],
        "reserved_system_tokens": budget["reserved_system_tokens"],
        "safety_margin_tokens": budget["safety_margin_tokens"],
        "ctx_size": safe_ctx,
        "percent": percent,
        "free_tokens": max(0, safe_ctx - total_tokens),
        "breakdown": breakdown,
    }


def check_context_limit(usage: dict[str, Any]) -> dict[str, Any]:
    percent = max(0.0, min(100.0, float(usage.get("percent") or 0.0)))
    if percent >= 95.0:
        status, allowed = "critical", False
    elif percent >= 90.0:
        status, allowed = "strong_compression", True
    elif percent >= 85.0:
        status, allowed = "auto_compression", True
    elif percent >= 75.0:
        status, allowed = "prepare", True
    elif percent >= 60.0:
        status, allowed = "monitor", True
    else:
        status, allowed = "normal", True
    return {"status": status, "allowed": allowed, "percent": percent}
